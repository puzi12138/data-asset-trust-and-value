"""Precommitted-budget audits with valid conditional finite-population bounds.

The candidate chooses its confirmation allocation from a small pilot.  Pilot
records are observed finite-population units, not an SRS estimate of the rest.
Conditional on the pilot, each remaining stratum is sampled by an independent
uniform permutation prefix.  This permits the existing exact HG kernel to be
used on each residual population, with a Bonferroni union bound.

This module receives audit outcomes only by calling ``audit(position)``.  It
does not receive injected error positions or any complete reference labels.
Evidence authentication and dataset/version binding belong to the caller.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
import json
from math import floor, isfinite
from numbers import Integral, Real
from time import perf_counter
from typing import Any

import numpy as np

from ..audit_risk.intervals import finite_population_interval, interval_decision

METHOD_VERSION = "reference_workbench-budget-residual-hg-v1"
STRATEGIES = ("srs", "fixed_random", "proportional", "risk_priority", "candidate")
PILOT_PER_STRATUM = 5
AuditOracle = Callable[[int], str | Mapping[str, Any]]


def _int(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name}_MUST_BE_INTEGER_AT_LEAST_{minimum}")
    return int(value)


def _prob(value: Any, name: str, *, interior: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name}_MUST_BE_FINITE_PROBABILITY")
    number = float(value)
    if not isfinite(number) or not (0 < number < 1 if interior else 0 <= number <= 1):
        raise ValueError(f"{name}_MUST_BE_FINITE_PROBABILITY")
    return number


def stratified_residual_interval(
    *, populations: Sequence[int], pilot_sizes: Sequence[int],
    pilot_errors: Sequence[int], pilot_unknown: Sequence[int],
    confirmation_sizes: Sequence[int], confirmation_errors: Sequence[int],
    confirmation_unknown: Sequence[int], alpha: float = 0.05,
    reference_error_upper: float = 0.0, reference_risk_beta: float = 0.0,
) -> dict[str, Any]:
    """Bound the total errors, adding known pilot units to residual HG bounds.

    Pilot identities and values may inform confirmation sample sizes.  Once
    those sizes have been chosen, each residual sample must be a fresh SRS
    without replacement.  No interim confirmation outcomes may change them.
    Unknown outcomes remain in the denominator and are completed worst-case.
    ``rho`` and ``beta`` are external assumptions, not estimates made here.
    """
    h_count = len(populations)
    vectors = (pilot_sizes, pilot_errors, pilot_unknown, confirmation_sizes,
               confirmation_errors, confirmation_unknown)
    if h_count < 1 or any(len(v) != h_count for v in vectors):
        raise ValueError("NONEMPTY_EQUAL_LENGTH_STRATUM_VECTORS_REQUIRED")
    a = _prob(alpha, "ALPHA", interior=True)
    rho = _prob(reference_error_upper, "REFERENCE_ERROR_UPPER")
    beta = _prob(reference_risk_beta, "REFERENCE_RISK_BETA")
    if beta >= a:
        raise ValueError("REFERENCE_RISK_BETA_MUST_BE_LESS_THAN_ALPHA")
    per_stratum_alpha = (a - beta) / h_count
    total = lower_total = upper_total = 0
    details: list[dict[str, Any]] = []
    for h in range(h_count):
        n = _int(populations[h], "POPULATION", 1)
        p = _int(pilot_sizes[h], "PILOT_SIZE")
        pe = _int(pilot_errors[h], "PILOT_ERRORS")
        pu = _int(pilot_unknown[h], "PILOT_UNKNOWN")
        m = _int(confirmation_sizes[h], "CONFIRMATION_SIZE")
        e = _int(confirmation_errors[h], "CONFIRMATION_ERRORS")
        u = _int(confirmation_unknown[h], "CONFIRMATION_UNKNOWN")
        if p > n or pe + pu > p or m > n - p or e + u > m:
            raise ValueError("INVALID_STRATUM_COUNTS")
        remaining = n - p
        if remaining:
            lo = finite_population_interval(remaining, m, e, per_stratum_alpha)
            hi = finite_population_interval(remaining, m, e + u, per_stratum_alpha)
            kl, ku = lo["error_total_lower"], hi["error_total_upper"]
        else:
            kl = ku = 0
        hl, hu = pe + kl, pe + pu + ku
        total += n
        lower_total += hl
        upper_total += hu
        details.append({"stratum": h, "population_size": n,
                        "pilot_size": p, "pilot_errors": pe, "pilot_unknown": pu,
                        "residual_population_size": remaining,
                        "confirmation_size": m, "confirmation_errors": e,
                        "confirmation_unknown": u, "alpha": per_stratum_alpha,
                        "error_total_lower": hl, "error_total_upper": hu})
    lower = max(0.0, lower_total / total - rho)
    upper = min(1.0, upper_total / total + rho)
    return {"lower": lower, "upper": upper,
            "disagreement_lower": lower_total / total,
            "disagreement_upper": upper_total / total,
            "error_total_lower": lower_total, "error_total_upper": upper_total,
            "population_size": total, "alpha": a,
            "audit_alpha": a - beta, "per_stratum_alpha": per_stratum_alpha,
            "reference_error_upper": rho, "reference_risk_beta": beta,
            "strata": details,
            "method": "pilot_exact_plus_bonferroni_residual_hypergeometric",
            "coverage_scope": "conditional_on_pilot_fixed_allocation_residual_SRS_and_valid_reference_bound",
            "false_accept_tail_bound": (a - beta) / 2 + beta,
            "false_reject_tail_bound": (a - beta) / 2 + beta}


def _apportion(budget: int, capacities: Sequence[int], weights: Sequence[float]) -> list[int]:
    """Fixed, deterministic weighted allocation with capacity constraints."""
    result = [0] * len(capacities)
    for _ in range(budget):
        available = [h for h, capacity in enumerate(capacities) if result[h] < capacity]
        # Greedy deficit allocation; no audit outcomes are used.
        chosen = max(available, key=lambda h: (budget * weights[h] - result[h], -h))
        result[chosen] += 1
    return result


def _counts(observations: Sequence[dict[str, Any]], h_count: int) -> tuple[list[int], list[int], list[int]]:
    n, e, u = [0] * h_count, [0] * h_count, [0] * h_count
    for record in observations:
        h = record["stratum"]
        n[h] += 1
        e[h] += record["outcome"] == "ERROR"
        u[h] += record["outcome"] == "UNKNOWN"
    return n, e, u


def _compute_candidate_allocation(*, populations: list[int], pilot_sizes: list[int],
                          pilot_errors: list[int], pilot_unknown: list[int],
                          budget: int, epsilon: float, alpha: float,
                          rho: float, beta: float) -> tuple[list[int], dict[str, Any]]:
    """Enumerate at most two-stratum allocations using a frozen planning rule.

    Jeffreys-smoothed pilot error fractions are working forecasts, not trusted
    probabilities.  Forecast bounds at rounded expected errors determine the
    allocation maximizing clearance from the acceptance/rejection threshold.
    None of the forecasts enter the final interval or final decision.
    """
    residual = [n - p for n, p in zip(populations, pilot_sizes)]
    remaining_budget = budget - sum(pilot_sizes)
    forecasts = [(e + 0.5 * u + 0.5) / (p + 1)
                 for p, e, u in zip(pilot_sizes, pilot_errors, pilot_unknown)]
    if len(populations) == 1:
        candidates = [[remaining_budget]]
    else:
        start = max(0, remaining_budget - residual[1])
        end = min(remaining_budget, residual[0])
        candidates = [[first, remaining_budget - first] for first in range(start, end + 1)]
    best_key: tuple[float, float, int] | None = None
    best: list[int] | None = None
    best_details: dict[str, Any] = {}
    total = sum(populations)
    for sizes in candidates:
        errors = [min(m, int(floor(m * p + 0.5))) for m, p in zip(sizes, forecasts)]
        estimate = stratified_residual_interval(
            populations=populations, pilot_sizes=pilot_sizes,
            pilot_errors=pilot_errors, pilot_unknown=pilot_unknown,
            confirmation_sizes=sizes, confirmation_errors=errors,
            confirmation_unknown=[0] * len(populations), alpha=alpha,
            reference_error_upper=rho, reference_risk_beta=beta)
        clearance = max(epsilon - estimate["upper"], estimate["lower"] - epsilon)
        deviation = sum(abs(p + m - budget * n / total)
                        for p, m, n in zip(pilot_sizes, sizes, populations))
        key = (round(clearance, 14), -round(deviation, 12), -sizes[0])
        if best_key is None or key > best_key:
            best_key, best = key, sizes
            best_details = {"predicted_lower": estimate["lower"],
                            "predicted_upper": estimate["upper"],
                            "predicted_errors": errors, "threshold_clearance": clearance,
                            "proportional_allocation_deviation": deviation}
    assert best is not None
    return best, {"rule": "maximize_max(epsilon-predicted_U,predicted_L-epsilon)",
                  "pilot_forecast": forecasts, "allocations_considered": len(candidates),
                  "tie_break": "round14_clearance_then_min_proportional_deviation_then_lexicographic",
                  "forecast_assumptions": "Jeffreys_smoothed_pilot_fraction_rounded_mean_and_no_confirmation_missingness",
                  "forecast_not_used_for_inference": True,
                  "not_a_proven_optimal_allocation": True, **best_details}


@lru_cache(maxsize=32768)
def _cached_candidate_allocation(
    populations: tuple[int, ...], pilot_sizes: tuple[int, ...],
    pilot_errors: tuple[int, ...], pilot_unknown: tuple[int, ...],
    budget: int, epsilon: float, alpha: float, rho: float, beta: float,
) -> tuple[tuple[int, ...], str]:
    """Cache only immutable keys/values; no audit records or answers are stored."""
    allocation, details = _compute_candidate_allocation(
        populations=list(populations), pilot_sizes=list(pilot_sizes),
        pilot_errors=list(pilot_errors), pilot_unknown=list(pilot_unknown),
        budget=budget, epsilon=epsilon, alpha=alpha, rho=rho, beta=beta)
    return tuple(allocation), json.dumps(details, sort_keys=True, separators=(",", ":"))


def _candidate_allocation(*, populations: list[int], pilot_sizes: list[int],
                          pilot_errors: list[int], pilot_unknown: list[int],
                          budget: int, epsilon: float, alpha: float,
                          rho: float, beta: float) -> tuple[list[int], dict[str, Any]]:
    """Return fresh caller-owned containers for the identical cached rule."""
    allocation, serialized = _cached_candidate_allocation(
        tuple(populations), tuple(pilot_sizes), tuple(pilot_errors), tuple(pilot_unknown),
        budget, epsilon, alpha, rho, beta)
    return list(allocation), json.loads(serialized)


def run_budget_audit(*, strata: Sequence[int], budget: int, seed: int,
                     strategy: str, audit: AuditOracle, epsilon: float = 0.05,
                     alpha: float = 0.05, reference_error_upper: float = 0.0,
                     reference_risk_beta: float = 0.0) -> dict[str, Any]:
    """Spend exactly a fixed unit-cost budget and evaluate the audited version.

    ``strata`` is a public feature-only partition per row: 0=low, 1=high risk.
    At most two nonempty strata are supported.  The caller must freeze it before
    revealing audit outcomes.  ``audit`` may return PASS, ERROR, UNKNOWN or a
    mapping with ``outcome`` and optional ``reason``.  UNRESOLVED/INCOMPARABLE
    are normalized to UNKNOWN.  Calls expose no unsampled reference records.

    SRS and fixed_random deliberately have the same positions and inference:
    the latter is a named historical fixed-SRS baseline, not a distinct method.
    """
    start = perf_counter()
    if strategy not in STRATEGIES:
        raise ValueError("UNKNOWN_AUDIT_STRATEGY")
    if not callable(audit):
        raise ValueError("CALLABLE_AUDIT_ORACLE_REQUIRED")
    labels = [_int(x, "STRATUM") for x in strata]
    if not labels or set(labels) not in ({0}, {0, 1}):
        raise ValueError("STRATA_MUST_BE_NONEMPTY_ZERO_OR_ZERO_ONE_PARTITION")
    total, h_count = len(labels), max(labels) + 1
    b, rng_seed = _int(budget, "BUDGET"), _int(seed, "SEED")
    if b > total:
        raise ValueError("BUDGET_EXCEEDS_POPULATION")
    eps = _prob(epsilon, "EPSILON")
    a = _prob(alpha, "ALPHA", interior=True)
    rho = _prob(reference_error_upper, "REFERENCE_ERROR_UPPER")
    beta = _prob(reference_risk_beta, "REFERENCE_RISK_BETA")
    if beta >= a:
        raise ValueError("REFERENCE_RISK_BETA_MUST_BE_LESS_THAN_ALPHA")
    frames = [[i for i, h in enumerate(labels) if h == target] for target in range(h_count)]
    populations = [len(frame) for frame in frames]
    observations: list[dict[str, Any]] = []
    seen: set[int] = set()

    def observe(positions: Sequence[int], step: str) -> None:
        for position in positions:
            if position in seen or len(seen) >= b:
                raise RuntimeError("DUPLICATE_OR_OVER_BUDGET_AUDIT")
            raw = audit(position)
            outcome = raw.get("outcome") if isinstance(raw, Mapping) else raw
            if not isinstance(outcome, str):
                raise ValueError("AUDIT_ORACLE_OUTCOME_MUST_BE_PASS_ERROR_UNKNOWN")
            if outcome in {"INCOMPARABLE", "UNRESOLVED"}:
                outcome = "UNKNOWN"
            if outcome not in {"PASS", "ERROR", "UNKNOWN"}:
                raise ValueError("AUDIT_ORACLE_OUTCOME_MUST_BE_PASS_ERROR_UNKNOWN")
            record = {"position": position, "stratum": labels[position],
                      "step": step, "outcome": outcome}
            if isinstance(raw, Mapping) and "reason" in raw:
                record["reason"] = str(raw["reason"])
            observations.append(record)
            seen.add(position)

    pilot_sizes, pilot_errors, pilot_unknown = [0] * h_count, [0] * h_count, [0] * h_count
    planning: dict[str, Any] | None = None
    if strategy in {"srs", "fixed_random"}:
        positions = sorted(int(i) for i in np.random.default_rng(rng_seed).choice(total, b, replace=False))
        observe(positions, "fixed_srs")
        _, errors, unknown = _counts(observations, h_count)
        interval = stratified_residual_interval(
            populations=[total], pilot_sizes=[0], pilot_errors=[0], pilot_unknown=[0],
            confirmation_sizes=[b], confirmation_errors=[sum(errors)],
            confirmation_unknown=[sum(unknown)], alpha=a,
            reference_error_upper=rho, reference_risk_beta=beta)
        allocation = _counts(observations, h_count)[0]
    else:
        # Reusing the same per-layer random permutations across strategies is a
        # paired design.  Its remaining prefix is still uniform given a pilot.
        permutations = [np.random.default_rng(np.random.SeedSequence([rng_seed, h, 6003])).permutation(frame).tolist()
                        for h, frame in enumerate(frames)]
        if strategy == "candidate":
            pilot_sizes = [min(PILOT_PER_STRATUM, n) for n in populations]
            if b < sum(pilot_sizes):
                raise ValueError("CANDIDATE_BUDGET_SMALLER_THAN_FIXED_PILOT")
            for h in range(h_count):
                observe(permutations[h][:pilot_sizes[h]], "pilot")
            _, pilot_errors, pilot_unknown = _counts(observations, h_count)
            confirmation, planning = _candidate_allocation(
                populations=populations, pilot_sizes=pilot_sizes,
                pilot_errors=pilot_errors, pilot_unknown=pilot_unknown,
                budget=b, epsilon=eps, alpha=a, rho=rho, beta=beta)
        else:
            weights = ([n / total for n in populations] if strategy == "proportional"
                       else ([1.0] if h_count == 1 else [0.2, 0.8]))
            confirmation = _apportion(b, populations, weights)
        # All confirmation sizes are fixed before the first confirmation call.
        confirmation_start = len(observations)
        for h in range(h_count):
            p = pilot_sizes[h]
            observe(permutations[h][p:p + confirmation[h]], "confirmation")
        _, errors, unknown = _counts(observations[confirmation_start:], h_count)
        interval = stratified_residual_interval(
            populations=populations, pilot_sizes=pilot_sizes,
            pilot_errors=pilot_errors, pilot_unknown=pilot_unknown,
            confirmation_sizes=confirmation, confirmation_errors=errors,
            confirmation_unknown=unknown, alpha=a,
            reference_error_upper=rho, reference_risk_beta=beta)
        allocation = [p + m for p, m in zip(pilot_sizes, confirmation)]
    if len(observations) != b or len(seen) != b:
        raise RuntimeError("FIXED_BUDGET_NOT_EXACTLY_SPENT")
    _, all_errors, all_unknown = _counts(observations, h_count)
    decision = interval_decision(interval["lower"], interval["upper"], eps)
    return {"method_version": METHOD_VERSION, "strategy": strategy,
            "seed": rng_seed, "population_size": total, "budget": b,
            "audit_cost": b, "cost_unit": "one_audited_record_not_observed_money_or_time",
            "sample_size": b, "n": b - sum(all_unknown), "e": sum(all_errors),
            "unresolved": sum(all_unknown), "positions": [x["position"] for x in observations],
            "observations": observations, "allocation": allocation,
            "pilot_allocation": pilot_sizes, "planning": planning,
            "epsilon": eps, "alpha": a,
            "error_rate_interval": {"lower": interval["lower"], "upper": interval["upper"]},
            "interval_details": interval, "statistical_decision": decision,
            "decision": decision, "content_decision": decision,
            "claim_scope": "reference_conformance_unless_external_factual_basis_is_established",
            "evidence_authentication_in_this_kernel": False,
            "fixed_budget": True, "optional_stopping": False,
            "baseline_duplicate_of": "srs" if strategy == "fixed_random" else None,
            "elapsed_seconds": perf_counter() - start,
            "assumptions": [
                "Immutable row frame, feature-only strata and budget fixed before audit outcomes.",
                "Within-stratum permutations are uniform; confirmation sizes use only pilot observations.",
                "No confirmation outcome is used to reallocate, extend, stop or choose a favorable result.",
                "The reference version and comparison rule are fixed; rho/beta require external support.",
                "Repeated budgets are separate sensitivity cases, not a sequence permitting optional stopping."],
            "limitations": [
                "No probability that the complete dataset is factually true is returned.",
                "Candidate planning uses an unvalidated working forecast and may be worse than SRS.",
                "Unit audit cost is a declared experimental cost, not measured real audit labor.",
                "Provenance, signatures, representativeness and task value require separate checks."]}
