"""Controlled Task authority checks for external targets and source manifests.

This module is a small, side-effect-free bridge around the project's existing
Ed25519/canonical JSON conventions.  It verifies a *registered* caller and
its authorization; it does not establish a real-world identity, provenance,
licence, or factual correctness.  Source manifests can therefore affect E
(asset metadata integrity) only.  They never prove C (content correctness).

Private keys used by ``FixtureKeyring`` are held in memory for tests only and
are never serialized by this module.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization


FORMAT = "task-authority-v1"
TARGET_KIND = "TARGET_AUTHORIZATION"
MANIFEST_KIND = "SOURCE_MANIFEST_AUTHORIZATION"
ROLES = {"target_authority", "source_authority"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _without(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in keys}


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("TIME_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _profile_hash(profile: Mapping[str, Any]) -> str:
    return digest(_without(profile, "task_profile_sha256"))


def _pub(raw: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(raw, validate=True))


def _verify_envelope(envelope: Mapping[str, Any], registry: Mapping[str, Any],
                     *, expected_role: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(envelope, Mapping) or set(envelope) != {"signer_id", "payload", "signature"}:
        return None, "INVALID_ENVELOPE"
    signer = envelope.get("signer_id")
    payload = envelope.get("payload")
    if not isinstance(signer, str) or not isinstance(payload, Mapping):
        return None, "INVALID_ENVELOPE_FIELDS"
    if not isinstance(registry, Mapping) or registry.get("format") != FORMAT:
        return None, "INVALID_REGISTRY_FORMAT"
    identities = registry.get("identities")
    entry = identities.get(signer) if isinstance(identities, Mapping) else None
    if not isinstance(entry, Mapping):
        return None, "UNKNOWN_SIGNER"
    if entry.get("role") != expected_role:
        return None, "WRONG_SIGNER_ROLE"
    if entry.get("status") != "ACTIVE":
        return None, "SIGNER_REVOKED"
    try:
        key = _pub(entry["public_key"])
        key.verify(base64.b64decode(envelope["signature"], validate=True),
                   canonical_bytes({"signer_id": signer, "payload": dict(payload)}))
    except (KeyError, ValueError, TypeError, InvalidSignature, UnicodeError, RecursionError):
        return None, "INVALID_SIGNATURE"
    return dict(payload), None


def _base_result(kind: str) -> dict[str, Any]:
    return {"format": FORMAT, "kind": kind, "decision": None,
            "identity": None, "permission": None, "integrity": None,
            "validity": None, "reasons": []}


def _version_permission(entry: Mapping[str, Any], *, object_id: str,
                        version: str, prefix: str, now: str) -> tuple[bool, str | None]:
    """Require an explicit allowlist for both identity and object version.

    Optional revocations are object/version pairs, independent of the signer
    status; a revoked version is rejected even when it is still allowlisted.
    Registry configuration is a trust input, not authenticated here.
    """
    ids = entry.get(f"authorized_{prefix}_ids")
    versions = entry.get(f"authorized_{prefix}_versions")
    if not isinstance(ids, list) or object_id not in ids or not isinstance(versions, Mapping):
        return False, f"{prefix.upper()}_NOT_AUTHORIZED"
    allowed = versions.get(object_id)
    if not isinstance(allowed, list) or version not in allowed:
        return False, f"{prefix.upper()}_VERSION_NOT_AUTHORIZED"
    revoked = entry.get(f"revoked_{prefix}_versions", {})
    if not isinstance(revoked, Mapping):
        return False, "INVALID_REVOCATION_LIST"
    revoked_versions = revoked.get(object_id, [])
    if not isinstance(revoked_versions, list):
        return False, "INVALID_REVOCATION_LIST"
    if version in revoked_versions:
        return False, f"{prefix.upper()}_VERSION_REVOKED"
    if entry.get("revoked_at"):
        try:
            if _parse_time(entry["revoked_at"]) <= _parse_time(now):
                return False, "CALLER_REVOKED"
        except (ValueError, TypeError):
            return False, "INVALID_REVOCATION_TIME"
    return True, None


def verify_target(profile: Mapping[str, Any], envelope: Mapping[str, Any],
                  registry: Mapping[str, Any], now: str) -> dict[str, Any]:
    """Verify target content, registered caller identity, and target permission.

    ``None`` means the available registry/evidence cannot determine the field;
    ``False`` means a supplied assertion was contradicted.  The returned
    ``decision`` is only a controlled-registry authorization result.
    """
    result = _base_result(TARGET_KIND)
    reasons: list[str] = result["reasons"]
    if not isinstance(profile, Mapping):
        reasons.append("INVALID_PROFILE")
        result["integrity"] = False
        result["decision"] = 0
        return result
    if any(not isinstance(profile.get(k), str) or not profile[k]
           for k in ("task_id", "task_version", "task_profile_sha256")):
        result["integrity"] = False
        result["decision"] = 0
        reasons.append("INVALID_PROFILE_FIELDS")
        return result
    claimed_hash = profile["task_profile_sha256"]
    try:
        result["integrity"] = claimed_hash == _profile_hash(profile)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        result["integrity"] = False
    if not result["integrity"]:
        reasons.append("PROFILE_HASH_MISMATCH")
    payload, error = _verify_envelope(envelope, registry, expected_role="target_authority")
    if error:
        reasons.append(error)
        result["identity"] = False if error != "UNKNOWN_SIGNER" else None
        result["permission"] = False
        result["validity"] = False if error not in {"UNKNOWN_SIGNER"} else None
        result["decision"] = 0 if error != "UNKNOWN_SIGNER" else None
        return result
    signer = envelope["signer_id"]
    result["identity"] = True
    if payload.get("kind") != TARGET_KIND:
        reasons.append("WRONG_AUTHORIZATION_KIND")
    fields = ("caller_id", "task_id", "task_version", "profile_sha256", "issued_at", "expires_at")
    if any(not isinstance(payload.get(k), str) or not payload[k] for k in fields):
        reasons.append("MISSING_AUTHORIZATION_FIELDS")
    if payload.get("profile_sha256") != claimed_hash:
        reasons.append("PROFILE_BINDING_MISMATCH")
    if payload.get("task_id") != profile.get("task_id") or payload.get("task_version") != profile.get("task_version"):
        reasons.append("TARGET_VERSION_MISMATCH")
    if payload.get("caller_id") != signer:
        reasons.append("CALLER_SIGNER_MISMATCH")
    try:
        at, expires = _parse_time(now), _parse_time(payload["expires_at"])
        issued = _parse_time(payload["issued_at"])
        result["validity"] = bool(issued <= at < expires)
        if not result["validity"]:
            reasons.append("AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID")
    except (KeyError, TypeError, ValueError):
        result["validity"] = False
        reasons.append("INVALID_AUTHORIZATION_TIME")
    entry = registry["identities"][signer]
    result["permission"], why = _version_permission(entry, object_id=profile["task_id"],
                                                     version=profile["task_version"],
                                                     prefix="task", now=now)
    if why:
        reasons.append(why)
    pins = entry.get("authorized_task_profile_hashes")
    if pins is not None:
        version_pins = pins.get(profile["task_id"], {}) if isinstance(pins, Mapping) else {}
        if not isinstance(version_pins, Mapping) or version_pins.get(profile["task_version"]) != claimed_hash:
            result["permission"] = False
            reasons.append("PROFILE_HASH_NOT_PINNED")
    result["decision"] = int(bool(result["integrity"] and result["identity"] and result["permission"] and result["validity"] and not reasons))
    return result


def _member_hash(record: Any) -> str:
    return digest(record)


def verify_manifest(manifest: Mapping[str, Any], envelope: Mapping[str, Any],
                    registry: Mapping[str, Any], records: Iterable[Mapping[str, Any]],
                    now: str) -> dict[str, Any]:
    """Verify a source/version/member manifest as metadata evidence only."""
    result = _base_result(MANIFEST_KIND)
    result["affects"] = "E_ONLY"
    reasons: list[str] = result["reasons"]
    if (not isinstance(manifest, Mapping) or not isinstance(manifest.get("members"), list)
            or any(not isinstance(manifest.get(k), str) or not manifest[k]
                   for k in ("source_id", "asset_version", "manifest_sha256"))):
        result["integrity"] = False
        reasons.append("INVALID_MANIFEST")
        result["decision"] = 0
        return result
    claimed = manifest.get("manifest_sha256")
    try:
        result["integrity"] = claimed == digest(_without(manifest, "manifest_sha256"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        result["integrity"] = False
    if not result["integrity"]:
        reasons.append("MANIFEST_HASH_MISMATCH")
    payload, error = _verify_envelope(envelope, registry, expected_role="source_authority")
    if error:
        reasons.append(error)
        result["identity"] = False if error != "UNKNOWN_SIGNER" else None
        result["permission"] = False
        result["validity"] = False if error != "UNKNOWN_SIGNER" else None
        result["decision"] = 0 if error != "UNKNOWN_SIGNER" else None
        return result
    result["identity"] = True
    if payload.get("kind") != MANIFEST_KIND:
        reasons.append("WRONG_AUTHORIZATION_KIND")
    if payload.get("caller_id") != envelope["signer_id"]:
        reasons.append("CALLER_SIGNER_MISMATCH")
    if payload.get("source_id") != manifest.get("source_id") or payload.get("asset_version") != manifest.get("asset_version"):
        reasons.append("SOURCE_VERSION_MISMATCH")
    if payload.get("manifest_sha256") != claimed:
        reasons.append("MANIFEST_BINDING_MISMATCH")
    try:
        result["validity"] = _parse_time(payload["issued_at"]) <= _parse_time(now) < _parse_time(payload["expires_at"])
    except (KeyError, TypeError, ValueError):
        result["validity"] = False
        reasons.append("INVALID_AUTHORIZATION_TIME")
    if result["validity"] is False:
        reasons.append("AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID")
    identities = registry.get("identities", {})
    entry = identities.get(envelope["signer_id"], {})
    result["permission"], why = _version_permission(entry, object_id=manifest["source_id"],
                                                     version=manifest["asset_version"],
                                                     prefix="source", now=now)
    if why:
        reasons.append(why)
    try:
        rows = list(records)
        members = manifest["members"]
        if not rows or any(not isinstance(r, Mapping) or not isinstance(r.get("sample_id"), str)
                           or not r["sample_id"] for r in rows):
            raise ValueError("invalid records")
        if any(not isinstance(m, Mapping) or not isinstance(m.get("sample_id"), str)
               or not isinstance(m.get("member_sha256"), str) for m in members):
            raise ValueError("invalid members")
        by_id = {r["sample_id"]: r for r in rows}
        supplied = {m["sample_id"]: m["member_sha256"] for m in members}
        if len(by_id) != len(rows) or len(supplied) != len(members):
            raise ValueError("duplicate sample id")
        expected = {sid: _member_hash(rec) for sid, rec in by_id.items()}
        # A correctly signed list of record hashes does not establish that
        # those records declare the same source or asset version as the list.
        version_matches = all(r.get("source_id") == manifest["source_id"]
                              and r.get("asset_version") == manifest["asset_version"]
                              and ("source_version" not in manifest
                                   or r.get("source_version") == manifest["source_version"])
                              for r in rows)
        member_matches = (set(supplied) == set(expected)
                          and all(supplied[k] == v for k, v in expected.items())
                          and version_matches)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        member_matches = False
    if not member_matches:
        reasons.append("MEMBER_MANIFEST_MISMATCH")
        result["integrity"] = False
    result["decision"] = int(bool(result["integrity"] and result["identity"] and result["permission"] and result["validity"] and not reasons))
    return result


class FixtureKeyring:
    """In-memory public fixture signer for tests and controlled Task runs."""
    def __init__(self, seed: str = "PUBLIC_FIXTURE_SEED", identities: Mapping[str, Mapping[str, Any]] | None = None):
        self._private: dict[str, Ed25519PrivateKey] = {}
        entries = identities or {"authority-target": {"role": "target_authority", "authorized_task_ids": []},
                                 "authority-source": {"role": "source_authority", "authorized_source_ids": []}}
        registry = {"format": FORMAT, "identities": {}}
        for identity, raw in entries.items():
            role = raw["role"]
            if role not in ROLES:
                raise ValueError("INVALID_FIXTURE_ROLE")
            material = hashlib.sha256(canonical_bytes(["PUBLIC_FIXTURE", seed, identity, role])).digest()
            key = Ed25519PrivateKey.from_private_bytes(material)
            self._private[identity] = key
            registry["identities"][identity] = {"role": role,
                "public_key": base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode(),
                **{k: deepcopy(v) for k, v in raw.items() if k != "role"}, "status": "ACTIVE"}
        self.registry = registry

    def sign(self, signer_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = {"signer_id": signer_id, "payload": dict(payload)}
        return {**body, "signature": base64.b64encode(self._private[signer_id].sign(canonical_bytes(body))).decode()}

