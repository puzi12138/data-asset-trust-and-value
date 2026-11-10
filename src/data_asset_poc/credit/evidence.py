"""Signed, contract-level SIMULATED delivery evidence.

Ed25519 authenticates a locally registered signer, not factual truth. The local
observer allowlist is a research trust assumption. A JSONL hash chain detects
rewrites only relative to an independently protected anchor. Demo deterministic
keys are deliberately reproducible, PUBLIC test material, never production keys.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

GENESIS = "0" * 64
EVENT_TYPES = frozenset({"DELIVERY", "SLA_FAILURE", "CHECK", "UNKNOWN", "DISPUTE", "RESOLUTION"})
CONTRACT_FIELDS = frozenset({"contract_id", "provider_id", "asset_id", "asset_version",
    "expected_content_hash", "expected_schema_hash", "agreed_deadline", "usage_policy_id", "created_at", "signer_id"})
EVENT_FIELDS = frozenset({"event_id", "contract_id", "event_type", "event_time", "observed_at",
    "observer_id", "actual_content_hash", "verification_result", "previous_event_hash"})


def canonical_bytes(value: Any) -> bytes:
    """The project canonical form: UTF-8, sorted keys, no whitespace/NaN.

    This restricted JSON protocol is not a claim of RFC 8785 compatibility.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Evidence timestamps must include a UTC offset")
    return dt.astimezone(timezone.utc)


def _without(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in keys}


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class DemoIdentities:
    """Separate provider and observer Ed25519 identities; no secret is persisted.

    An omitted seed generates random temporary keys. A seed derives reproducible
    TEST-only 32-byte Ed25519 seeds using SHA-256; Ed25519 itself is cryptography.
    Exported registry contains public keys only. Importing a registry does not
    establish real-world identity or authorize an observer.
    """
    def __init__(self, seed: int | None = None, provider_ids: Iterable[str] = (),
                 observer_ids: Iterable[str] = ()) -> None:
        self.seed = seed
        self._private: dict[str, Ed25519PrivateKey] = {}
        self._public: dict[str, Ed25519PublicKey] = {}
        self.roles: dict[str, str] = {}
        for identity_id in provider_ids:
            self.add(identity_id, "provider")
        for identity_id in observer_ids:
            self.add(identity_id, "observer")

    def add(self, identity_id: str, role: str) -> None:
        if role not in {"provider", "observer"} or not identity_id:
            raise ValueError("Identity requires a non-empty ID and provider/observer role")
        if identity_id in self.roles:
            if self.roles[identity_id] != role:
                raise ValueError("Provider and observer identities must be separate")
            return
        if self.seed is None:
            private = Ed25519PrivateKey.generate()
        else:
            material = canonical_bytes(["PUBLIC-INSECURE-SIMULATION-DEMO-KEY", self.seed, role, identity_id])
            private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(material).digest())
        self._private[identity_id] = private
        self._public[identity_id] = private.public_key()
        self.roles[identity_id] = role

    def public_registry(self) -> dict[str, Any]:
        return {"format": "simulation-ed25519-public-registry-v1", "identities": {
            identity_id: {"role": self.roles[identity_id], "public_key": base64.b64encode(
                key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")}
            for identity_id, key in sorted(self._public.items())}}

    @classmethod
    def from_public_registry(cls, registry: Mapping[str, Any]) -> DemoIdentities:
        obj = cls()
        if registry.get("format") != "simulation-ed25519-public-registry-v1":
            raise ValueError("Unknown public-key registry format")
        for identity_id, entry in registry["identities"].items():
            if entry["role"] not in {"provider", "observer"}:
                raise ValueError("Invalid identity role")
            obj.roles[identity_id] = entry["role"]
            obj._public[identity_id] = Ed25519PublicKey.from_public_bytes(base64.b64decode(entry["public_key"], validate=True))
        return obj

    def _sign(self, identity_id: str, value: Mapping[str, Any]) -> str:
        if identity_id not in self._private:
            raise ValueError("No private demo key for signer")
        return base64.b64encode(self._private[identity_id].sign(canonical_bytes(value))).decode("ascii")

    def verify(self, identity_id: str, value: Mapping[str, Any], signature: str) -> bool:
        try:
            self._public[identity_id].verify(base64.b64decode(signature, validate=True), canonical_bytes(value))
            return True
        except (KeyError, InvalidSignature, ValueError, TypeError):
            return False

    def sign_commitment(self, commitment: Mapping[str, Any]) -> dict[str, Any]:
        result = _without(deepcopy(commitment), "signature")
        result.setdefault("signer_id", result.get("provider_id"))
        result["signature"] = self._sign(result["signer_id"], result)
        return result

    def sign_event(self, event: Mapping[str, Any], previous_event_hash: str = GENESIS) -> dict[str, Any]:
        result = _without(deepcopy(event), "signature", "evidence_hash")
        result["previous_event_hash"] = previous_event_hash
        result.setdefault("actual_content_hash", None)
        result.setdefault("verification_result", None)
        result["evidence_hash"] = canonical_hash(result)
        result["signature"] = self._sign(result["observer_id"], result)
        return result


def make_anchor(events: Sequence[Mapping[str, Any]], commitments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Capture and protect OUTSIDE the log; a colocated copy alone is not secure."""
    return {"head_hash": canonical_hash(events[-1]) if events else GENESIS,
            "event_count": len(events), "commitments_hash": canonical_hash(list(commitments))}


def write_jsonl(path: str | Path, events: Iterable[Mapping[str, Any]]) -> None:
    """Append complete signed events, refusing a broken link at the append point."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = GENESIS
    if target.exists():
        existing = read_jsonl(target)
        previous = canonical_hash(existing[-1]) if existing else GENESIS
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        for event in events:
            if event.get("previous_event_hash") != previous:
                raise ValueError("Append refused: previous_event_hash does not match current log head")
            stream.write(canonical_bytes(event).decode("utf-8") + "\n")
            stream.flush()
            previous = canonical_hash(event)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


class EvidenceLedger:
    """Verify signatures ONCE, then reduce already validated history as of time.

    A broken chain/anchor fails closed for the whole presented log. Invalid
    signatures in an otherwise intact append sequence quarantine that event.
    Authoritative deadline failure is sticky: late completion cannot erase it.
    A dispute suspends statistical counting until a supported resolution.
    """
    def __init__(self, commitments: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
                 identities: DemoIdentities, trusted_observers: Iterable[str],
                 anchor: Mapping[str, Any] | None = None) -> None:
        self.commitments = deepcopy(list(commitments))
        self.events = deepcopy(list(events))
        self.identities = identities
        self.trusted_observers = frozenset(trusted_observers)
        self.anchor = make_anchor(self.events, self.commitments)
        self.commitment_validations: list[dict[str, Any]] = []
        self.validations: list[dict[str, Any]] = []
        self._contracts: dict[str, dict[str, Any]] = {}
        self._verified: list[dict[str, Any]] = []
        chain_errors: list[str] = []
        previous, previous_observed = GENESIS, None
        for event in self.events:
            if event.get("previous_event_hash") != previous:
                chain_errors.append("broken_chain")
            previous = canonical_hash(event)
            try:
                observed = parse_time(event["observed_at"])
                if previous_observed is not None and observed < previous_observed:
                    chain_errors.append("nonmonotone_observation_order")
                previous_observed = observed
            except (KeyError, ValueError, TypeError):
                chain_errors.append("invalid_observation_time")
        if anchor is not None and dict(anchor) != self.anchor:
            chain_errors.append("anchor_mismatch")
        self.chain_status = {"valid": not chain_errors, "anchored": anchor is not None,
                             "errors": sorted(set(chain_errors))}
        duplicate_contract_ids: set[str] = set()
        for commitment in self.commitments:
            cid = commitment.get("contract_id")
            errors: list[str] = []
            if not CONTRACT_FIELDS.issubset(commitment):
                errors.append("missing_commitment_fields")
            if cid in self._contracts:
                duplicate_contract_ids.add(cid)
                errors.append("duplicate_contract_id")
            signer = commitment.get("signer_id")
            if signer != commitment.get("provider_id") or identities.roles.get(signer) != "provider":
                errors.append("unauthorized_provider")
            if not identities.verify(signer, _without(commitment, "signature"), commitment.get("signature", "")):
                errors.append("invalid_commitment_signature")
            try:
                if parse_time(commitment["created_at"]) >= parse_time(commitment["agreed_deadline"]):
                    errors.append("invalid_commitment_times")
            except (KeyError, ValueError, TypeError):
                errors.append("invalid_commitment_times")
            if not all(_is_hash(commitment.get(k)) for k in ("expected_content_hash", "expected_schema_hash")):
                errors.append("invalid_expected_hash")
            self.commitment_validations.append({"contract_id": cid, "valid": not errors, "reasons": errors})
            if not errors:
                self._contracts[cid] = commitment
        for cid in duplicate_contract_ids:
            self._contracts.pop(cid, None)
        seen_ids: set[str] = set()
        for index, event in enumerate(self.events):
            errors = []
            eid, cid = event.get("event_id"), event.get("contract_id")
            if not EVENT_FIELDS.issubset(event):
                errors.append("missing_event_fields")
            if not isinstance(eid, str) or not eid:
                errors.append("invalid_event_id")
            if eid in seen_ids:
                errors.append("duplicate_event_id")
            if cid not in self._contracts:
                errors.append("unknown_or_invalid_contract")
            observer = event.get("observer_id")
            if identities.roles.get(observer) != "observer" or observer not in self.trusted_observers:
                errors.append("untrusted_observer")
            if event.get("evidence_hash") != canonical_hash(_without(event, "evidence_hash", "signature")):
                errors.append("evidence_hash_mismatch")
            if not identities.verify(observer, _without(event, "signature"), event.get("signature", "")):
                errors.append("invalid_event_signature")
            if event.get("event_type") not in EVENT_TYPES:
                errors.append("invalid_event_type")
            if event.get("verification_result") is not None and not isinstance(event["verification_result"], bool):
                errors.append("invalid_verification_result")
            try:
                when, observed = parse_time(event["event_time"]), parse_time(event["observed_at"])
                contract = self._contracts.get(cid)
                if observed < when or (contract and when < parse_time(contract["created_at"])):
                    errors.append("invalid_event_times")
                if event.get("event_type") == "SLA_FAILURE" and contract and when < parse_time(contract["agreed_deadline"]):
                    errors.append("failure_before_deadline")
            except (KeyError, ValueError, TypeError):
                errors.append("invalid_event_times")
            if not self.chain_status["valid"]:
                errors.append("invalid_log_chain")
            diagnostic = {"event_index": index, "event_id": eid, "contract_id": cid,
                          "valid": not errors, "reasons": sorted(set(errors))}
            self.validations.append(diagnostic)
            if not [reason for reason in errors if reason != "invalid_log_chain"]:
                # Retain replay diagnostics even if the presented log's chain
                # already failed, but never let unauthenticated IDs reserve it.
                seen_ids.add(eid)
            if not errors:
                self._verified.append(event)

    def query(self, as_of_time: str) -> dict[str, Any]:
        """Return one outcome per independent contract at the requested time.

        ``event_time`` dates the substantive outcome; ``observed_at`` dates its
        reliable availability. Both must be <= as_of_time. Resolving a dispute
        yields a new availability time, preventing historical leakage.
        """
        now = parse_time(as_of_time)
        states: dict[str, dict[str, Any]] = {}
        for cid, contract in self._contracts.items():
            if parse_time(contract["created_at"]) <= now:
                states[cid] = {"contract_id": cid, "provider_id": contract["provider_id"],
                    "asset_id": contract["asset_id"], "state": "PENDING" if now < parse_time(contract["agreed_deadline"]) else "UNKNOWN",
                    "outcome": None, "event_time": None, "observed_at": None, "evidence_status": "NO_VERIFIED_RESULT",
                    "delivery_completed": False, "sla_failed": False, "supporting_event_ids": [],
                    "duplicate_contract_reports": 0}
        available: dict[str, dict[str, Any]] = {}
        for event in self._verified:
            if parse_time(event["observed_at"]) > now or parse_time(event["event_time"]) > now:
                continue
            cid = event["contract_id"]
            if cid not in states:
                continue
            state, contract = states[cid], self._contracts[cid]
            available[event["event_id"]] = event
            kind = event["event_type"]
            if kind == "CHECK":
                continue  # Correlated technical subchecks are never independent outcomes.
            if kind == "DISPUTE":
                state.update(state="DISPUTED", outcome=None, evidence_status="DISPUTED")
                state["supporting_event_ids"].append(event["event_id"])
                continue
            if kind == "UNKNOWN":
                if state["outcome"] is None and state["state"] != "DISPUTED":
                    state.update(state="UNKNOWN", evidence_status="INSUFFICIENT_EVIDENCE")
                continue
            if kind == "RESOLUTION":
                refs = event.get("supporting_evidence_ids", [])
                result = event.get("resolved_outcome")
                # Resolution must reference independently verified substantive
                # evidence available by this time, for this same contract.
                support = next((available[ref] for ref in refs if ref in available
                    and available[ref]["contract_id"] == cid
                    and available[ref]["event_type"] in {"DELIVERY", "SLA_FAILURE"}
                    and self._result(available[ref], contract) == result), None)
                if state["state"] != "DISPUTED" or type(result) is not int or result not in (0, 1) or support is None:
                    state["evidence_status"] = "UNSUPPORTED_RESOLUTION"
                    continue
                if state["sla_failed"]:
                    result = 0
                    # A late completion cannot erase deadline failure; retain
                    # the actual failing observation as the substantive time.
                    failure_support = next((available[ref] for ref in state["supporting_event_ids"]
                        if ref in available and self._result(available[ref], contract) == 0), None)
                    if failure_support is not None:
                        support = failure_support
                state.update(state="RESOLVED", outcome=result, event_time=support["event_time"],
                             observed_at=event["observed_at"], evidence_status="VERIFIED_RESOLUTION")
                state["supporting_event_ids"].append(event["event_id"])
                continue
            result = self._result(event, contract)
            if result is None:
                continue
            if kind == "SLA_FAILURE" or parse_time(event["event_time"]) > parse_time(contract["agreed_deadline"]):
                state["sla_failed"] = True
            if kind == "DELIVERY" and event["verification_result"]:
                state["delivery_completed"] = True
            state["supporting_event_ids"].append(event["event_id"])
            if state["state"] == "DISPUTED":
                continue
            if state["outcome"] is not None:
                state["duplicate_contract_reports"] += 1
                if state["sla_failed"]:
                    if state["outcome"] != 0:
                        state.update(state="FAILURE", outcome=0, event_time=event["event_time"],
                                     observed_at=event["observed_at"], evidence_status="VERIFIED")
                elif state["outcome"] != result:
                    state.update(state="DISPUTED", outcome=None, evidence_status="CONFLICTING_VERIFIED_REPORTS")
                continue
            state.update(state="SUCCESS" if result else "FAILURE", outcome=result,
                         event_time=event["event_time"], observed_at=event["observed_at"], evidence_status="VERIFIED")
        outcomes = [{k: state[k] for k in ("contract_id", "provider_id", "asset_id", "outcome", "event_time", "observed_at")}
                    for state in states.values() if state["outcome"] is not None]
        outcomes.sort(key=lambda row: (parse_time(row["observed_at"]), row["contract_id"]))
        return {"outcomes": outcomes, "states": list(states.values()), "as_of_time": as_of_time,
                "chain_status": self.chain_status}

    @staticmethod
    def _result(event: Mapping[str, Any], contract: Mapping[str, Any]) -> int | None:
        if event["event_type"] == "SLA_FAILURE":
            return 0 if event["verification_result"] is False else None
        if event["event_type"] != "DELIVERY" or event["verification_result"] is None:
            return None
        match = (event.get("actual_content_hash") == contract["expected_content_hash"]
                 and event.get("actual_schema_hash") == contract["expected_schema_hash"])
        on_time = parse_time(event["event_time"]) <= parse_time(contract["agreed_deadline"])
        return int(event["verification_result"] and match and on_time)
