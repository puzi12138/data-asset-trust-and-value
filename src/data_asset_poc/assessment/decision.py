"""Fail-closed dataset admission decision using registry-bound evidence receipts.

This is not a cryptographic authorization service. The registry is an
externally trusted input established by the operator, NOT by the data supplier;
identity, source independence and truthful assessments require external checks.
An approved receipt hash binds its fields but does not authenticate its maker.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Mapping

from .binding import BindingError, asset_digest, canonical_asset, json_digest

FACT_VERSION = "INDEPENDENT-FACT-1"
CALIBRATION_VERSION = "INDEPENDENT-CALIBRATION-1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _number(v: Any) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def _probability(v: Any) -> bool:
    return _number(v) and 0.0 <= v <= 1.0


def _text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _sha(v: Any) -> bool:
    return isinstance(v, str) and _HEX64.fullmatch(v) is not None


def _contains_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, (dict, list)):
        values = v.values() if isinstance(v, dict) else v
        return any(_contains_null(value) for value in values)
    return False


def _registry_receipt(registry: Any, channel: str, receipt: Any) -> bool:
    """Check receipt bytes against an operator-owned registry, not self-claims."""
    if not isinstance(registry, Mapping) or not isinstance(receipt, Mapping):
        return False
    registered = registry.get(channel)
    rid = receipt.get("receipt_id")
    if not isinstance(registered, Mapping) or not _text(rid):
        return False
    expected = registered.get(rid)
    if not _sha(expected):
        return False
    try:
        return json_digest(receipt) == expected
    except (BindingError, TypeError, ValueError, RecursionError):
        return False


def _reply(decision: str, reason: str, digest: str | None) -> dict[str, Any]:
    return {"decision": decision, "reason": reason, "asset_sha256": digest,
            "trust_scope": "OPERATOR_REGISTRY_REQUIRED_NOT_A_SIGNATURE_OR_TRUTH_PROOF"}


def decide(asset: Any, context: Any, certificate: Any, utility: Any,
           trust_registry: Any, *, tau: float = 0.10,
           minimum_gain: float = 0.0) -> dict[str, Any]:
    """Evaluate a single exact asset under a specific task/model/budget.

    Independently supplied context: ``task_sha256, model_id, model_version,
    model_artifact_sha256, budget_sha256``. The registry maps receipt ID to canonical JSON SHA under
    ``fact`` and ``calibration``. Its provenance and access control belong to
    the caller; passing a supplier-controlled registry removes the guarantee.
    UNKNOWN/incomplete information is NEEDS_VERIFICATION, not good or bad.
    """
    digest = None
    try:
        clean = canonical_asset(asset)
        digest = asset_digest(clean)
        if not _probability(tau) or not _number(minimum_gain) or minimum_gain < 0:
            return _reply("NEEDS_VERIFICATION", "INVALID_DECISION_THRESHOLDS", digest)
        if _contains_null(clean) or not clean["records"]:
            return _reply("NEEDS_VERIFICATION", "INCOMPLETE_ASSET", digest)
        if not isinstance(context, Mapping) or not all(
            _text(context.get(k)) for k in ("model_id", "model_version")
        ) or not _sha(context.get("task_sha256")) or not _sha(context.get("budget_sha256")) or not _sha(context.get("model_artifact_sha256")):
            return _reply("NEEDS_VERIFICATION", "INCOMPLETE_CONTEXT", digest)
        if context["task_sha256"] != clean["task_sha256"]:
            return _reply("NEEDS_VERIFICATION", "TASK_CONTEXT_MISMATCH", digest)
        if not isinstance(certificate, Mapping):
            return _reply("NEEDS_VERIFICATION", "CERTIFICATE_MISSING", digest)
        if certificate.get("asset_sha256") != digest:
            return _reply("NEEDS_VERIFICATION", "ASSET_BINDING_MISMATCH", digest)
        binding = certificate.get("binding_valid")
        if binding is False:
            return _reply("REJECT", "BINDING_CONFLICT", digest)
        if binding is not True:
            return _reply("NEEDS_VERIFICATION", "BINDING_NOT_EXPLICITLY_TRUE", digest)
        risk = certificate.get("risk_upper")
        lower = certificate.get("known_conflict_lower_bound")
        if not _probability(risk) or not _probability(lower) or lower > risk:
            return _reply("NEEDS_VERIFICATION", "INVALID_RISK_OR_CONFLICT_BOUND", digest)
        fact = certificate.get("fact_receipt")
        if not _registry_receipt(trust_registry, "fact", fact):
            return _reply("NEEDS_VERIFICATION", "FACT_RECEIPT_NOT_TRUSTED", digest)
        if (fact.get("receipt_version") != FACT_VERSION or
                fact.get("scope") != "INDEPENDENT_FACTUAL_ORACLE" or
                fact.get("outcome") != "VERIFIED" or
                not _text(fact.get("source_id")) or unicodedata.normalize("NFC", fact["source_id"]).strip() == clean["source_id"] or
                not _text(fact.get("evidence_reference")) or not _text(fact.get("audit_method")) or
                fact.get("asset_sha256") != digest or fact.get("task_sha256") != clean["task_sha256"] or
                not _probability(fact.get("risk_upper")) or not _probability(fact.get("known_conflict_lower_bound")) or
                fact["risk_upper"] != risk or fact["known_conflict_lower_bound"] != lower):
            return _reply("NEEDS_VERIFICATION", "FACT_RECEIPT_INVALID_OR_SAME_SOURCE", digest)
        if lower > tau:
            return _reply("REJECT", "OBSERVED_CONFLICT_EXCEEDS_TOLERANCE", digest)
        if risk > tau:
            return _reply("NEEDS_VERIFICATION", "FACTUAL_RISK_TOO_HIGH", digest)
        if not isinstance(utility, Mapping) or utility.get("status") != "CALIBRATED_INDEPENDENTLY":
            return _reply("NEEDS_VERIFICATION", "UTILITY_NOT_FITTED_OR_CALIBRATED", digest)
        lcb = utility.get("lcb")
        if not _number(lcb) or lcb < 0:
            return _reply("NEEDS_VERIFICATION", "INVALID_OR_NEGATIVE_LCB", digest)
        if (utility.get("model_id") != context["model_id"] or
                utility.get("model_version") != context["model_version"] or
                utility.get("model_artifact_sha256") != context["model_artifact_sha256"] or
                utility.get("budget_sha256") != context["budget_sha256"] or
                utility.get("task_sha256") != context["task_sha256"]):
            return _reply("NEEDS_VERIFICATION", "UTILITY_CONTEXT_MISMATCH", digest)
        calibration = utility.get("calibration_receipt")
        if not _registry_receipt(trust_registry, "calibration", calibration):
            return _reply("NEEDS_VERIFICATION", "CALIBRATION_RECEIPT_NOT_TRUSTED", digest)
        if (calibration.get("receipt_version") != CALIBRATION_VERSION or
                calibration.get("status") != "CALIBRATED_INDEPENDENTLY" or
                calibration.get("split") != "INDEPENDENT_HELD_OUT" or
                calibration.get("asset_sha256") != digest or
                calibration.get("dataset_id") != clean["dataset_id"] or
                calibration.get("dataset_version") != clean["dataset_version"] or
                calibration.get("task_sha256") != context["task_sha256"] or
                calibration.get("model_id") != context["model_id"] or
                calibration.get("model_version") != context["model_version"] or
                calibration.get("model_artifact_sha256") != context["model_artifact_sha256"] or
                calibration.get("budget_sha256") != context["budget_sha256"] or
                not _sha(calibration.get("calibration_members_sha256")) or
                not _text(calibration.get("calibration_evidence_reference")) or
                type(calibration.get("calibration_n")) is not int or calibration["calibration_n"] < 1 or
                not _number(calibration.get("lcb")) or calibration["lcb"] != lcb):
            return _reply("NEEDS_VERIFICATION", "CALIBRATION_RECEIPT_CONTEXT_OR_VERSION_INVALID", digest)
        if lcb <= minimum_gain:
            return _reply("NEEDS_VERIFICATION", "GAIN_LOWER_BOUND_NOT_POSITIVE", digest)
        return _reply("ADMIT", "REGISTRY_BOUND_FACT_RISK_AND_CALIBRATED_GAIN", digest)
    except (BindingError, TypeError, ValueError, KeyError, OverflowError, RecursionError) as exc:
        return _reply("NEEDS_VERIFICATION", "INVALID_INPUT:" + type(exc).__name__, digest)
