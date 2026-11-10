"""Conditional decision benefits and delivery-risk values; no monetary market price.

All money-like numbers are *hypothetical scenario units*.  Quality never enters
this module: its influence is already in the saved task-model predictions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import numpy as np


CORE_VERSION = "valuation_engine-conditional-value-v1"
MODEL_STATUSES = frozenset({"VALUATION_VALID", "VALUATION_UNCERTAIN", "VALUATION_UNRELIABLE"})


def _finite(name: str, value: float, *, nonnegative: bool = False) -> float:
    result = float(value)
    if not np.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and nonnegative" if nonnegative else ""))
    return result


def _probability(value: float) -> float:
    result = _finite("q", value)
    if not 0 <= result <= 1:
        raise ValueError("q must lie in [0, 1]")
    return result


def _aligned_predictions(
    y: Sequence[int] | np.ndarray,
    prob_base: Sequence[float] | np.ndarray,
    prob_asset: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    arrays = tuple(np.asarray(item, dtype=float) for item in (y, prob_base, prob_asset))
    if any(item.ndim != 1 for item in arrays) or not len(arrays[0]):
        raise ValueError("Labels and predictions must be nonempty one-dimensional arrays")
    if len({len(item) for item in arrays}) != 1:
        raise ValueError("Base and asset predictions must align to identical evaluation observations")
    if not all(np.isfinite(item).all() for item in arrays):
        raise ValueError("Labels and probabilities must be finite")
    if not np.isin(arrays[0], [0, 1]).all():
        raise ValueError("Reference labels must be binary 0/1")
    if any(((item < 0) | (item > 1)).any() for item in arrays[1:]):
        raise ValueError("Prediction probabilities must lie in [0, 1]")
    return arrays


def decision_losses(
    y: Sequence[int] | np.ndarray,
    probabilities: Sequence[float] | np.ndarray,
    c_fp: float,
    c_fn: float,
) -> np.ndarray:
    """FP/FN loss at the prespecified Bayes threshold; assumes usable calibration.

    This is an observational prediction loss, not a causal action profit.  The
    caller must enforce model reliability/calibration conditions separately.
    """
    labels, predictions, _ = _aligned_predictions(y, probabilities, probabilities)
    fp = _finite("c_fp", c_fp, nonnegative=True)
    fn = _finite("c_fn", c_fn, nonnegative=True)
    total = _finite("total decision cost", fp + fn, nonnegative=True)
    if total <= 0:
        raise ValueError("At least one decision cost must be positive")
    actions = predictions >= fp / total
    return fp * (actions & (labels == 0)) + fn * (~actions & (labels == 1))


def paired_decision_gains(
    y: Sequence[int] | np.ndarray,
    prob_base: Sequence[float] | np.ndarray,
    prob_asset: Sequence[float] | np.ndarray,
    c_fp: float,
    c_fn: float,
) -> np.ndarray:
    """Aligned per-observation base loss minus augmented-model loss for bootstrap."""
    labels, base, asset = _aligned_predictions(y, prob_base, prob_asset)
    return decision_losses(labels, base, c_fp, c_fn) - decision_losses(labels, asset, c_fp, c_fn)


@dataclass(frozen=True)
class DecisionEconomics:
    delta_utility: float
    log_loss_base: float
    log_loss_asset: float
    risk_base: float
    risk_asset: float
    delta_risk: float
    benefit_success: float
    threshold: float
    c_fp: float
    c_fn: float
    n_per_period: float
    periods: float
    n_total: float
    observations: int
    assumptions: str = "HYPOTHETICAL_DECISION_LOSS_PARAMETERS_NOT_CAUSAL_PROFIT"
    version: str = CORE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_economics(
    y: Sequence[int] | np.ndarray,
    prob_base: Sequence[float] | np.ndarray,
    prob_asset: Sequence[float] | np.ndarray,
    c_fp: float,
    c_fn: float,
    n_per_period: float,
    periods: float = 1,
) -> DecisionEconomics:
    """B_success = N_total * delta_R, where N_total = N_per_period * periods.

    Costs/scale must be frozen before inspecting evaluation outcomes.  Fixed and
    operational costs are not silently subtracted here; aggregate them explicitly
    in the `fixed_cost` argument of :func:`risk_adjusted_value`.
    """
    labels, base, asset = _aligned_predictions(y, prob_base, prob_asset)
    fp = _finite("c_fp", c_fp, nonnegative=True)
    fn = _finite("c_fn", c_fn, nonnegative=True)
    n = _finite("n_per_period", n_per_period, nonnegative=True)
    t = _finite("periods", periods, nonnegative=True)
    base_loss = decision_losses(labels, base, fp, fn)
    asset_loss = decision_losses(labels, asset, fp, fn)
    # Fixed numerical clipping matches the ordinary binary log-loss convention.
    eps = np.finfo(float).eps
    def log_loss(probabilities: np.ndarray) -> float:
        clipped = np.clip(probabilities, eps, 1 - eps)
        return float(np.mean(-labels * np.log(clipped) - (1 - labels) * np.log1p(-clipped)))
    ll_base, ll_asset = log_loss(base), log_loss(asset)
    rb, ra = float(base_loss.mean()), float(asset_loss.mean())
    n_total = _finite("n_total", n * t, nonnegative=True)
    benefit = _finite("benefit_success", n_total * (rb - ra))
    return DecisionEconomics(ll_base - ll_asset, ll_base, ll_asset, rb, ra, rb - ra,
                             benefit, fp / (fp + fn), fp, fn, n, t, n_total, len(labels))


def risk_adjusted_value(
    q: float,
    benefit_success: float,
    benefit_failure: float,
    fixed_cost: float,
) -> float:
    """W = q*B_success + (1-q)*B_failure - C_fixed, before licence price.

    Negative conditional benefits and values are preserved.  This function has
    no quality multiplier, no observed future delivery label, and no rounding.
    """
    probability = _probability(q)
    bs = _finite("benefit_success", benefit_success)
    bf = _finite("benefit_failure", benefit_failure)
    cost = _finite("fixed_cost", fixed_cost, nonnegative=True)
    return _finite("value", probability * bs + (1 - probability) * bf - cost)


def conditional_value_interval(
    q: float,
    benefit_success_draws: Sequence[float] | np.ndarray,
    benefit_failure: float,
    fixed_cost: float,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Percentiles of paired-evaluation bootstrap benefits at a *fixed* q.

    These are not full real-world confidence limits or calibrated credit risk
    bounds.  Asset allocation, estimated q, and economic parameters are held
    fixed.  Stress changes of q must be reported separately from this interval.
    """
    probability = _probability(q)
    bf = _finite("benefit_failure", benefit_failure)
    cost = _finite("fixed_cost", fixed_cost, nonnegative=True)
    coverage = _finite("confidence", confidence)
    if not 0 < coverage < 1:
        raise ValueError("confidence must lie strictly between zero and one")
    draws = np.asarray(benefit_success_draws, dtype=float)
    if draws.ndim != 1 or len(draws) < 20 or not np.isfinite(draws).all():
        raise ValueError("At least 20 finite one-dimensional bootstrap benefit draws are required")
    values = probability * draws + (1 - probability) * bf - cost
    if not np.isfinite(values).all():
        raise ValueError("Bootstrap value arithmetic overflowed")
    tail = (1 - coverage) / 2
    lower, upper = (float(x) for x in np.quantile(values, [tail, 1 - tail]))
    return {
        "lower": lower, "upper": upper, "confidence": coverage,
        "fixed_q": probability, "replicates": len(draws),
        "crosses_zero": bool(lower <= 0 <= upper),
        "scope": "PAIRED_EVALUATION_BOOTSTRAP_CONDITIONAL_ON_FIXED_Q_ASSET_AND_ECONOMIC_PARAMETERS",
        "excluded_uncertainty": ["credit_probability", "asset_allocation", "economic_parameters", "distribution_shift"],
        "is_full_real_world_confidence_interval": False,
        "version": CORE_VERSION,
    }


def assess_status(
    *,
    value: float,
    benefit_success: float,
    evidence_verified: bool,
    policy_verified: bool,
    model_status: str = "VALUATION_VALID",
    conditional_lower: float | None = None,
    conditional_upper: float | None = None,
    model_reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """Keep evidence, policy, model, and economics as independent state axes.

    Point-positive UNCERTAIN valuations may support a labelled research policy;
    formal eligibility additionally requires VALID and a positive conditional
    lower bound.  Failure of evidence/policy never becomes a statistical failure
    observation.  A nonpositive successful-delivery benefit blocks purchase even
    if an unusual positive failure payment made W positive.
    """
    expected = _finite("value", value)
    success = _finite("benefit_success", benefit_success)
    if model_status not in MODEL_STATUSES:
        raise ValueError(f"Unknown model_status: {model_status}")
    if not isinstance(evidence_verified, (bool, np.bool_)) or not isinstance(policy_verified, (bool, np.bool_)):
        raise ValueError("Evidence and policy verification must be explicit booleans")
    if (conditional_lower is None) != (conditional_upper is None):
        raise ValueError("Conditional interval must have both bounds or neither")
    lower = upper = None
    if conditional_lower is not None:
        lower = _finite("conditional_lower", conditional_lower)
        upper = _finite("conditional_upper", conditional_upper)
        if lower > upper:
            raise ValueError("Conditional interval lower bound exceeds upper bound")
    reasons = list(model_reasons)
    if not evidence_verified:
        reasons.append("EVIDENCE_INSUFFICIENT")
    if not policy_verified:
        reasons.append("POLICY_NOT_VERIFIED")
    if model_status != "VALUATION_VALID":
        reasons.append(model_status)
    if expected <= 0:
        reasons.append("NONPOSITIVE_EXPECTED_VALUE")
    if success <= 0:
        reasons.append("NONPOSITIVE_SUCCESS_BENEFIT")
    if lower is None:
        reasons.append("CONDITIONAL_INTERVAL_UNAVAILABLE")
    elif lower <= 0:
        reasons.append("CONDITIONAL_LOWER_BOUND_NONPOSITIVE")
    eligible = bool(evidence_verified and policy_verified and model_status != "VALUATION_UNRELIABLE"
                    and expected > 0 and success > 0)
    formal = bool(eligible and model_status == "VALUATION_VALID" and lower is not None and lower > 0)
    economic = ("POSITIVE_EXPECTED_VALUE" if expected > 0 else
                "NEGATIVE_EXPECTED_VALUE" if expected < 0 else "ZERO_EXPECTED_VALUE")
    return {
        "evidence_status": "VERIFIED" if evidence_verified else "EVIDENCE_INSUFFICIENT",
        "policy_status": "VERIFIED" if policy_verified else "POLICY_NOT_VERIFIED",
        "model_status": model_status,
        "economic_status": economic,
        "success_benefit_positive": bool(success > 0),
        "interval_crosses_zero": None if lower is None else bool(lower <= 0 <= upper),
        "research_recommendation_eligible": eligible,
        "formal_recommendation_eligible": formal,
        "reasons": list(dict.fromkeys(reasons)),
    }


def compare_values(candidates: Sequence[Mapping[str, Any]], policy: str = "point") -> dict[str, Any]:
    """Select by point W or conditional lower bound; retain a no-purchase option.

    Required candidate fields: asset_id, version, value, benefit_success,
    evidence_verified, policy_verified.  Optional fields: model_status,
    conditional_lower/upper.  Conservative policy requires formal eligibility.
    Distinct versions remain distinct alternatives; duplicate alternatives fail.
    """
    if policy not in {"point", "conservative"}:
        raise ValueError("policy must be point or conservative")
    seen: set[tuple[str, str]] = set()
    rows = []
    for item in candidates:
        raw_identity = (item["asset_id"], item["version"])
        identity = tuple(str(value) for value in raw_identity)
        if any(value is None for value in raw_identity) or not all(identity) or identity in seen:
            raise ValueError("Candidates must have nonempty, unique (asset_id, version) identities")
        seen.add(identity)
        status = assess_status(
            value=item["value"], benefit_success=item["benefit_success"],
            evidence_verified=item["evidence_verified"], policy_verified=item["policy_verified"],
            model_status=item.get("model_status", "VALUATION_VALID"),
            conditional_lower=item.get("conditional_lower"), conditional_upper=item.get("conditional_upper"),
            model_reasons=item.get("model_reasons", ()),
        )
        eligible = status["research_recommendation_eligible"] if policy == "point" else status["formal_recommendation_eligible"]
        raw_score = item["value"] if policy == "point" else item.get("conditional_lower")
        score = None if raw_score is None else float(raw_score)
        rows.append({"asset_id": identity[0], "version": identity[1], "value": float(item["value"]),
                     "score": score, "eligible": bool(eligible), "status": status})
    eligible_rows = [item for item in rows if item["eligible"]]
    # A stable, declared identity tie break avoids input-order-driven choices.
    eligible_rows.sort(key=lambda item: (-item["score"], item["asset_id"], item["version"]))
    selected = eligible_rows[0] if eligible_rows else None
    blocked = any(item["status"]["evidence_status"] != "VERIFIED" or
                  item["status"]["policy_status"] != "VERIFIED" or
                  item["status"]["model_status"] == "VALUATION_UNRELIABLE" for item in rows)
    return {
        "policy": policy,
        "selected_asset_id": None if selected is None else selected["asset_id"],
        "selected_version": None if selected is None else selected["version"],
        "decision": ("RESEARCH_RECOMMENDATION" if selected is not None and policy == "point" else
                     "FORMAL_RECOMMENDATION" if selected is not None else
                     "NO_ELIGIBLE_RECOMMENDATION" if blocked else "NO_PURCHASE"),
        "selected_value": 0.0 if selected is None else selected["value"],
        "formal_recommendation": bool(selected is not None and selected["status"]["formal_recommendation_eligible"]),
        "alternatives": rows,
        "tie_break": "descending score, then asset_id, then version",
        "version": CORE_VERSION,
    }
