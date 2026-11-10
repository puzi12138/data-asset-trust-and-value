"""Measurable quality baselines; validity does not establish factual truth."""
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schema import FieldRule, RULES


def missing_mask(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype("string").str.strip().str.lower().isin(["", "unknown"])


def valid_mask(series: pd.Series, rule: FieldRule) -> pd.Series:
    if rule.kind == "category":
        return series.isin(rule.allowed) & ~missing_mask(series)
    values = pd.to_numeric(series, errors="coerce")
    valid = values.notna() & np.isfinite(values)
    if rule.lower is not None:
        valid &= values >= rule.lower
    if rule.upper is not None:
        valid &= values <= rule.upper
    if rule.integer:
        valid &= values.mod(1).eq(0)
    return valid & ~missing_mask(series)


@dataclass(frozen=True)
class ConsistencyRule:
    name: str
    columns: tuple[str, ...]
    predicate: Callable[[pd.DataFrame], pd.Series]


DEFAULT_CONSISTENCY = (
    ConsistencyRule("no_previous_implies_pdays_999", ("previous", "pdays"),
                    lambda d: d.previous.ne(0) | d.pdays.eq(999)),
    ConsistencyRule("no_previous_implies_nonexistent", ("previous", "poutcome"),
                    lambda d: d.previous.ne(0) | d.poutcome.eq("nonexistent")),
    ConsistencyRule("previous_implies_known_outcome", ("previous", "poutcome"),
                    lambda d: d.previous.eq(0) | d.poutcome.isin(["failure", "success"])),
)


def evaluate_quality(frame: pd.DataFrame, rules: dict[str, FieldRule] | None = None,
                     consistency_rules: Sequence[ConsistencyRule] | None = None) -> dict:
    rules = RULES if rules is None else rules
    consistency_rules = DEFAULT_CONSISTENCY if consistency_rules is None else consistency_rules
    if frame.empty or len(frame.columns) == 0:
        raise ValueError("Quality metrics require a nonempty dataset")
    if set(frame.columns) != set(rules):
        raise ValueError(f"Schema mismatch: expected {sorted(rules)}, got {sorted(frame.columns)}")
    missing = pd.DataFrame({c: missing_mask(frame[c]) for c in frame})
    valid = pd.DataFrame({c: valid_mask(frame[c], rules[c]) for c in frame})
    present_count = int((~missing).to_numpy().sum())
    invalid_count = int((~valid & ~missing).to_numpy().sum())
    details, eligible_total, violation_total = {}, 0, 0
    normalized = frame.copy()
    for col, rule in rules.items():
        if rule.kind == "numeric":
            normalized[col] = pd.to_numeric(frame[col], errors="coerce")
    for rule in consistency_rules:
        eligible = valid[list(rule.columns)].all(axis=1)
        violations = eligible & ~rule.predicate(normalized)
        denominator, errors = int(eligible.sum()), int(violations.sum())
        eligible_total += denominator
        violation_total += errors
        details[rule.name] = {"eligible": denominator, "violations": errors,
                              "pass_rate": 1 - errors / denominator if denominator else None}
    duplicates = int(frame.duplicated().sum())  # Excess copies; includes y and excludes lineage IDs.
    scores = {"completeness": 1 - int(missing.to_numpy().sum()) / frame.size,
              "uniqueness": 1 - duplicates / len(frame),
              "format_validity": 1 - invalid_count / present_count if present_count else None,
              "consistency": 1 - violation_total / eligible_total if eligible_total else None}
    total_score = sum(scores.values()) / 4 if all(v is not None for v in scores.values()) else None
    return {"row_count": len(frame), "cell_count": int(frame.size), "missing_cells": int(missing.to_numpy().sum()),
            "present_cells": present_count, "invalid_present_cells": invalid_count,
            "duplicate_excess": duplicates, "consistency_eligible": eligible_total,
            "consistency_violations": violation_total, **scores, "quality_score": total_score,
            "per_column": {c: {"missing": int(missing[c].sum()), "invalid_present": int((~valid[c] & ~missing[c]).sum())} for c in frame},
            "consistency_rules": details}


def sanitize_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply identical fixed rules before train-only learned imputation/encoding."""
    clean = frame.copy()
    for col in clean:
        rule = RULES[col]
        clean.loc[~valid_mask(clean[col], rule), col] = np.nan
        if rule.kind == "numeric":
            clean[col] = pd.to_numeric(clean[col], errors="coerce").astype(float)
    # 999 denotes not previously contacted, not a real elapsed interval.
    if "pdays" in clean:
        clean.loc[clean.pdays.eq(999), "pdays"] = np.nan
    return clean
