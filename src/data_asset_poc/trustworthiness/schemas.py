"""Explicit domain checks; inferred CSV dtypes are not business truth."""
from __future__ import annotations

import pandas as pd

from ..schema import FieldRule, RULES, numeric
from ..quality import DEFAULT_CONSISTENCY

WINE_COLUMNS = (
    "fixed acidity", "volatile acidity", "citric acid", "residual sugar", "chlorides",
    "free sulfur dioxide", "total sulfur dioxide", "density", "pH", "sulphates", "alcohol", "quality",
)
WINE_RULES: dict[str, FieldRule] = {name: numeric(0) for name in WINE_COLUMNS}
WINE_RULES.update({"pH": numeric(0, 14), "quality": numeric(0, 10, True)})


def identify_schema(frame: pd.DataFrame) -> str:
    names = set(frame.columns)
    if names == set(WINE_COLUMNS):
        return "wine"
    # Supported bank field sets permit omission of campaign and duration.
    # Arbitrary subsets do not silently acquire a Bank domain interpretation.
    bank_names = set(RULES)
    if names in (bank_names, bank_names - {"campaign"}, bank_names | {"duration"}):
        return "bank"
    return "generic"


def rules_for(frame: pd.DataFrame, schema_name: str):
    """Return applicable explicit checks, never learn ranges from the candidate."""
    names = set(frame.columns)
    if schema_name == "bank":
        available = {**RULES, "duration": numeric(0, None, True)}
        if not names <= set(available) or "y" not in names:
            raise ValueError("BANK_SCHEMA_MISMATCH")
        return {c: available[c] for c in frame}, tuple(
            rule for rule in DEFAULT_CONSISTENCY if set(rule.columns) <= names)
    if schema_name in {"wine", "wine_red", "wine_white"}:
        if names != set(WINE_COLUMNS):
            raise ValueError("WINE_SCHEMA_MISMATCH")
        from ..quality import ConsistencyRule
        # Total sulfur includes its free component. This checks only a necessary
        # domain constraint, not chemical accuracy or sensory label accuracy.
        return WINE_RULES, (ConsistencyRule("free_sulfur_not_above_total",
            ("free sulfur dioxide", "total sulfur dioxide"),
            lambda d: d["free sulfur dioxide"] <= d["total sulfur dioxide"]),)
    if schema_name != "generic":
        raise ValueError("UNKNOWN_SCHEMA_NAME")
    return None, ()
