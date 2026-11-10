"""Evidence-aware assessment with explicit refusal and nonprobabilistic bounds."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .baselines import DIMENSIONS, WEIGHT_PROFILES, compute_baselines, get_weights, partial_index
from .metrics import (accuracy_evidence, dimension, provenance_evidence,
                      representativeness_evidence, structural_quality, timeliness_evidence)
from .schemas import identify_schema

METHOD_VERSION = "dataset_app-evidence-aware-v1.1"


def evaluate_dataset(frame: pd.DataFrame, *, dataset_id: str, version: str,
                     context: dict | None = None, raw_bytes: bytes | None = None) -> dict[str, Any]:
    """Assess a fixed dataset version against explicit evidence supplied by caller.

    ``context`` is trusted evaluation configuration, not arbitrary uploaded JSON.
    In particular, trusted public keys and audit references must come from the
    local evaluator. The caller must ensure ``frame`` was parsed from
    ``raw_bytes``; the model's byte hash does not itself enforce CSV parsing.
    Missing evidence is retained as UNKNOWN; no 0/1 substitution is used for the
    main index. See docs/DATASET_APP_METHOD.md for the full contract and equations.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty or not len(frame.columns):
        raise ValueError("NONEMPTY_DATAFRAME_REQUIRED")
    if frame.columns.duplicated().any() or any(not isinstance(c, str) or not c for c in frame):
        raise ValueError("DISTINCT_NONEMPTY_STRING_COLUMNS_REQUIRED")
    if not isinstance(dataset_id, str) or not dataset_id or not isinstance(version, str) or not version:
        raise ValueError("DATASET_ID_AND_VERSION_REQUIRED")
    if raw_bytes is not None and not isinstance(raw_bytes, bytes):
        raise ValueError("RAW_BYTES_MUST_BE_BYTES")
    context = {} if context is None else context
    if not isinstance(context, dict):
        raise ValueError("CONTEXT_MUST_BE_OBJECT")
    temporal = context.get("temporal") or {}
    temporal_column = temporal.get("record_time_column")
    excluded = [temporal_column] if temporal_column in frame and temporal_column not in {"y", "quality"} else []
    structural_frame = frame.drop(columns=excluded)
    schema = context.get("schema_name") or identify_schema(structural_frame)
    structure, quality = structural_quality(structural_frame, schema)
    structure["metrics"]["assessed_columns"] = list(structural_frame.columns)
    structure["metrics"]["excluded_temporal_columns"] = excluded
    provenance_context = dict(context.get("provenance") or {})
    if context.get("source") and not provenance_context.get("metadata"):
        provenance_context["metadata"] = {"source": context["source"]}
    provenance = provenance_evidence(dataset_id, version, raw_bytes, provenance_context)
    accuracy = accuracy_evidence(frame, context.get("audit"))
    timeliness = timeliness_evidence(frame, temporal)
    representative = representativeness_evidence(frame, context.get("reference"))
    dimensions = {"structure": structure, "provenance": provenance, "accuracy": accuracy,
                  "timeliness": timeliness, "representativeness": representative}
    scores = {k: v["score"] for k, v in dimensions.items()}
    weights = get_weights(context.get("weight_profile", "equal"), context.get("weights"))
    projection = partial_index(scores, weights)
    refusal = []
    if accuracy["score"] is None:
        refusal.append("NO_POPULATION_VALID_CONTENT_ACCURACY_AUDIT")
    if provenance["status"] == "FAILED":
        refusal.append("INTEGRITY_OR_SIGNATURE_BINDING_FAILED")
    elif provenance["status"] != "VERIFIED":
        refusal.append("PROVENANCE_NOT_VERIFIED")
    if projection["unknown_dimensions"]:
        refusal.append("MISSING_DIMENSION_EVIDENCE")
    # Negative, experimentally checkable label changes cause a screening veto;
    # this does not assert that either original or modified labels are true.
    injection_alert = accuracy["metrics"].get("reference_kind") == "known_injection_reference" and accuracy["metrics"].get("screening_detected", False)
    if injection_alert:
        refusal.append("KNOWN_REFERENCE_LABEL_CHANGES_REQUIRE_REVIEW")
    # An externally documented audit can establish errors in inspected labels
    # even when its sample is insufficient for population inference. A high
    # arithmetic average must never silently erase such observed discrepancies.
    external_accuracy_alert = (accuracy["metrics"].get("reference_kind") == "independent_verified_labels"
        and bool(accuracy["metrics"].get("verification_description"))
        and accuracy["metrics"].get("disagreements", 0) > 0)
    if external_accuracy_alert:
        refusal.append("EXTERNAL_AUDIT_DISCREPANCIES_REQUIRE_REVIEW")
    fusion = {**projection, "status": "REFUSED" if refusal else "CONDITIONAL_INDEX_AVAILABLE",
        "point_index": None if refusal else projection["interval_lower"], "refusal_reasons": refusal,
        "content_accuracy_alert": bool(injection_alert or external_accuracy_alert),
        "weights": weights, "scale": "0_to_1_defined_conformance_index_not_truth_probability",
        "interval_type": "partial_identification_from_unknown_dimension_values_in_0_1",
        "uncertainty_note": "The index interval bounds missing contributions only; it is not a sampling confidence interval and ignores metric/reference uncertainty."}
    screening_scores = dict(scores)
    if accuracy["metrics"].get("reference_agreement") is not None:
        screening_scores["accuracy"] = accuracy["metrics"]["reference_agreement"]
    screening = partial_index(screening_scores, weights)
    screening_index = {"score": screening["observed_projection"],
        "evidence_coverage": screening["evidence_coverage"],
        "uses_known_injection_reference": accuracy["metrics"].get("reference_kind") == "known_injection_reference",
        "meaning": "Observed-reference conformance diagnostic; incomplete dimensions are renormalized. Not a definitive trust score or truth probability.",
        "label_change_alert": bool(injection_alert)}
    known_count = sum(v is not None for v in scores.values())
    evidence = dimension(score=known_count / len(DIMENSIONS), status="MEASURED", evidence_class="assessment_metadata",
        metrics={"fully_scored_dimensions": known_count, "assessment_dimensions": len(DIMENSIONS),
                 "dimension_score_coverage": known_count / len(DIMENSIONS),
                 "weighted_dimension_coverage": projection["evidence_coverage"],
                 "dimension_statuses": {k: v["status"] for k, v in dimensions.items()},
                 "known_injection_comparison_available": bool(accuracy["metrics"]),
                 "audit_rows": accuracy["metrics"].get("audited_rows", 0)},
        meaning="Evidence coverage accounting; excluded from fusion to avoid double-counting its inputs.")
    dimensions["evidence_sufficiency"] = evidence
    missing = [{"dimension": k, "item": item} for k, v in dimensions.items() for item in v["missing_evidence"]]
    risks = [{"dimension": k, "message": item} for k, v in dimensions.items() for item in v["risks"]]
    risks.extend([{"dimension": "global", "message": "No dataset truth probability or monetary valuation is inferred by this assessment."},
                  {"dimension": "accuracy", "message": "Passing structural/domain checks does not verify label truth or external facts."}])
    sensitivity = {}
    for profile, profile_weights in WEIGHT_PROFILES.items():
        identified = partial_index(scores, profile_weights)
        diag = partial_index(screening_scores, profile_weights)
        sensitivity[profile] = {**identified, "screening_projection": diag["observed_projection"],
            "point_index": None if refusal else identified["interval_lower"], "refused": bool(refusal)}
    baselines = compute_baselines(quality, scores, weights)
    diagnostic_baselines = compute_baselines(quality, screening_scores, weights)
    baselines["no_provenance_screening_wsm"] = {
        **diagnostic_baselines["no_provenance_wsm"], "uses_reference_agreement_when_available": True,
        "meaning": "Same-reference diagnostic ablation; no provenance criteria, not a factual trust index."}
    baselines["reference_topsis_diagnostic"] = {
        **diagnostic_baselines["fixed_ideal_topsis"], "uses_reference_agreement_when_available": True,
        "meaning": "Same-reference fixed-ideal TOPSIS diagnostic; not a factual trust index."}
    return {"method_version": METHOD_VERSION, "dataset_id": dataset_id, "version": version,
        "evaluated_at": context.get("evaluated_at") or datetime.now(timezone.utc).isoformat(),
        "schema_name": schema, "row_count": len(frame), "column_count": len(frame.columns),
        "dimensions": dimensions, "fusion": fusion, "screening_index": screening_index,
        "baselines": baselines, "weight_sensitivity": sensitivity,
        "missing_evidence": missing, "risks": risks, "applicable_scenarios": context.get("applicable_scenarios", ["local_research_dataset_assessment"]),
        "method": {"weight_source": "predeclared_research_assumptions_not_fitted_or_calibrated",
            "reference_scope": context.get("reference", {}).get("applicable_population") if context.get("reference") else None,
            "factual_probability": False, "provider_reliability_used": False,
            "task_utility_used": False, "evidence_sufficiency_in_fusion": False,
            "value_link_policy": "Separate exact-version task measurement only; never multiply trust index into value."}}
