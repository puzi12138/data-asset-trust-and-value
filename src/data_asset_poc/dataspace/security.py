"""Public, deliberately insecure demo identities; never production credentials."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
from typing import Any
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ..credit.evidence import canonical_bytes, canonical_hash, parse_time

ROLES = {"registry": "registry", "provider_a": "provider", "provider_b": "provider",
         "consumer": "consumer", "outsider": "consumer", "verifier": "verifier"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def private_demo_key(actor: str) -> Ed25519PrivateKey:
    if actor not in ROLES:
        raise ValueError("UNKNOWN_DEMO_IDENTITY")
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(
        canonical_bytes(["PUBLIC_INSECURE_DELIVERY_CORE_DEMO_NOT_SECRET", actor])).digest())


def public_registry() -> dict:
    return {actor: {"role": role, "public_key": base64.b64encode(private_demo_key(actor).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()} for actor, role in ROLES.items()}


def sign(actor: str, payload: dict) -> dict:
    body = {"signer_id": actor, "payload": payload}
    return {**body, "signature": base64.b64encode(private_demo_key(actor).sign(canonical_bytes(body))).decode()}


def verify(envelope: dict, *, expected_actor: str | None = None, expected_role: str | None = None) -> dict:
    if not isinstance(envelope, dict) or set(envelope) != {"signer_id", "payload", "signature"}:
        raise ValueError("INVALID_SIGNED_ENVELOPE")
    actor = envelope["signer_id"]
    if not isinstance(actor, str) or actor not in ROLES or expected_actor is not None and actor != expected_actor or expected_role is not None and ROLES[actor] != expected_role:
        raise ValueError("UNTRUSTED_SIGNER_OR_ROLE")
    if not isinstance(envelope["payload"], dict):
        raise ValueError("PAYLOAD_MUST_BE_OBJECT")
    try:
        private_demo_key(actor).public_key().verify(base64.b64decode(envelope["signature"], validate=True),
            canonical_bytes({"signer_id": actor, "payload": envelope["payload"]}))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise ValueError("INVALID_SIGNATURE") from exc
    return envelope["payload"]


def byte_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def policy_allows(metadata: dict, actor: str, purpose: str, at: str | None = None) -> bool:
    policy = metadata["usage_policy"]
    return (actor in policy.get("allowed_consumers", []) and purpose in policy.get("allowed_purposes", [])
            and parse_time(at or utcnow()) < parse_time(policy["expires_at"]))
