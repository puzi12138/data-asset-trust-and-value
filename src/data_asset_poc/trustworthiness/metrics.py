"""Evidence checks with numerator/denominator and explicit epistemic limits."""
from __future__ import annotations

import base64
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from scipy.spatial.distance import jensenshannon

from ..credit.evidence import canonical_bytes
from ..quality import evaluate_quality, missing_mask
from .schemas import rules_for


def dimension(*, score: float | None = None, status: str = "UNKNOWN", evidence_class: str = "unknown",
              metrics: dict | None = None, evidence: list | None = None, missing: list | None = None,
              risks: list | None = None, meaning: str = "") -> dict:
    return {"score": score, "status": status, "evidence_class": evidence_class,
            "metrics": metrics or {}, "evidence": evidence or [], "missing_evidence": missing or [],
            "risks": risks or [], "meaning": meaning}


def structural_quality(frame: pd.DataFrame, schema_name: str) -> tuple[dict, dict]:
    rules, constraints = rules_for(frame, schema_name)
    if rules is not None:
        result = evaluate_quality(frame, rules, constraints)
    else:
        missing = pd.DataFrame({c: missing_mask(frame[c]) for c in frame})
        duplicates = int(frame.duplicated().sum())
        result = {"row_count": len(frame), "cell_count": int(frame.size),
            "missing_cells": int(missing.to_numpy().sum()), "duplicate_excess": duplicates,
            "completeness": 1 - int(missing.to_numpy().sum()) / frame.size,
            "uniqueness": 1 - duplicates / len(frame), "format_validity": None,
            "consistency": None, "quality_score": None,
            "per_column": {c: {"missing": int(missing[c].sum())} for c in frame},
            "consistency_rules": {}}
    values = [result[k] for k in ("completeness", "uniqueness", "format_validity", "consistency")]
    known = [v for v in values if v is not None]
    for name in ("completeness", "uniqueness", "format_validity", "consistency"):
        result[name + "_status"] = "MEASURED" if result[name] is not None else "NOT_VERIFIED"
    # Unknown denominator is never turned into a passing check.
    result["measured_projection"] = float(np.mean(known)) if known else None
    status = "MEASURED" if len(known) == 4 else "PARTIALLY_MEASURED"
    missing = [] if len(known) == 4 else ["declared_domain_format_and_consistency_rules_or_eligible_rows"]
    return dimension(score=result["quality_score"], status=status, evidence_class="measured",
        metrics=result, evidence=[{"type": "dataset_cells", "rows": len(frame), "columns": len(frame.columns)}],
        missing=missing, meaning="Structural conformance to declared checks; neither label truth nor factual accuracy."), result


def _normalized_hash(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.removeprefix("sha256:").lower()
    return value if len(value) == 64 and all(c in "0123456789abcdef" for c in value) else None


def provenance_evidence(dataset_id: str, version: str, raw_bytes: bytes | None, evidence: dict) -> dict:
    metadata = evidence.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("PROVENANCE_METADATA_MUST_BE_OBJECT")
    actual_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else None
    declared_hash = _normalized_hash(metadata.get("content_hash"))
    hash_match = actual_hash == declared_hash if actual_hash and declared_hash else None
    declared_id = metadata.get("asset_id", metadata.get("dataset_id"))
    binding = (declared_id == dataset_id and str(metadata["version"]) == version
               if declared_id is not None and metadata.get("version") is not None else None)
    envelope = evidence.get("envelope")
    keys = evidence.get("public_keys") or {}
    signature_valid, signature_error = None, None
    signer = None
    if envelope is not None:
        try:
            if not isinstance(envelope, dict) or set(envelope) != {"signer_id", "payload", "signature"}:
                raise ValueError("INVALID_ENVELOPE")
            signer = envelope["signer_id"]
            expected = evidence.get("expected_signer")
            if not isinstance(signer, str) or signer not in keys or expected is not None and signer != expected:
                # An unknown trust anchor is an unverified signer, not proof of
                # an invalid mathematical signature or falsified CSV content.
                signature_error = "UNTRUSTED_SIGNER"
            else:
                entry = keys[signer]
                key_data = entry["public_key"] if isinstance(entry, dict) else entry
                public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_data, validate=True))
                public.verify(base64.b64decode(envelope["signature"], validate=True),
                    canonical_bytes({"signer_id": signer, "payload": envelope["payload"]}))
                if envelope["payload"] != metadata:
                    raise ValueError("SIGNED_PAYLOAD_METADATA_MISMATCH")
                signature_valid = True
        except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
            signature_valid, signature_error = False, type(exc).__name__ + ":" + str(exc)
    source = metadata.get("source") or (metadata.get("provenance") or {}).get("source")
    derivation = metadata.get("derivation") or (metadata.get("provenance") or {}).get("modifications")
    controls = {"content_hash_matches": hash_match, "metadata_signature_valid": signature_valid,
                "asset_version_binding": binding}
    negative = [k for k, v in controls.items() if v is False]
    unknown = [k for k, v in controls.items() if v is None]
    score = 0.0 if negative else (1.0 if not unknown else None)
    return dimension(score=score, status="FAILED" if negative else "VERIFIED" if not unknown else "NOT_VERIFIED",
        evidence_class="cryptographically_verified" if signature_valid else "provider_declared" if metadata else "unknown",
        metrics={**controls, "actual_content_hash": "sha256:" + actual_hash if actual_hash else None,
            "declared_content_hash": metadata.get("content_hash"), "signature_error": signature_error,
            "source_description_present": bool(source), "derivation_description_present": bool(derivation),
            "source_description": source, "derivation_description": derivation, "signer_id": signer,
            "source_description_status": "PROVIDER_DECLARED" if source else "UNKNOWN",
            "derivation_status": "PROVIDER_DECLARED" if derivation else "UNKNOWN",
            "trusted_key_count": len(keys), "negative_controls": negative},
        evidence=[{"type": "signed_metadata" if envelope else "declared_metadata", "signer_id": signer,
                   "content_binding": signature_valid is True and hash_match is True and binding is True}],
        missing=unknown + ([] if source else ["source_description"]) + ([] if derivation else ["derivation_description"]),
        risks=negative + (["Source and derivation descriptions remain claims unless independently corroborated."] if source or derivation else []),
        meaning="Byte integrity and local trusted-key metadata binding; valid signatures do not establish factual truth or ownership.")


def wilson_interval(passed: int, checked: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if checked <= 0 or not 0 <= passed <= checked:
        raise ValueError("INVALID_AUDIT_COUNTS")
    p = passed / checked
    denominator = 1 + z * z / checked
    centre = (p + z * z / (2 * checked)) / denominator
    radius = z * math.sqrt(p * (1 - p) / checked + z * z / (4 * checked * checked)) / denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


def accuracy_evidence(frame: pd.DataFrame, audit: dict | None) -> dict:
    if not audit:
        return dimension(missing=["independently_verified_labels_or_facts", "documented_audit_sampling"],
            meaning="No independently checkable truth reference; format success is not accuracy.")
    reference = audit.get("reference_frame")
    target = audit.get("target_column")
    if not isinstance(reference, pd.DataFrame) or len(reference) != len(frame) or target not in reference or target not in frame:
        raise ValueError("AUDIT_REQUIRES_ALIGNED_FULL_REFERENCE_FRAME_AND_TARGET")
    positions = audit.get("positions", list(range(len(frame))))
    if (not isinstance(positions, (list, tuple, np.ndarray)) or not len(positions) or
            any(isinstance(x, bool) or not isinstance(x, (int, np.integer)) or x < 0 or x >= len(frame) for x in positions)
            or len(set(positions)) != len(positions)):
        raise ValueError("AUDIT_POSITIONS_MUST_BE_DISTINCT_IN_RANGE_INTEGERS")
    if not audit.get("reference_id"):
        raise ValueError("AUDIT_REFERENCE_ID_REQUIRED")
    mechanism = audit.get("mechanism", "convenience")
    if mechanism not in {"simple_random_without_replacement", "census", "convenience"}:
        raise ValueError("UNKNOWN_AUDIT_SAMPLING_MECHANISM")
    if mechanism == "census" and len(positions) != len(frame):
        raise ValueError("CENSUS_MUST_COVER_ALL_ROWS")
    actual, expected = frame.iloc[positions][target].reset_index(drop=True), reference.iloc[positions][target].reset_index(drop=True)
    comparable = ~(missing_mask(actual) | missing_mask(expected))
    matches = comparable & actual.eq(expected).fillna(False)
    checked, passed = len(positions), int(matches.sum())
    # Uncheckable/missing reference entries cannot be counted as verified errors.
    comparable_count = int(comparable.sum())
    agreement = passed / comparable_count if comparable_count else None
    kind = audit.get("reference_kind", "known_injection_reference")
    if kind not in {"known_injection_reference", "independent_verified_labels"}:
        raise ValueError("UNKNOWN_AUDIT_REFERENCE_KIND")
    independent = kind == "independent_verified_labels"
    documented = bool(audit.get("verification_description")) if independent else True
    population_inference = independent and documented and mechanism in {"simple_random_without_replacement", "census"} and comparable_count == checked
    # Wilson is an iid/binomial approximation for SRS without replacement.
    # No finite-population correction or exact coverage guarantee is claimed.
    interval = wilson_interval(passed, checked) if population_inference and mechanism != "census" else ((agreement, agreement) if population_inference else None)
    score = agreement if population_inference else None
    mismatch = comparable_count - passed
    risks = []
    if not independent:
        risks.append("Known-injection reference agreement does not independently establish original or modified label truth.")
    if mechanism == "convenience":
        risks.append("Convenience sampling does not support population accuracy inference.")
    if independent and not documented:
        risks.append("Independent verification has been claimed without a verification protocol.")
    if independent:
        risks.append("Reference truth and random sampling must be established by the evaluator; this function does not independently verify those external assertions.")
    if independent and documented and mismatch > 0:
        risks.append("EXTERNAL_AUDIT_DISCREPANCIES_REQUIRE_REVIEW: observed disagreements with the documented external audit reference block a definitive composite, regardless of high structural scores.")
    return dimension(score=score, status="EXTERNALLY_AUDITED" if population_inference else "REFERENCE_COMPARISON_ONLY",
        evidence_class="external_reference" if independent and documented else "known_injection_reference",
        metrics={"reference_id": audit["reference_id"], "reference_kind": kind, "target_column": target,
            "sampling_mechanism": mechanism, "audited_rows": checked, "population_rows": len(frame),
            "comparable_rows": comparable_count, "passed": passed, "disagreements": mismatch,
            "reference_agreement": agreement, "screening_detected": mismatch > 0,
            "coverage": checked / len(frame), "population_inference_supported": population_inference,
            "sampling_mechanism_verified_by_model": False,
            "reference_facts_independently_verified_by_model": False,
            "interval_95": list(interval) if interval else None,
            "interval_method": "census_exact_reference_pass_rate" if interval and mechanism == "census" else "Wilson_binomial_approximation_no_FPC" if interval else None,
            "verification_description": audit.get("verification_description")},
        evidence=[{"type": kind, "reference_id": audit["reference_id"], "positions": [int(x) for x in positions]}],
        missing=[] if population_inference else ["independent_truth_reference_and_probability_sample"], risks=risks,
        meaning="Agreement with documented audit reference; reference validity remains an external assumption.")


def timeliness_evidence(frame: pd.DataFrame, temporal: dict | None) -> dict:
    if not temporal or not temporal.get("record_time_column"):
        return dimension(missing=["record_time_column", "applicability_as_of", "max_age_days"],
            meaning="Publication/update timestamps do not establish the age of observations.")
    column = temporal["record_time_column"]
    if column not in frame or "as_of" not in temporal or "max_age_days" not in temporal:
        return dimension(missing=["record_time_column", "applicability_as_of", "max_age_days"],
            meaning="Incomplete temporal evaluation contract.")
    as_of = pd.Timestamp(temporal["as_of"])
    if as_of.tzinfo is None:
        raise ValueError("TEMPORAL_AS_OF_MUST_HAVE_TIMEZONE")
    max_age = float(temporal["max_age_days"])
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError("MAX_AGE_DAYS_MUST_BE_NONNEGATIVE_FINITE")
    parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
    ages = (as_of.tz_convert("UTC") - parsed).dt.total_seconds() / 86400
    valid = parsed.notna()
    fresh = valid & ages.ge(0) & ages.le(max_age)
    coverage = int(valid.sum()) / len(frame)
    future = int((valid & ages.lt(0)).sum())
    score = int(fresh.sum()) / len(frame) if coverage == 1 else None
    return dimension(score=score, status="MEASURED" if coverage == 1 else "PARTIALLY_MEASURED",
        evidence_class="provider_declared", metrics={"record_time_column": column, "as_of": as_of.isoformat(),
            "max_age_days": max_age, "valid_timestamp_rows": int(valid.sum()), "fresh_rows": int(fresh.sum()),
            "future_timestamp_rows": future, "timestamp_coverage": coverage,
            "fresh_fraction_among_valid": int(fresh.sum()) / int(valid.sum()) if valid.any() else None,
            "median_age_days": float(ages[valid].median()) if valid.any() else None},
        evidence=[{"type": "record_timestamp_values", "column": column}],
        missing=[] if coverage == 1 else ["valid_timestamps_for_all_rows"],
        risks=["Timestamp validity and age are measured; the truth of record timestamps is not independently verified."] + (["future_record_timestamps"] if future else []),
        meaning="Freshness relative to explicit scenario horizon; timestamp assertions require external provenance.")


def _distribution_distance(candidate: pd.Series, reference: pd.Series) -> tuple[float, dict]:
    """Reference-fitted bins; candidate never changes cut points or category set."""
    if pd.api.types.is_numeric_dtype(reference.dtype):
        ref = pd.to_numeric(reference, errors="coerce")
        cand = pd.to_numeric(candidate, errors="coerce")
        finite = ref[np.isfinite(ref)]
        if not len(finite):
            raise ValueError("REFERENCE_COLUMN_HAS_NO_FINITE_VALUES")
        cut = np.unique(np.quantile(finite, np.linspace(0, 1, 11)))[1:-1]
        # Explicit below/above tails: extremes from the candidate remain visible.
        edges = np.unique(np.r_[float(finite.min()), cut, float(finite.max())])
        rb = np.searchsorted(edges, ref.to_numpy(dtype=float), side="right")
        cb = np.searchsorted(edges, cand.to_numpy(dtype=float), side="right")
        missing_bin = len(edges) + 1
        rb[~np.isfinite(ref.to_numpy(dtype=float))] = missing_bin
        cb[~np.isfinite(cand.to_numpy(dtype=float))] = missing_bin
        size = missing_bin + 1
        rp, cp = np.bincount(rb, minlength=size), np.bincount(cb, minlength=size)
        rule = {"type": "reference_quantile_numeric", "edges": edges.tolist(), "missing_bin": missing_bin}
    else:
        def values(series):
            return series.astype("string").fillna("<MISSING>").where(~missing_mask(series), "<MISSING>")
        rv, cv = values(reference), values(candidate)
        categories = sorted(rv.unique().tolist())
        mapping = {value: i for i, value in enumerate(categories)}
        rb = np.array([mapping[v] for v in rv])
        cb = np.array([mapping.get(v, len(categories)) for v in cv])
        rp, cp = np.bincount(rb, minlength=len(categories) + 1), np.bincount(cb, minlength=len(categories) + 1)
        rule = {"type": "reference_categories_with_other", "categories": categories}
    return float(jensenshannon(cp / cp.sum(), rp / rp.sum(), base=2)), rule


def representativeness_evidence(frame: pd.DataFrame, reference: dict | None) -> dict:
    if not reference or not isinstance(reference.get("frame"), pd.DataFrame):
        return dimension(missing=["explicit_reference_distribution_and_applicability"],
            meaning="No reference population/distribution was provided; no representativeness score is inferred.")
    ref = reference["frame"]
    if ref.empty or not reference.get("reference_id") or not reference.get("applicable_population"):
        return dimension(missing=["nonempty_reference", "reference_id", "applicable_population"],
            meaning="Reference scope must be explicit before distribution comparison.")
    columns = reference.get("columns")
    if not columns or len(set(columns)) != len(columns) or any(c not in frame or c not in ref for c in columns):
        raise ValueError("REFERENCE_REQUIRES_DISTINCT_SHARED_COLUMNS")
    values = {}
    for column in columns:
        distance, rule = _distribution_distance(frame[column], ref[column])
        values[column] = {"js_distance": distance, "binning": rule}
    mean_distance = float(np.mean([item["js_distance"] for item in values.values()]))
    return dimension(score=1 - mean_distance, status="REFERENCE_COMPARED", evidence_class="external_reference",
        metrics={"mean_js_distance": mean_distance, "similarity": 1 - mean_distance,
            "per_column": values, "reference_rows": len(ref), "candidate_rows": len(frame),
            "reference_id": reference["reference_id"], "applicable_population": reference["applicable_population"],
            "description": reference.get("description"), "column_count": len(columns)},
        evidence=[{"type": "explicit_reference_distribution", "reference_id": reference["reference_id"]}],
        risks=["Marginal similarity to this reference is not universal representativeness; joint/conditional shifts can be missed.",
               "Reference selection bias and sampling uncertainty are not calibrated by this point distance."],
        meaning="One minus mean marginal Jensen-Shannon distance (base2), conditional on declared reference scope.")
