"""Predeclared, non-trained fusion baselines with explicit missing policies."""
from __future__ import annotations

import math
from typing import Mapping

DIMENSIONS = ("structure", "provenance", "accuracy", "timeliness", "representativeness")
WEIGHT_PROFILES = {
    "equal": dict(zip(DIMENSIONS, (0.2, 0.2, 0.2, 0.2, 0.2))),
    "structure_heavy": dict(zip(DIMENSIONS, (0.4, 0.15, 0.15, 0.15, 0.15))),
    "accuracy_heavy": dict(zip(DIMENSIONS, (0.15, 0.15, 0.4, 0.15, 0.15))),
    "provenance_heavy": dict(zip(DIMENSIONS, (0.15, 0.4, 0.15, 0.15, 0.15))),
}


def get_weights(profile: str = "equal", weights: Mapping[str, float] | None = None) -> dict[str, float]:
    if weights is None:
        if profile not in WEIGHT_PROFILES:
            raise ValueError("UNKNOWN_WEIGHT_PROFILE")
        return dict(WEIGHT_PROFILES[profile])
    if set(weights) != set(DIMENSIONS):
        raise ValueError("WEIGHTS_REQUIRE_ALL_FIVE_ASSESSMENT_DIMENSIONS")
    result = {k: float(v) for k, v in weights.items()}
    if any(not math.isfinite(v) or v < 0 for v in result.values()) or not math.isclose(sum(result.values()), 1):
        raise ValueError("WEIGHTS_MUST_BE_NONNEGATIVE_AND_SUM_TO_ONE")
    return result


def partial_index(scores: Mapping[str, float | None], weights: Mapping[str, float]) -> dict:
    """Identification set of a *defined index*, not a probability interval."""
    known = {k: float(scores[k]) for k in weights if scores.get(k) is not None}
    lower = sum(weights[k] * value for k, value in known.items())
    coverage = sum(weights[k] for k in known)
    return {"interval_lower": lower, "interval_upper": min(1.0, lower + 1 - coverage),
            "evidence_coverage": coverage,
            "observed_projection": lower / coverage if coverage else None,
            "unknown_dimensions": [k for k in weights if k not in known]}


def compute_baselines(quality: dict, scores: Mapping[str, float | None], weights: Mapping[str, float]) -> dict:
    criteria = [quality.get(k) for k in ("completeness", "uniqueness", "format_validity", "consistency")]
    # Static_quality freezes the equal four-item mean and declines when a denominator is
    # absent. Reuse those exact metric values rather than substituting zero.
    static_quality = sum(criteria) / 4 if all(v is not None for v in criteria) else None
    observed = {k: float(v) for k, v in scores.items() if v is not None and k in weights}
    # Fixed ideal TOPSIS avoids dependence on the other candidates in the batch.
    # The main comparator renormalizes observed criteria; an additional explicitly
    # optimistic imputation illustrates the danger of rewarding absent evidence.
    # Euclidean distance uses sqrt(weight), hence weights multiply squared gaps.
    observed_weight = sum(weights[k] for k in observed)
    observed_plus = math.sqrt(sum(weights[k] * (1 - observed[k]) ** 2 / observed_weight for k in observed)) if observed_weight else None
    observed_minus = math.sqrt(sum(weights[k] * observed[k] ** 2 / observed_weight for k in observed)) if observed_weight else None
    observed_topsis = (observed_minus / (observed_plus + observed_minus)
                       if observed_weight and observed_plus + observed_minus else None)
    filled = {k: float(scores[k]) if scores.get(k) is not None else 1.0 for k in weights}
    d_plus = math.sqrt(sum(weights[k] * (1 - filled[k]) ** 2 for k in weights))
    d_minus = math.sqrt(sum(weights[k] * filled[k] ** 2 for k in weights))
    topsis = d_minus / (d_plus + d_minus) if d_plus + d_minus else 0.5
    no_source = {k: weights[k] for k in weights if k != "provenance" and k in observed}
    total = sum(no_source.values())
    return {
        "static_quality_static_equal_quality": {"score": static_quality, "missing_policy": "decline_if_any_of_four_unknown"},
        "fixed_ideal_topsis": {"score": observed_topsis, "missing_policy": "renormalize_observed_criteria_weights",
            "observed_weight": observed_weight, "positive_ideal": 1.0, "negative_ideal": 0.0,
            "warning": "Different coverage remains incomparable; this conventional projection does not refuse incomplete evidence."},
        "optimistic_unknown_topsis": {"score": topsis, "missing_policy": "optimistic_unknown_equals_one",
            "positive_ideal": 1.0, "negative_ideal": 0.0,
            "warning": "Unknown is not verified; this baseline can reward absent evidence."},
        "no_provenance_wsm": {"score": sum(no_source[k] * observed[k] for k in no_source) / total if total else None,
            "missing_policy": "renormalize_observed_nonprovenance_weights",
            "warning": "Conditional projection ignores missing evidence and cannot be a definitive assessment."},
    }
