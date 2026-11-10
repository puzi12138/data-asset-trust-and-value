"""Separate observable duplicate definitions from unknown factual duplicates."""
from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def _columns(frame: pd.DataFrame, columns, name: str) -> list[str]:
    if isinstance(columns, (str, bytes)) or not isinstance(columns, Sequence):
        raise ValueError(f"{name}_MUST_BE_COLUMN_LIST")
    result = list(columns)
    if any(not isinstance(c, str) or c not in frame.columns for c in result) or len(set(result)) != len(result):
        raise ValueError(f"{name}_MUST_BE_DISTINCT_EXISTING_COLUMNS")
    return result


def _metric(frame: pd.DataFrame, columns: list[str]) -> dict:
    n = len(frame)
    if not columns or not n:
        return {"status": "NOT_EVALUATED", "columns": columns, "row_count": n,
                "excess_duplicate_rows": None, "rows_in_duplicate_groups": None,
                "duplicate_group_count": None, "unique_row_patterns": None,
                "excess_duplicate_fraction": None, "uniqueness": None,
                "reason": "NO_ROWS" if not n else "NO_ASSESSED_COLUMNS"}
    selected = frame.loc[:, columns]
    excess = int(selected.duplicated(keep="first").sum())
    members = int(selected.duplicated(keep=False).sum())
    return {"status": "MEASURED", "columns": columns, "row_count": n,
            "excess_duplicate_rows": excess, "rows_in_duplicate_groups": members,
            "duplicate_group_count": members - excess,
            "unique_row_patterns": n - excess,
            "excess_duplicate_fraction": excess / n, "uniqueness": 1 - excess / n,
            "definition": "Exact values in declared columns; first occurrence is not excess."}


def duplicate_metrics(frame: pd.DataFrame, auxiliary_id_columns=None, feature_columns=None) -> dict:
    """Measure three column scopes; a unique row_id cannot certify content uniqueness.

    ``None`` auxiliary columns means the requested default empty list. No column
    is inferred or dropped merely because its name resembles an ID.
    """
    if not isinstance(frame, pd.DataFrame) or frame.columns.has_duplicates:
        raise ValueError("DISTINCT_COLUMN_DATAFRAME_REQUIRED")
    if any(not isinstance(c, str) or not c for c in frame.columns):
        raise ValueError("NONEMPTY_STRING_COLUMNS_REQUIRED")
    auxiliary = _columns(frame, [] if auxiliary_id_columns is None else auxiliary_id_columns, "AUXILIARY_ID_COLUMNS")
    features = None if feature_columns is None else _columns(frame, feature_columns, "FEATURE_COLUMNS")
    if features is not None and set(features) & set(auxiliary):
        raise ValueError("TASK_FEATURES_MUST_EXCLUDE_DECLARED_AUXILIARY_IDS")
    content = [c for c in frame.columns if c not in auxiliary]
    return {"row_count": len(frame), "auxiliary_id_columns": auxiliary,
            "full_row": _metric(frame, frame.columns.tolist()),
            "content_excluding_auxiliary_ids": _metric(frame, content),
            "task_features": (_metric(frame, features) if features is not None else
                {"status": "NOT_EVALUATED", "columns": None, "reason": "TASK_FEATURE_COLUMNS_NOT_DECLARED"}),
            "factual_duplicates": {"status": "UNKNOWN", "count": None,
                "reason": "Equal field values do not establish the same real-world entity or event."},
            "auxiliary_ids_are_declarations": True,
            "warning": "Full-row uniqueness may be caused by an auxiliary ID and does not establish content uniqueness."}
