"""Local Ed25519 identities: one signing key per service, public trust only.

This separates signing capabilities between processes.  It is not an OS sandbox,
enterprise identity provider, CA, or protection against the same local user.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ..credit.evidence import canonical_bytes, canonical_hash, parse_time as _parse_time

ROLES = {"registry": "registry", "provider_a": "provider", "provider_b": "provider",
         "consumer": "consumer", "verifier": "verifier"}
_default: Keyring | None = None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("TIME_MUST_BE_TIMEZONE_AWARE_ISO_STRING")
    return _parse_time(value)


def byte_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Keyring:
    """A single actor's signing capability plus all trusted public keys."""

    def __init__(self, actor: str, private_key: Ed25519PrivateKey, trust: dict[str, dict]):
        if actor not in ROLES or not isinstance(trust, dict) or set(trust) != set(ROLES):
            raise ValueError("INVALID_LOCAL_TRUST_REGISTRY")
        self.actor = actor
        self._private_key = private_key
        self._trust: dict[str, dict] = {}
        self._public: dict[str, Ed25519PublicKey] = {}
        for identity, entry in trust.items():
            if entry.get("role") != ROLES[identity]:
                raise ValueError("TRUST_ROLE_MISMATCH")
            try:
                raw = base64.b64decode(entry["public_key"], validate=True)
                self._public[identity] = Ed25519PublicKey.from_public_bytes(raw)
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("INVALID_TRUST_PUBLIC_KEY") from exc
            self._trust[identity] = {"role": entry["role"], "public_key": entry["public_key"]}
        own_public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if base64.b64decode(self._trust[actor]["public_key"]) != own_public:
            raise ValueError("PRIVATE_KEY_DOES_NOT_MATCH_TRUSTED_IDENTITY")

    @classmethod
    def from_files(cls, private_key_path: Path | str, trust_path: Path | str,
                   expected_actor: str | None = None) -> Keyring:
        private_record = json.loads(Path(private_key_path).read_text(encoding="utf-8"))
        if expected_actor is not None and private_record["actor"] != expected_actor:
            raise ValueError("WRONG_SERVICE_PRIVATE_IDENTITY")
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_record["private_key"], validate=True))
        trust = json.loads(Path(trust_path).read_text(encoding="utf-8"))
        return cls(private_record["actor"], key, trust["identities"])

    def sign(self, actor: str, payload: dict) -> dict:
        if actor != self.actor:
            raise ValueError("SIGNING_ANOTHER_IDENTITY_IS_FORBIDDEN")
        if not isinstance(payload, dict):
            raise ValueError("PAYLOAD_MUST_BE_OBJECT")
        body = {"signer_id": actor, "payload": payload}
        return {**body, "signature": base64.b64encode(self._private_key.sign(canonical_bytes(body))).decode()}

    def verify(self, envelope: dict, *, expected_actor: str | None = None,
               expected_role: str | None = None) -> dict:
        if not isinstance(envelope, dict) or set(envelope) != {"signer_id", "payload", "signature"}:
            raise ValueError("INVALID_SIGNED_ENVELOPE")
        actor = envelope["signer_id"]
        if (not isinstance(actor, str) or actor not in self._public
                or expected_actor is not None and actor != expected_actor
                or expected_role is not None and self._trust[actor]["role"] != expected_role):
            raise ValueError("UNTRUSTED_SIGNER_OR_ROLE")
        if not isinstance(envelope["payload"], dict):
            raise ValueError("PAYLOAD_MUST_BE_OBJECT")
        try:
            self._public[actor].verify(base64.b64decode(envelope["signature"], validate=True),
                canonical_bytes({"signer_id": actor, "payload": envelope["payload"]}))
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise ValueError("INVALID_SIGNATURE") from exc
        return envelope["payload"]

    def public_registry(self) -> dict:
        return {k: dict(v) for k, v in self._trust.items()}


def generate_identities(directory: Path | str) -> dict[str, Any]:
    """Exclusive initialization.  Outputs contain secrets and must not be packed."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    records, trust = {}, {}
    for actor, role in ROLES.items():
        key = Ed25519PrivateKey.generate()
        folder = directory / actor
        folder.mkdir()
        secret = folder / "private_key.json"
        payload = {"actor": actor, "algorithm": "Ed25519", "private_key": base64.b64encode(key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode()}
        with secret.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        try:
            secret.chmod(0o600)
        except OSError:
            pass  # Windows ACL isolation is explicitly outside this local demo.
        trust[actor] = {"role": role, "public_key": base64.b64encode(key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()}
        records[actor] = {"private_key_file": str(secret), "trust_file": str(directory / "trust.json")}
    (directory / "trust.json").write_text(json.dumps({"version": 1, "identities": trust}, indent=2), encoding="utf-8")
    return {"trust_file": str(directory / "trust.json"), "identities": records}


def keyring_from_config(config: dict, base: Path, expected_actor: str) -> Keyring:
    identity = config["identity"]
    return Keyring.from_files(base / identity["private_key_file"], base / identity["trust_file"], expected_actor)


def configure_identity(private_key_path: Path | str, trust_path: Path | str,
                       expected_actor: str | None = None) -> Keyring:
    global _default
    _default = Keyring.from_files(private_key_path, trust_path, expected_actor)
    return _default


def _identity() -> Keyring:
    if _default is None:
        raise ValueError("SERVICE_IDENTITY_NOT_CONFIGURED")
    return _default


def sign(actor: str, payload: dict) -> dict:
    return _identity().sign(actor, payload)


def verify(envelope: dict, *, expected_actor: str | None = None, expected_role: str | None = None) -> dict:
    return _identity().verify(envelope, expected_actor=expected_actor, expected_role=expected_role)


def public_registry() -> dict:
    return _identity().public_registry()


def policy_allows(metadata: dict, actor: str, purpose: str, at: str | None = None) -> bool:
    policy = metadata["usage_policy"]
    return (actor in policy.get("allowed_consumers", []) and purpose in policy.get("allowed_purposes", [])
            and parse_time(at or utcnow()) < parse_time(policy["expires_at"]))
