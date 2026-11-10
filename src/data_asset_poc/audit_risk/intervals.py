"""Exact finite-population inference for a single precommitted SRS audit.

These bounds concern a fixed binary finite population relative to a specified
reference. They do not turn reference agreement into factual truth.
"""
from __future__ import annotations

from functools import lru_cache
import math
from typing import Any

from scipy.stats import beta, hypergeom


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}_MUST_BE_INTEGER_AT_LEAST_{minimum}")
    return value


def _probability(value: Any, name: str, *, open_interval: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}_MUST_BE_FINITE_PROBABILITY")
    number = float(value)
    if not math.isfinite(number) or not (0 < number < 1 if open_interval else 0 <= number <= 1):
        raise ValueError(f"{name}_MUST_BE_FINITE_PROBABILITY")
    return number


def finite_population_interval(population_size: int, sample_size: int, errors: int,
                               alpha: float = 0.05) -> dict[str, Any]:
    """Invert both exact hypergeometric tails (alpha/2 each).

    Returned integer error-total limits are attainable finite-population values.
    Boundary tail equality is included, making the interval conservative. n=0
    returns [0, N]; n=N gives the observed census exactly.
    """
    total = _integer(population_size, "POPULATION_SIZE", 1)
    n = _integer(sample_size, "SAMPLE_SIZE")
    e = _integer(errors, "ERRORS")
    a = _probability(alpha, "ALPHA", open_interval=True)
    if n > total or e > n:
        raise ValueError("REQUIRE_0_LE_ERRORS_LE_SAMPLE_SIZE_LE_POPULATION_SIZE")
    lower, upper = _finite_population_error_totals(total, n, e, a)
    return {"lower": lower / total, "upper": upper / total,
            "error_total_lower": lower, "error_total_upper": upper,
            "population_size": total, "sample_size": n, "errors": e,
            "alpha": a, "tail_risk_each": a / 2,
            "method": "exact_hypergeometric_two_sided_tail_inversion"}


@lru_cache(maxsize=200000)
def _finite_population_error_totals(total: int, n: int, e: int, a: float) -> tuple[int, int]:
    """Cache immutable values only, so callers cannot contaminate later runs."""
    if n == 0:
        lower, upper = 0, total
    elif n == total:
        lower = upper = e
    else:
        tail = a / 2
        # The feasible K range is [e, N-n+e]. The survival tail increases
        # monotonically with K, while the CDF decreases monotonically.
        lo, hi = e, total - n + e
        while lo < hi:
            mid = (lo + hi) // 2
            if hypergeom.sf(e - 1, total, mid, n) >= tail:
                hi = mid
            else:
                lo = mid + 1
        lower = lo
        lo, hi = e, total - n + e
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if hypergeom.cdf(e, total, mid, n) >= tail:
                lo = mid
            else:
                hi = mid - 1
        upper = lo
    return lower, upper


def clopper_pearson_interval(sample_size: int, errors: int,
                             alpha: float = 0.05) -> dict[str, Any]:
    """Classical exact-binomial comparator, without finite-population gain."""
    n = _integer(sample_size, "SAMPLE_SIZE")
    e = _integer(errors, "ERRORS")
    a = _probability(alpha, "ALPHA", open_interval=True)
    if e > n:
        raise ValueError("ERRORS_EXCEED_SAMPLE_SIZE")
    lower = 0.0 if e == 0 else float(beta.ppf(a / 2, e, n - e + 1))
    upper = 1.0 if e == n else float(beta.ppf(1 - a / 2, e + 1, n - e))
    return {"lower": lower, "upper": upper, "sample_size": n, "errors": e,
            "alpha": a, "tail_risk_each": a / 2, "method": "clopper_pearson_binomial"}


def interval_decision(lower: float, upper: float, epsilon: float) -> str:
    """Accept <=epsilon, reject >epsilon; the boundary itself is acceptable."""
    lo = _probability(lower, "LOWER")
    hi = _probability(upper, "UPPER")
    eps = _probability(epsilon, "EPSILON")
    if lo > hi:
        raise ValueError("LOWER_EXCEEDS_UPPER")
    if hi <= eps:
        return "ACCEPT_CONDITIONALLY"
    if lo > eps:
        return "REJECT_CONFIRMED"
    return "NEED_MORE_EVIDENCE"


def assess_fixed_sample(*, population_size: int, sample_size: int, errors: int,
                        unresolved: int = 0, epsilon: float, alpha: float = 0.05,
                        reference_error_upper: float = 0.0,
                        reference_risk_beta: float = 0.0) -> dict[str, Any]:
    """Mathematical counts kernel, not an evidence-authentication function.

    All sampled positions remain in n even if unresolved. Taking the union of
    confidence intervals over their possible outcomes yields a conservative
    envelope. A population bound rho on reference errors widens the true-error
    interval by the triangle inequality. If rho itself can fail with risk beta,
    audit tail risk is reduced to alpha-beta (union bound, no independence
    needed). Its factual validity must be established outside this function.
    """
    total = _integer(population_size, "POPULATION_SIZE", 1)
    n = _integer(sample_size, "SAMPLE_SIZE")
    e = _integer(errors, "ERRORS")
    missing = _integer(unresolved, "UNRESOLVED")
    eps = _probability(epsilon, "EPSILON")
    a = _probability(alpha, "ALPHA", open_interval=True)
    rho = _probability(reference_error_upper, "REFERENCE_ERROR_UPPER")
    ref_beta = _probability(reference_risk_beta, "REFERENCE_RISK_BETA")
    if n > total or e + missing > n:
        raise ValueError("INVALID_FIXED_SAMPLE_COUNTS")
    if ref_beta >= a:
        raise ValueError("REFERENCE_RISK_BETA_MUST_BE_LESS_THAN_ALPHA")
    audit_alpha = a - ref_beta
    low = finite_population_interval(total, n, e, audit_alpha)
    high = finite_population_interval(total, n, e + missing, audit_alpha)
    cp_low = clopper_pearson_interval(n, e, audit_alpha)
    cp_high = clopper_pearson_interval(n, e + missing, audit_alpha)
    lower, upper = max(0.0, low["lower"] - rho), min(1.0, high["upper"] + rho)
    cp_lower, cp_upper = max(0.0, cp_low["lower"] - rho), min(1.0, cp_high["upper"] + rho)
    return {"population_size": total, "sample_size": n,
            "n": n - missing, "e": e, "unresolved": missing,
            "sample_error_fraction_among_comparable": e / (n - missing) if n > missing else None,
            "epsilon": eps, "alpha": a, "audit_alpha": audit_alpha,
            "reference_error_upper": rho, "reference_risk_beta": ref_beta,
            "disagreement_interval": {"lower": low["lower"], "upper": high["upper"],
                "error_total_lower": low["error_total_lower"], "error_total_upper": high["error_total_upper"]},
            "error_rate_interval": {"lower": lower, "upper": upper},
            "statistical_decision": interval_decision(lower, upper, eps),
            "binomial_comparator": {"lower": cp_lower, "upper": cp_upper,
                "decision": interval_decision(cp_lower, cp_upper, eps), "method": cp_low["method"]},
            "interval_method": low["method"], "unresolved_policy": "worst_case_completion_without_dropping_sampled_positions",
            "coverage_claim": "at_least_1_minus_alpha_conditional_on_fixed_SRS_frame_and_valid_reference_bound",
            "false_accept_tail_bound": audit_alpha / 2 + ref_beta,
            "false_reject_tail_bound": audit_alpha / 2 + ref_beta,
            "not_a_truth_probability": True}
