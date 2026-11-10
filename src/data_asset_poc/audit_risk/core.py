"""Authenticate fixed-plan evidence and keep content/use decisions distinct."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..credit.evidence import canonical_bytes, parse_time
from .intervals import assess_fixed_sample
from .plans import (PLAN_VERSION, SAMPLING_ALGORITHM, normalized_hash,
                    plan_hash, reference_descriptor_hash, sample_positions)

METHOD_VERSION = "reference_evidence-fixed-risk-audit-v1.1"
OUTCOMES = frozenset({"PASS", "ERROR", "INCOMPARABLE", "UNRESOLVED"})


def evaluate_audit(*, dataset: dict[str, Any], plan: dict[str, Any],
                   evidence: dict[str, Any] | None, trust_context: dict[str, Any],
                   as_of: str | None = None) -> dict[str, Any]:
    """Evaluate evidence; ``trust_context`` must never be an uploader's JSON.

    Trusted plan hashes, keys, reference classes and error-bound assumptions are
    configured by the local evaluator. Cryptography validates who attested to
    observations; this function cannot establish their truth or a truly prior
    preregistration. SRS guarantees are conditional on that external protocol.
    Missing sampled outcomes widen bounds instead of shrinking the denominator.
    """
    now = as_of or datetime.now(timezone.utc).isoformat()
    parse_time(now)
    result: dict[str, Any] = {
        "method_version": METHOD_VERSION, "dataset_id": dataset.get("dataset_id"),
        "version": dataset.get("version"), "content_hash": dataset.get("content_hash"),
        "plan_id": plan.get("plan_id"), "plan_hash": plan.get("plan_hash"),
        "evaluated_at": now, "decision": "NEED_MORE_EVIDENCE",
        "content_decision": "NEED_MORE_EVIDENCE", "statistical_decision": None,
        "claim_scope": plan.get("claim_scope"), "reasons": [], "content_reasons": [],
        "statistical_result": None, "n": 0, "e": 0, "unresolved": None,
        "sample_size": plan.get("sample_size"), "population_size": plan.get("population_size"),
        "epsilon": plan.get("epsilon"), "alpha": plan.get("alpha"),
        "error_rate_interval": None, "observations": [],
        "signature_valid": None, "reference": None,
        "counts_verified": False, "strict_dataset_app_content_baseline": "NEED_MORE_EVIDENCE",
        "assumptions": [
            "The finite frame and immutable dataset version were fixed before sampling.",
            "A single SRS without replacement was actually executed using this precommitted plan; no seed search or optional stopping.",
            "PASS/ERROR compare the declared field scope with the locally trusted fixed reference.",
            "Any reference error upper bound is valid for this same finite population and scope.",
            "A trusted signature authenticates an attestation, not factual correctness."],
        "limitations": [
            "No probability that all dataset facts are true is returned.",
            "The program cannot independently prove external facts, audit honesty, or actual prior preregistration.",
            "Repeated plans are separate research sensitivity cases; choosing a favorable plan after results invalidates the single-plan guarantee.",
            "Content conformity does not establish timeliness, structural suitability or population representativeness."],
        "verification": {"sampling_positions_recomputed": False, "plan_hash_pinned": False,
            "reference_bytes_and_descriptor_pinned": False,
            "external_preregistration_independently_verified": False,
            "reference_facts_independently_verified_by_model": False},
        "value_policy": "Do not multiply this decision or interval into task utility or monetary value.",
    }

    def finish(status: str, *reasons: str, content_status: str | None = None) -> dict[str, Any]:
        result["decision"] = status
        result["reasons"].extend(x for x in reasons if x not in result["reasons"])
        if content_status is not None:
            result["content_decision"] = content_status
            result["content_reasons"].extend(x for x in reasons if x not in result["content_reasons"])
        return result

    try:
        if not isinstance(dataset.get("dataset_id"), str) or not dataset["dataset_id"]:
            raise ValueError("DATASET_ID_REQUIRED")
        if not isinstance(dataset.get("version"), str) or not dataset["version"]:
            raise ValueError("DATASET_VERSION_REQUIRED")
        digest = normalized_hash(dataset.get("content_hash"))
        row_count = dataset.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 1:
            raise ValueError("POSITIVE_DATASET_ROW_COUNT_REQUIRED")
        if plan.get("plan_version") != PLAN_VERSION or plan.get("sampling_algorithm") != SAMPLING_ALGORITHM:
            raise ValueError("UNSUPPORTED_PLAN_VERSION_OR_ALGORITHM")
        if plan.get("plan_hash") != plan_hash(plan):
            raise ValueError("PLAN_HASH_MISMATCH")
        pinned = trust_context.get("trusted_plan_hash")
        if not pinned:
            return finish("NEED_MORE_EVIDENCE", "PLAN_NOT_IN_TRUSTED_PREREGISTRATION_STORE")
        if pinned != plan["plan_hash"]:
            raise ValueError("PLAN_NOT_EQUAL_TO_TRUSTED_PINNED_HASH")
        result["verification"]["plan_hash_pinned"] = True
        if (plan.get("dataset_id") != dataset["dataset_id"] or plan.get("version") != dataset["version"]
                or normalized_hash(plan.get("content_hash")) != digest or plan.get("population_size") != row_count):
            raise ValueError("PLAN_DATASET_HASH_VERSION_OR_FRAME_MISMATCH")
        if isinstance(plan.get("population_size"), bool) or not isinstance(plan.get("population_size"), int):
            raise ValueError("PLAN_POPULATION_SIZE_MUST_BE_INTEGER")
        created = parse_time(plan["created_at"])
        if created > parse_time(now):
            raise ValueError("PLAN_NOT_YET_AVAILABLE_AS_OF")
        if plan.get("sampling_mechanism") != "simple_random_without_replacement":
            return finish("NEED_MORE_EVIDENCE", "NO_VALID_SRS_POPULATION_INFERENCE")
        if plan.get("fixed_sample") is not True or plan.get("optional_stopping") is not False:
            return finish("NEED_MORE_EVIDENCE", "FIXED_PLAN_ASSUMPTIONS_NOT_SATISFIED")
        selected = sample_positions(row_count, plan["sample_size"], plan["seed"])
        if not isinstance(plan.get("positions"), list) or any(type(p) is not int for p in plan["positions"]):
            raise ValueError("PLAN_POSITIONS_MUST_BE_INTEGER_LIST")
        if plan.get("positions") != selected:
            raise ValueError("POSITIONS_DIFFER_FROM_PREDECLARED_RANDOM_DRAW")
        result["verification"]["sampling_positions_recomputed"] = True
        if plan.get("claim_scope") not in {"reference_conformance", "factual_accuracy", "not_applicable"}:
            raise ValueError("UNSUPPORTED_CLAIM_SCOPE")
        if plan["claim_scope"] == "not_applicable":
            if trust_context.get("content_not_applicable") is True:
                return finish("NOT_APPLICABLE", "EVALUATOR_DECLARED_CONTENT_REQUIREMENT_NOT_APPLICABLE", content_status="NOT_APPLICABLE")
            return finish("NEED_MORE_EVIDENCE", "NOT_APPLICABLE_REQUIRES_EVALUATOR_BASIS")
        reference = (trust_context.get("references") or {}).get(plan.get("reference_id"))
        if not isinstance(reference, dict):
            raise ValueError("UNTRUSTED_OR_UNKNOWN_REFERENCE")
        if reference.get("evidence_class") not in {"A", "B", "C", "SIMULATED"}:
            raise ValueError("REFERENCE_EVIDENCE_CLASS_REQUIRED")
        if not plan.get("reference_hash") or not plan.get("reference_descriptor_hash"):
            return finish("NEED_MORE_EVIDENCE", "REFERENCE_VERSION_AND_PROTOCOL_PINS_MISSING")
        if not reference.get("reference_hash") or not reference.get("audit_protocol"):
            return finish("NEED_MORE_EVIDENCE", "TRUSTED_REFERENCE_VERSION_OR_PROTOCOL_NOT_SPECIFIED")
        if (normalized_hash(plan["reference_hash"]) != normalized_hash(reference["reference_hash"])
                or normalized_hash(plan["reference_descriptor_hash"]) != reference_descriptor_hash(reference)):
            raise ValueError("REFERENCE_BYTES_OR_PROTOCOL_DESCRIPTOR_CHANGED_SINCE_PLAN")
        result["verification"]["reference_bytes_and_descriptor_pinned"] = True
        result["reference"] = {k: reference.get(k) for k in (
            "evidence_class", "source_uri", "reference_hash", "reliability_basis",
            "error_rate_upper", "reference_risk_beta", "supported_scopes", "audit_protocol")}
        result["reference"]["reference_id"] = plan["reference_id"]
        result["reference"]["reference_descriptor_hash"] = plan["reference_descriptor_hash"]
        result["reference"]["factual_truth_established"] = False
        if reference.get("available_at") and parse_time(reference["available_at"]) > created:
            raise ValueError("REFERENCE_NOT_AVAILABLE_WHEN_PLAN_CREATED")
        if not reference.get("reliability_basis"):
            return finish("NEED_MORE_EVIDENCE", "REFERENCE_RELIABILITY_BASIS_MISSING")
        if not evidence:
            return finish("NEED_MORE_EVIDENCE", "AUDIT_EVIDENCE_MISSING")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("payload"), dict):
            raise ValueError("AUDIT_ENVELOPE_WITH_PAYLOAD_REQUIRED")
        payload = evidence["payload"]
        if any(payload.get(key) != plan.get(key) for key in (
                "plan_id", "plan_hash", "dataset_id", "version", "content_hash", "reference_id")):
            raise ValueError("AUDIT_PLAN_DATASET_VERSION_HASH_OR_REFERENCE_BINDING_MISMATCH")
        if not isinstance(payload.get("audit_id"), str) or not payload["audit_id"]:
            raise ValueError("AUDIT_ID_REQUIRED")
        when = parse_time(payload["audited_at"])
        if when < created or when > parse_time(now):
            raise ValueError("AUDIT_TIME_OUTSIDE_AVAILABLE_FIXED_PLAN_WINDOW")
        if not evidence.get("signature"):
            result["observations"] = payload.get("outcomes", [])
            return finish("NEED_MORE_EVIDENCE", "AUDIT_SIGNATURE_MISSING")
        if set(evidence) != {"signer_id", "payload", "signature"}:
            raise ValueError("UNEXPECTED_AUDIT_ENVELOPE_FIELDS")
        signer = evidence.get("signer_id")
        key = (trust_context.get("public_keys") or {}).get(signer)
        if not isinstance(key, dict) or key.get("role") != "auditor":
            raise ValueError("UNTRUSTED_OR_NON_AUDITOR_SIGNER")
        if reference.get("allowed_auditors") is not None and signer not in reference["allowed_auditors"]:
            raise ValueError("AUDITOR_NOT_AUTHORIZED_FOR_REFERENCE")
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key["public_key"], validate=True))
        public.verify(base64.b64decode(evidence["signature"], validate=True),
                      canonical_bytes({"signer_id": signer, "payload": payload}))
        result["signature_valid"] = True
        result["signer_id"] = signer
        observations = payload.get("outcomes")
        if not isinstance(observations, list):
            raise ValueError("AUDIT_OUTCOMES_MUST_BE_LIST")
        by_position: dict[int, dict[str, Any]] = {}
        for record in observations:
            if not isinstance(record, dict):
                raise ValueError("AUDIT_OUTCOME_MUST_BE_OBJECT")
            position = record.get("position")
            if isinstance(position, bool) or not isinstance(position, int) or position not in selected:
                raise ValueError("AUDIT_OUTCOME_POSITION_NOT_IN_FIXED_PLAN")
            if position in by_position:
                raise ValueError("DUPLICATE_AUDIT_OUTCOME_POSITION")
            if record.get("outcome") not in OUTCOMES:
                raise ValueError("UNKNOWN_AUDIT_OUTCOME")
            by_position[position] = record
        full = [by_position.get(p, {"position": p, "outcome": "UNRESOLVED",
                                   "reason": "selected_position_not_reported"}) for p in selected]
        e = sum(x["outcome"] == "ERROR" for x in full)
        unresolved = sum(x["outcome"] in {"UNRESOLVED", "INCOMPARABLE"} for x in full)
        result.update({"observations": full, "e": e, "n": len(selected) - unresolved,
                       "unresolved": unresolved, "counts_verified": True})
        result["strict_dataset_app_content_baseline"] = (
            "ACCEPT_CONDITIONALLY" if e == 0 and unresolved == 0 else "NEED_MORE_EVIDENCE")
        # The baseline above isolates the strict zero-disagreement gate. The
        # original full Dataset_app fusion still additionally requires all dimensions.
        if plan["claim_scope"] not in reference.get("supported_scopes", []):
            return finish("NEED_MORE_EVIDENCE", "REFERENCE_DOES_NOT_SUPPORT_REQUESTED_CLAIM_SCOPE")
        if plan["claim_scope"] == "factual_accuracy":
            if reference["evidence_class"] in {"A", "B"}:
                return finish("NEED_MORE_EVIDENCE", "A_OR_B_REFERENCE_CANNOT_ESTABLISH_FACTUAL_ACCURACY")
            if reference["evidence_class"] == "C" and not reference.get("independent_origin_evidence"):
                return finish("NEED_MORE_EVIDENCE", "FACTUAL_SCOPE_NEEDS_INDEPENDENT_ORIGIN_EVIDENCE")
            if reference.get("error_rate_upper") is None:
                return finish("NEED_MORE_EVIDENCE", "FACTUAL_REFERENCE_ERROR_BOUND_UNKNOWN")
        rho = 0.0 if plan["claim_scope"] == "reference_conformance" else reference["error_rate_upper"]
        beta = 0.0 if plan["claim_scope"] == "reference_conformance" else reference.get("reference_risk_beta", 0.0)
        stats = assess_fixed_sample(population_size=row_count, sample_size=len(selected), errors=e,
            unresolved=unresolved, epsilon=plan["epsilon"], alpha=plan["alpha"],
            reference_error_upper=rho, reference_risk_beta=beta)
        result.update({"statistical_result": stats, "error_rate_interval": stats["error_rate_interval"],
                       "statistical_decision": stats["statistical_decision"],
                       "content_decision": stats["statistical_decision"]})
        result["simulation_only"] = reference["evidence_class"] == "SIMULATED"
        result["content_reasons"] = ["FIXED_SRS_ERROR_BOUND_COMPARED_WITH_PREDECLARED_EPSILON"]
        if unresolved:
            result["content_reasons"].append("INCOMPARABLE_OR_MISSING_OUTCOMES_WIDEN_INTERVAL")
        provenance = trust_context.get("provenance_status", "MISSING")
        if provenance == "INVALID":
            return finish("EVIDENCE_INVALID", "DATASET_PROVENANCE_OR_INTEGRITY_INVALID")
        if provenance != "VERIFIED":
            return finish("NEED_MORE_EVIDENCE", "DATASET_PROVENANCE_NOT_VERIFIED")
        if trust_context.get("require_representativeness", False):
            representative = trust_context.get("representation_status", "UNKNOWN")
            if representative == "FAIL":
                return finish("REJECT_CONFIRMED", "DECLARED_USE_REPRESENTATIVENESS_REQUIREMENT_FAILED")
            if representative not in {"PASS", "NOT_APPLICABLE"}:
                return finish("NEED_MORE_EVIDENCE", "DECLARED_USE_REPRESENTATIVENESS_NOT_ESTABLISHED")
        return finish(result["content_decision"], "CONTENT_REQUIREMENT_ONLY_NO_GLOBAL_TRUTH_CLAIM")
    except InvalidSignature:
        result["signature_valid"] = False
        return finish("EVIDENCE_INVALID", "AUDIT_SIGNATURE_INVALID", content_status="EVIDENCE_INVALID")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return finish("EVIDENCE_INVALID", str(exc) or type(exc).__name__, content_status="EVIDENCE_INVALID")
