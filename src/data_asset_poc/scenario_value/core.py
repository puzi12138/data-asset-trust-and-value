"""Frozen-rule scenario utility; no fitting, prices, or truth claims are inferred.

Validation selection and retrospective test evaluation deliberately use separate
functions. A bootstrap interval is conditional on fitted models and observed
exchangeable feature groups; it is not an exact finite-sample risk guarantee.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

import numpy as np
from threadpoolctl import threadpool_limits

METHOD_VERSION = "task_value-paired-group-value-v1"
CI_SCOPE = (
    "Percentile feature-group bootstrap, conditional on fitted models, realized "
    "training/assets and exchangeable evaluation groups. Does not include "
    "training, model selection, future drift or evidence uncertainty; no exact "
    "finite-sample coverage guarantee."
)


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return int(value)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be finite numeric data")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite numeric data") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite numeric data")
    return number


def _vector(value: Any, name: str, length: int | None = None) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.ndim != 1 or not len(array) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a nonempty finite one-dimensional array")
    if length is not None and len(array) != length:
        raise ValueError(f"{name} must align with all evaluation rows")
    return array


def per_row_losses(
    y_true: Sequence[float], prediction: Sequence[float], task_type: str,
) -> np.ndarray:
    """Primary losses only: natural-log binary loss or absolute regression loss.

    Predictions 0/1 are clipped to float64 machine epsilon for finite binary
    loss, matching the declared pipeline. Invalid probabilities are rejected.
    The two task losses have different units and are never pooled here.
    """
    y = _vector(y_true, "y_true")
    pred = _vector(prediction, "prediction", len(y))
    if task_type in ("classification", "binary_classification"):
        if not np.isin(y, [0, 1]).all() or ((pred < 0) | (pred > 1)).any():
            raise ValueError("Binary labels must be 0/1 and probabilities in [0,1]")
        eps = np.finfo(np.float64).eps
        p = np.clip(pred, eps, 1 - eps)
        return -(y * np.log(p) + (1 - y) * np.log1p(-p))
    if task_type == "regression":
        return np.abs(y - pred)
    raise ValueError("task_type must be binary classification or regression")


def value_evidence_status(
    interval: Mapping[str, Any] | None,
    model_reliable: bool = True,
    evaluated: bool = True,
) -> str:
    """The status concerns one fixed task, learner, buyer and target distribution."""
    if not isinstance(model_reliable, bool) or not isinstance(evaluated, bool):
        raise ValueError("model_reliable/evaluated must be booleans")
    if not evaluated or interval is None:
        return "NOT_EVALUATED"
    if not model_reliable:
        return "TASK_MODEL_UNRELIABLE"
    lower = _finite(interval["ci_lower"], "ci_lower")
    upper = _finite(interval["ci_upper"], "ci_upper")
    if lower > upper:
        raise ValueError("ci_lower must not exceed ci_upper")
    if lower > 0:
        return "POSITIVE_VALUE_EVIDENCE"
    if upper < 0:
        return "NEGATIVE_VALUE_EVIDENCE"
    return "VALUE_UNCERTAIN"


@threadpool_limits.wrap(limits=1)
def paired_group_bootstrap(
    group_ids: Sequence[Any],
    base_loss: Sequence[float],
    candidate_losses: Mapping[str, Sequence[float]],
    *,
    n_bootstrap: int = 2000,
    seed: int = 20261023,
    alpha: float = .05,
    family_size: int = 6,
    simultaneous: bool = True,
    include_draws: bool = True,
) -> dict[str, Any]:
    """Resample G groups G times with replacement, keeping every group row.

    Each replicate's mean is row-weighted using its actual row denominator.
    Identical group multiplicities are used for every candidate. Validation
    percentile tails are alpha/(2*K), with the fixed predeclared family K;
    test description uses alpha/2 and never changes the validation decision.

    Group sums, counts, seed, RNG ID, weight hash and optional draws are emitted
    so a portable verifier need not fit models or access individual features.
    Bonferroni controls multiplicity only insofar as marginal bootstrap
    intervals are valid; finite-sample exactness is explicitly not asserted.
    """
    base = _vector(base_loss, "base_loss")
    if (base < 0).any():
        raise ValueError("Losses must be nonnegative")
    raw_groups = np.asarray(group_ids, dtype=object)
    if raw_groups.ndim != 1 or len(raw_groups) != len(base):
        raise ValueError("group_ids must align with all evaluation rows")
    if any(value is None or not isinstance(value, (str, int, np.integer))
           or isinstance(value, (bool, np.bool_)) or str(value) == ""
           for value in raw_groups):
        raise ValueError("Groups must be nonempty string or integer identifiers")
    # Canonical string labels match exported SHA feature groups. Distinct typed
    # labels such as integer 1 and string '1' are ambiguous and are rejected.
    typed = {(type(v).__name__, str(v)) for v in raw_groups}
    labels, inverse = np.unique(raw_groups.astype(str), return_inverse=True)
    if len(typed) != len(labels):
        raise ValueError("group_ids contain ambiguous typed identifiers")
    if len(labels) < 2:
        raise ValueError("At least two feature groups are needed for bootstrap")
    if not candidate_losses:
        raise ValueError("At least one candidate is required")
    names = list(candidate_losses)
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Candidate IDs must be nonempty strings")
    count = _integer(n_bootstrap, "n_bootstrap", 200)
    seed_value = _integer(seed, "seed", 0)
    family = _integer(family_size, "family_size", 1)
    if family < len(names):
        raise ValueError("family_size cannot be smaller than candidate count")
    risk = _finite(alpha, "alpha")
    if not 0 < risk < 1:
        raise ValueError("alpha must be in (0,1)")
    if not isinstance(simultaneous, bool) or not isinstance(include_draws, bool):
        raise ValueError("simultaneous/include_draws must be booleans")
    losses = np.column_stack([_vector(candidate_losses[name], name, len(base)) for name in names])
    if (losses < 0).any():
        raise ValueError("Losses must be nonnegative")
    group_count = len(labels)
    sizes = np.bincount(inverse).astype(np.int64)
    base_sums = np.bincount(inverse, weights=base)
    loss_sums = np.zeros((group_count, len(names)), dtype=float)
    np.add.at(loss_sums, inverse, losses)
    # Sum paired row differences directly to minimize cancellation.
    difference_sums = np.zeros_like(loss_sums)
    np.add.at(difference_sums, inverse, base[:, None] - losses)
    if not (np.isfinite(base_sums).all() and np.isfinite(loss_sums).all()
            and np.isfinite(difference_sums).all()):
        raise ValueError("Group loss sums overflowed; rescale the declared loss units")
    rng = np.random.default_rng(seed_value)
    weights = rng.multinomial(group_count, np.full(group_count, 1 / group_count), size=count)
    draws_hash = hashlib.sha256(weights.astype("<u4").tobytes(order="C")).hexdigest()
    denominators = weights @ sizes
    draws = (weights @ difference_sums) / denominators[:, None]
    if not np.isfinite(draws).all():
        raise ValueError("Bootstrap loss sums overflowed; rescale the declared loss units")
    tail = risk / (2 * family) if simultaneous else risk / 2
    low, high = np.quantile(draws, [tail, 1 - tail], axis=0, method="linear")
    points = np.mean(base[:, None] - losses, axis=0)
    assets: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(names):
        row = {"delta_u": float(points[index]), "ci_lower": float(low[index]),
               "ci_upper": float(high[index]), "base_loss_mean": float(base.mean()),
               "asset_loss_mean": float(losses[:, index].mean())}
        row["value_status"] = value_evidence_status(row)
        assets[name] = row
    return {
        "method_version": METHOD_VERSION, "assets": assets,
        "row_count": len(base), "group_count": group_count,
        "bootstrap_replicates": count, "bootstrap_seed": seed_value,
        "rng": "numpy_default_rng_PCG64_multinomial_equal_groups",
        "group_order": "ascending_canonical_string",
        "quantile_method": "linear", "alpha": risk, "family_size": family,
        "simultaneous": simultaneous, "percentile_tail_probability": tail,
        "nominal_confidence_level": 1 - risk,
        "coverage_guarantee": "APPROXIMATE_BOOTSTRAP_NO_EXACT_FINITE_SAMPLE_GUARANTEE",
        "ci_scope": CI_SCOPE, "draws_sha256": draws_hash,
        "draws_hash_encoding": "G multiplicities per replicate, row-major little-endian uint32",
        "groups": [
            {"group_id": str(label), "row_count": int(sizes[i]),
             "base_loss_sum": float(base_sums[i]),
             "candidate_loss_sums": {name: float(loss_sums[i, j]) for j, name in enumerate(names)},
             "delta_loss_sums": {name: float(difference_sums[i, j]) for j, name in enumerate(names)}}
            for i, label in enumerate(labels)
        ],
        "bootstrap_draws": ({name: draws[:, j].tolist() for j, name in enumerate(names)}
                            if include_draws else None),
    }


def validation_model_reliability(
    base_loss: float | None,
    base_dummy_loss: float | None,
    candidate_loss: float | None,
    candidate_dummy_loss: float | None,
    *,
    split: str = "validation",
    base_fit_success: bool = True,
    candidate_fit_success: bool = True,
) -> dict[str, Any]:
    """Necessary validation skill screen, never a calibration guarantee.

    Both trained models must strictly beat their respective training-fitted
    Dummy on the same validation primary loss. Ties fail. Fit failures are
    reported instead of being assigned synthetic loss values.
    """
    if split != "validation":
        raise ValueError("Reliability used for selection must use validation, never test")
    if not isinstance(base_fit_success, bool) or not isinstance(candidate_fit_success, bool):
        raise ValueError("Fit success flags must be booleans")
    reasons = []
    for role, fit, loss, dummy in (
        ("BASE", base_fit_success, base_loss, base_dummy_loss),
        ("CANDIDATE", candidate_fit_success, candidate_loss, candidate_dummy_loss),
    ):
        if not fit:
            reasons.append(f"{role}_FIT_FAILED")
            continue
        if loss is None or dummy is None:
            reasons.append(f"{role}_VALIDATION_NOT_EVALUATED")
            continue
        observed = _finite(loss, f"{role}_loss")
        constant = _finite(dummy, f"{role}_dummy_loss")
        if observed < 0 or constant < 0:
            raise ValueError("Losses must be nonnegative")
        if observed >= constant:
            reasons.append(f"{role}_NOT_BETTER_THAN_TRAIN_FITTED_DUMMY")
    return {"model_reliable": not reasons,
            "status": "TASK_MODEL_RELIABLE_SCREEN_PASSED" if not reasons else "TASK_MODEL_UNRELIABLE",
            "reasons": reasons, "uses_split": split,
            "rule": "base and augmented model validation primary loss strictly below own train-fitted Dummy",
            "calibration_guarantee": False}


def select_assets(
    candidates: Sequence[Mapping[str, Any]],
    method: str,
    *,
    trust_required: bool = False,
    model_reliable: bool = True,
) -> dict[str, Any]:
    """Select at most one asset using validation/metadata only; None keeps D0.

    ``size`` and ``quality`` are always-buy simple baselines among eligible
    assets. ``point`` and ``lcb`` buy only on strictly positive respective
    validation scores. Every deployed method uses the same reliability gate
    and has an optional same-evidence trust-constrained counterpart. Arbitrary
    test fields in the inputs are never read, including for tie-breaking.
    """
    if method not in {"size", "quality", "point", "lcb"}:
        raise ValueError("method must be size, quality, point or lcb; oracle is a separate function")
    if not isinstance(trust_required, bool) or not isinstance(model_reliable, bool):
        raise ValueError("Gate flags must be booleans")
    names = [candidate.get("asset_id") for candidate in candidates]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("Provide distinct nonempty asset IDs")
    eligible: list[tuple[str, float]] = []
    excluded: dict[str, str] = {}
    field = {"size": "row_count", "quality": "quality_score", "point": "validation_delta", "lcb": "validation_lower"}[method]
    for candidate in candidates:
        name = str(candidate["asset_id"])
        if (not isinstance(candidate.get("evaluated", True), bool)
                or not isinstance(candidate.get("model_reliable", True), bool)):
            raise ValueError("Candidate evaluated/model_reliable flags must be booleans")
        if not candidate.get("evaluated", True):
            excluded[name] = "NOT_EVALUATED"
        elif not model_reliable or not candidate.get("model_reliable", True):
            excluded[name] = "TASK_MODEL_UNRELIABLE"
        elif trust_required and candidate.get("trust_status", "NEED_MORE_EVIDENCE") != "ACCEPT_CONDITIONALLY":
            excluded[name] = "TRUST_CONDITIONS_NOT_MET"
        else:
            score = _finite(candidate.get(field), field)
            if method == "size" and (score <= 0 or not score.is_integer()):
                raise ValueError("row_count must be a positive integer")
            if method == "quality" and not 0 <= score <= 1:
                raise ValueError("quality_score must be in [0,1]")
            eligible.append((name, score))
    chosen = None
    best_score = None
    if eligible:
        # Deterministic, prespecified ties use only the public asset ID.
        name, score = sorted(eligible, key=lambda item: (-item[1], item[0]))[0]
        if method in {"size", "quality"} or score > 0:
            chosen, best_score = name, score
    return {"method": method, "selected_asset_id": chosen, "selection_score": best_score,
            "no_purchase": chosen is None, "uses_test_for_selection": False,
            "trust_required": trust_required, "model_reliability_gate": True,
            "eligible_asset_ids": sorted(name for name, _ in eligible),
            "excluded": excluded,
            "reason": ("SELECTED" if chosen else
                       "NO_ELIGIBLE_CANDIDATES" if not eligible else "NO_STRICTLY_POSITIVE_VALIDATION_SCORE")}


def oracle_selection(test_deltas: Mapping[str, float]) -> dict[str, Any]:
    """Unconstrained, undeployable retrospective test oracle including D0=0.

    This is an observed finite-test benchmark, never a true-population oracle.
    No trust/reliability gate is imposed so regret for gated methods honestly
    includes opportunity costs. A feasibility-constrained oracle can be formed
    explicitly by the caller passing the prespecified feasible subset.
    """
    scores = []
    for name, value in test_deltas.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Oracle asset IDs must be nonempty strings")
        scores.append((name, _finite(value, "test_delta")))
    chosen = None
    utility = 0.0
    if scores:
        name, score = sorted(scores, key=lambda item: (-item[1], item[0]))[0]
        if score > 0:
            chosen, utility = name, score
    return {"selected_asset_id": chosen, "test_delta": utility,
            "uses_test_for_selection": True, "deployable": False,
            "scope": "RETROSPECTIVE_OBSERVED_TEST_ORACLE_NOT_POPULATION_OPTIMUM"}


def evaluate_selection(
    selection: Mapping[str, Any] | str | None,
    test_deltas: Mapping[str, float],
) -> dict[str, Any]:
    """Evaluate a frozen choice after test access; this function cannot select."""
    name = selection.get("selected_asset_id") if isinstance(selection, Mapping) else selection
    clean = {asset: _finite(value, "test_delta") for asset, value in test_deltas.items()}
    if name is not None and name not in clean:
        raise ValueError("Selected asset has no exact corresponding test measurement")
    utility = clean[name] if name is not None else 0.0
    oracle = oracle_selection(clean)
    regret = oracle["test_delta"] - utility
    if regret < -1e-12:
        raise AssertionError("Observed oracle regret must be nonnegative")
    max_value = oracle["test_delta"]
    # A tie is correct regardless of ID; a strictly negative candidate is never
    # equivalent to abstention. Tolerance is numerical, not a utility threshold.
    correct = bool(np.isclose(utility, max_value, rtol=1e-10, atol=1e-12))
    return {"selected_asset_id": name, "test_delta": utility,
            "no_purchase": name is None, "negative_utility_selected": bool(name is not None and utility < 0),
            "correct_selection_observed_test": correct,
            "oracle_asset_id": oracle["selected_asset_id"], "oracle_test_delta": max_value,
            "observed_test_regret": max(0.0, regret),
            "evaluation_use": "POSTHOC_ONLY_NOT_AN_INPUT_TO_CURRENT_SELECTION"}
