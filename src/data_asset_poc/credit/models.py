"""Contract-level delivery probability estimators over previously verified evidence.

The evidence layer must authenticate and resolve contracts before calling this
module.  These estimators validate history structure and chronology, but do not
verify signatures, turn data quality into outcomes, or establish factual truth.
Each visible contract can contribute at most one Bernoulli outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import math
from numbers import Real
from typing import Any

from scipy.stats import beta as beta_distribution


ALGORITHM_VERSION = "simulation-credit-1.0.0"
METHODS = frozenset({"naive", "beta", "decay", "hierarchy", "decay_hierarchy"})
_DAY_SECONDS = 86400.0


def _parse_time(value: str, field: str) -> datetime:
    """Parse an explicit timezone, normalizing all times to UTC."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty timezone-aware ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not an ISO 8601 datetime: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a positive finite number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return converted


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _weight(event_time: datetime, as_of: datetime, half_life: float | None) -> float:
    if half_life is None:
        return 1.0
    # Future timestamps are excluded before reaching this function.
    age_days = (as_of - event_time).total_seconds() / _DAY_SECONDS
    return math.exp(-math.log(2.0) * age_days / half_life)


def predict_credit(
    history: Sequence[Mapping[str, Any]],
    provider_id: str,
    asset_id: str,
    as_of_time: str,
    method: str = "beta",
    alpha0: float = 1.0,
    beta0: float = 1.0,
    half_life_days: float | None = None,
    kappa: float = 2.0,
) -> dict[str, Any]:
    """Estimate success probability for one provider/asset at ``as_of_time``.

    Required history fields are ``contract_id``, ``provider_id``, ``asset_id``,
    binary ``outcome``, ``event_time`` and ``observed_at``. Both timestamps must
    be no later than the query time. Observations made later are unavailable,
    even if their event happened in the past. Timezones must be explicit.

    ``naive`` uses the raw asset success fraction, with prior mean as a declared
    cold-start default and no parameter interval. ``beta`` uses ordinary
    Beta-Bernoulli updates. ``decay`` uses event-age exponential pseudo-counts.
    A ``None`` half-life means no decay, including for decay method variants.

    ``hierarchy`` and ``decay_hierarchy`` compute an empirical prior center from
    *other assets of the same provider*, smoothed by Beta(alpha0, beta0), then
    cap the transferred prior strength at fixed kappa. With no positive-weight
    provider history, the original default prior is retained. This is a limited
    empirical prior, not full hierarchical Bayesian inference.

    Reported 95% Beta intervals for decay and empirical-prior variants are
    model-conditional approximations. They do not account for event correlation,
    drift, prior-center estimation uncertainty, or unobserved missing outcomes.
    Weighted history and Kish effective sample size are descriptive quantities;
    neither repairs statistical dependence between different contracts.

    Duplicate visible contract IDs raise an error even when outcomes agree.
    The caller must resolve observations to one verified result per contract.
    """
    if method not in METHODS:
        raise ValueError(f"Unknown credit method {method!r}; expected one of {sorted(METHODS)}")
    _identifier(provider_id, "provider_id")
    _identifier(asset_id, "asset_id")
    as_of = _parse_time(as_of_time, "as_of_time")
    alpha0 = _positive_finite(alpha0, "alpha0")
    beta0 = _positive_finite(beta0, "beta0")
    kappa = _positive_finite(kappa, "kappa")
    if half_life_days is not None:
        half_life_days = _positive_finite(half_life_days, "half_life_days")
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("history must be a sequence of verified contract records")

    use_decay = method in {"decay", "decay_hierarchy"}
    use_provider = method in {"hierarchy", "decay_hierarchy"}
    active_half_life = half_life_days if use_decay else None
    seen: set[str] = set()
    target_weights: list[float] = []
    weighted_successes: list[float] = []
    weighted_failures: list[float] = []
    provider_successes: list[float] = []
    provider_failures: list[float] = []
    successes = failures = provider_count = 0

    for index, record in enumerate(history):
        if not isinstance(record, Mapping):
            raise ValueError(f"history[{index}] must be a mapping")
        try:
            event_time = _parse_time(record["event_time"], f"history[{index}].event_time")
            observed_at = _parse_time(record["observed_at"], f"history[{index}].observed_at")
        except KeyError as exc:
            raise ValueError(f"history[{index}] is missing required field {exc.args[0]!r}") from exc

        # Future records (including their outcomes or duplicate IDs) must not
        # influence a past query. Resolve only evidence visible at the cutoff.
        if event_time > as_of or observed_at > as_of:
            continue
        if observed_at < event_time:
            raise ValueError(f"history[{index}].observed_at precedes event_time")
        try:
            contract_id = _identifier(record["contract_id"], f"history[{index}].contract_id")
            record_provider = _identifier(record["provider_id"], f"history[{index}].provider_id")
            record_asset = _identifier(record["asset_id"], f"history[{index}].asset_id")
            outcome = record["outcome"]
        except KeyError as exc:
            raise ValueError(f"history[{index}] is missing required field {exc.args[0]!r}") from exc
        if isinstance(outcome, bool) or not isinstance(outcome, Real) or outcome not in (0, 1):
            raise ValueError(f"history[{index}].outcome must be numeric 0 or 1")
        if record.get("verified") is False:
            raise ValueError(f"history[{index}] explicitly marks evidence as unverified")
        if "status" in record and record["status"] not in {"SUCCESS", "FAILURE", "RESOLVED"}:
            raise ValueError(f"history[{index}] is not a resolved binary contract result")
        if contract_id in seen:
            raise ValueError(f"Duplicate visible contract_id {contract_id!r}; resolve evidence before updating")
        seen.add(contract_id)
        if record_provider != provider_id:
            continue
        weight = _weight(event_time, as_of, active_half_life)
        if record_asset == asset_id:
            target_weights.append(weight)
            successes += int(outcome)
            failures += 1 - int(outcome)
            weighted_successes.append(weight * outcome)
            weighted_failures.append(weight * (1 - outcome))
        elif use_provider:
            provider_count += 1
            provider_successes.append(weight * outcome)
            provider_failures.append(weight * (1 - outcome))

    sum_successes = math.fsum(weighted_successes)
    sum_failures = math.fsum(weighted_failures)
    effective_history = math.fsum(target_weights)
    squared_weights = math.fsum(w * w for w in target_weights)
    # The ratio below can have an underflowed denominator when all weights are
    # tiny. Rescaling weights preserves the ratio and avoids reporting zero ESS.
    max_weight = max(target_weights, default=0.0)
    if max_weight > 0:
        scaled = [w / max_weight for w in target_weights]
        kish_ess = math.fsum(scaled) ** 2 / math.fsum(w * w for w in scaled)
    else:
        kish_ess = 0.0
    provider_weighted_successes = math.fsum(provider_successes)
    provider_weighted_failures = math.fsum(provider_failures)
    provider_effective_history = provider_weighted_successes + provider_weighted_failures
    prior_alpha, prior_beta = alpha0, beta0
    provider_mean = None
    if use_provider and provider_effective_history > 0:
        provider_mean = (alpha0 + provider_weighted_successes) / (
            alpha0 + beta0 + provider_effective_history
        )
        prior_alpha = kappa * provider_mean
        prior_beta = kappa * (1.0 - provider_mean)

    n_history = successes + failures
    if method == "naive":
        probability = successes / n_history if n_history else alpha0 / (alpha0 + beta0)
        alpha = beta = lower = upper = None
        interval_kind = "not_available_for_naive_fraction"
    else:
        alpha = prior_alpha + sum_successes
        beta = prior_beta + sum_failures
        probability = alpha / (alpha + beta)
        lower, upper = (float(value) for value in beta_distribution.ppf([0.025, 0.975], alpha, beta))
        interval_kind = (
            "beta_credible_interval_under_stationary_independent_bernoulli_model"
            if method == "beta"
            else "approximate_model_conditional_beta_interval"
        )

    return {
        "provider_id": provider_id,
        "asset_id": asset_id,
        "as_of_time": as_of.isoformat(),
        "method": method,
        "probability": float(probability),
        "lower": lower,
        "upper": upper,
        "confidence_level": 0.95 if method != "naive" else None,
        "interval_kind": interval_kind,
        "alpha": alpha,
        "beta": beta,
        "prior_alpha": prior_alpha,
        "prior_beta": prior_beta,
        "default_prior_alpha": alpha0,
        "default_prior_beta": beta0,
        "n_history": n_history,
        "successes": successes,
        "failures": failures,
        "weighted_successes": sum_successes,
        "weighted_failures": sum_failures,
        "effective_history": effective_history,
        "kish_effective_sample_size": kish_ess,
        "sum_squared_weights": squared_weights,
        "provider_history_count": provider_count,
        "provider_effective_history": provider_effective_history,
        "provider_prior_center": provider_mean,
        "half_life_days": active_half_life,
        "kappa": kappa if use_provider else None,
        "evidence_status": "VERIFIED_HISTORY_REQUIRED_NOT_REVERIFIED_BY_MODEL",
        "algorithm_version": ALGORITHM_VERSION,
    }
