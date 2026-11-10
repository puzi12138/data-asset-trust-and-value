"""New measurements from supplied tables; no historical value lookup.

The frozen Task_value loss/bootstrap functions are reused without modification.
All uncertainty is conditional on these fixed fits and exchangeable groups.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import warnings
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from ..scenario_value.core import (CI_SCOPE, paired_group_bootstrap,
    per_row_losses, validation_model_reliability, value_evidence_status)
from ..scenario_value import core as value_core

ENGINE_VERSION = "self_service_value-self-service-value-v2-train-schema-id"
MAX_ROWS = 10000
MAX_COLUMNS = 64


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write(path: Path, value: Any) -> str:
    raw = _json(value)
    path.write_bytes(raw)
    return _hash(raw)


def _table_hash(frame: pd.DataFrame) -> str:
    return _hash(frame.to_csv(index=False, lineterminator="\n", float_format="%.17g").encode("utf-8"))


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or not low <= value <= high:
        raise ValueError(f"{name}_OUT_OF_RANGE_{low}_{high}")
    return int(value)


def _settings(task: dict) -> dict:
    if not isinstance(task, dict):
        raise ValueError("TASK_MUST_BE_OBJECT")
    defaults = {"target": None, "task_type": None, "positive_label": None,
        "buyer_mode": "SIMULATED_BUYER_SCENARIO", "split_mode": "record",
        "entity_column": None, "time_column": None, "id_columns": [],
        "seed": 101, "model_seeds": [101, 202, 303], "n_bootstrap": 500,
        "alpha": .05, "target_unit": "target_units"}
    unknown = set(task) - set(defaults)
    if unknown:
        raise ValueError("UNKNOWN_TASK_PARAMETERS:" + ",".join(sorted(map(str, unknown))))
    result = {**defaults, **copy.deepcopy(task)}
    result["seed"] = _integer(result["seed"], "SEED", 0, 2**32-2)
    seeds = result["model_seeds"]
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 3:
        raise ValueError("MODEL_SEEDS_REQUIRE_1_TO_3_VALUES")
    result["model_seeds"] = [_integer(v, "MODEL_SEED", 0, 2**32-1) for v in seeds]
    if len(set(result["model_seeds"])) != len(seeds):
        raise ValueError("MODEL_SEEDS_MUST_BE_DISTINCT")
    result["n_bootstrap"] = _integer(result["n_bootstrap"], "BOOTSTRAP", 200, 2000)
    alpha = result["alpha"]
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("ALPHA_MUST_BE_FINITE_IN_0_1")
    result["alpha"] = float(alpha)
    if result["buyer_mode"] not in {"SIMULATED_BUYER_SCENARIO", "REAL_BUYER"}:
        raise ValueError("INVALID_BUYER_MODE")
    if result["split_mode"] not in {"record", "entity", "time"}:
        raise ValueError("INVALID_SPLIT_MODE")
    ids = result["id_columns"]
    if not isinstance(ids, list) or any(not isinstance(v, str) or not v for v in ids) or len(set(ids)) != len(ids):
        raise ValueError("ID_COLUMNS_MUST_BE_DISTINCT_COLUMN_NAMES")
    for name in ("entity_column", "time_column"):
        if result[name] is not None and (not isinstance(result[name], str) or not result[name]):
            raise ValueError(f"INVALID_{name.upper()}")
    if result["split_mode"] == "entity" and not result["entity_column"]:
        raise ValueError("ENTITY_SPLIT_REQUIRES_ENTITY_COLUMN")
    if result["split_mode"] == "time" and not result["time_column"]:
        raise ValueError("TIME_SPLIT_REQUIRES_TIME_COLUMN")
    if not isinstance(result["target_unit"], str) or not result["target_unit"].strip():
        raise ValueError("TARGET_UNIT_REQUIRED")
    return result


def _validate_tables(tables: dict[str, pd.DataFrame], task: dict) -> list[str]:
    if sum(len(f) for f in tables.values()) > MAX_ROWS:
        raise ValueError("COMBINED_INPUT_ROWS_EXCEED_10000")
    for role, frame in tables.items():
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError(f"{role.upper()}_REQUIRES_NONEMPTY_TABLE")
        if len(frame.columns) > MAX_COLUMNS or not frame.columns.is_unique:
            raise ValueError("INPUT_COLUMNS_EXCEED_64_OR_DUPLICATED")
        if any(not isinstance(c, str) or not c for c in frame.columns):
            raise ValueError("INPUT_COLUMN_NAMES_MUST_BE_NONEMPTY_STRINGS")
    target = task["target"]
    excluded = set(task["id_columns"]) | {target, task["entity_column"], task["time_column"]}
    features = [c for c in tables["candidate"].columns if c not in excluded]
    if not features:
        raise ValueError("NO_PREDICTOR_COLUMNS")
    if target in set(task["id_columns"]) | {task["entity_column"], task["time_column"]}:
        raise ValueError("TARGET_CANNOT_BE_ID_ENTITY_OR_TIME")
    required = set(features) | {target} | set(task["id_columns"])
    required |= {v for v in (task["entity_column"], task["time_column"]) if v}
    for role, frame in tables.items():
        if set(frame.columns) != set(tables["candidate"].columns) or not required <= set(frame.columns):
            raise ValueError(f"{role.upper()}_SCHEMA_MISMATCH")
        for column in task["id_columns"]:
            values = frame[column]
            if values.isna().any() or values.map(lambda v: str(v).strip().lower() in {"", "unknown", "none", "null", "nan", "?"}).any():
                raise ValueError(f"RECORD_ID_HAS_MISSING_OR_UNKNOWN_VALUE:{role}:{column}")
    if task["buyer_mode"] == "REAL_BUYER":
        for column in task["id_columns"]:
            ids = pd.concat([f[column] for f in tables.values()], ignore_index=True).map(str)
            if ids.duplicated().any():
                raise ValueError(f"DUPLICATE_RECORD_ID_IN_REAL_INPUTS:{column}")
    return features


def _normalize(parts: dict[str, pd.DataFrame], features: list[str], task: dict) -> tuple[dict, list, list, dict]:
    """Infer types from D0 values only, after fixed source-group partitioning.

    A whole-table dtype is not a schema: a test string can change that dtype.
    Numeric-looking strings in D0 are numeric only when every observed D0
    value parses. Evaluation values must then satisfy that frozen schema.
    """
    d0 = parts["D0"]
    numeric = []
    for column in features:
        observed = d0[column].dropna()
        parsed = pd.to_numeric(observed, errors="coerce")
        if parsed.notna().all():
            numeric.append(column)
    categorical = [c for c in features if c not in numeric]
    combined_train = pd.concat([parts["D0"][features], parts["Di"][features]], ignore_index=True)
    for col in categorical:
        if combined_train[col].map(lambda v: None if pd.isna(v) else str(v)).nunique(dropna=True) > 100:
            raise ValueError(f"CATEGORICAL_UNIQUE_EXCEEDS_100:{col}")
    # Conservative upper bound includes every category, missing indicators and numerics.
    transformed_bound = 2*len(numeric) + sum(combined_train[c].map(lambda v: None if pd.isna(v) else str(v)).nunique(dropna=True)+1 for c in categorical)
    if transformed_bound > 512:
        raise ValueError("TRANSFORMED_FEATURE_COLUMNS_EXCEED_512")
    normalized = {}
    labels: dict = {}
    if task["task_type"] == "classification":
        if not isinstance(task["positive_label"], str) or not task["positive_label"]:
            raise ValueError("BINARY_CLASSIFICATION_REQUIRES_EXPLICIT_POSITIVE_LABEL")
        raw_values = d0[task["target"]]
        if raw_values.isna().any():
            raise ValueError("TARGET_HAS_MISSING_VALUES")
        values = sorted(set(raw_values.map(str)))
        if len(values) != 2:
            raise ValueError("BINARY_CLASSIFICATION_REQUIRES_EXACTLY_TWO_CLASSES_MULTICLASS_UNSUPPORTED")
        if task["positive_label"] not in values:
            raise ValueError("POSITIVE_LABEL_NOT_PRESENT")
        labels = {"positive": task["positive_label"], "negative": next(v for v in values if v != task["positive_label"])}
    for role, original in parts.items():
        frame = original.copy().reset_index(drop=True)
        for c in numeric:
            try:
                vals = pd.to_numeric(frame[c], errors="raise").astype(float)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"TRAIN_SCHEMA_REQUIRES_NUMERIC:{role}:{c}") from exc
            if np.isinf(vals.to_numpy()).any():
                raise ValueError(f"NONFINITE_FEATURE:{c}")
            frame[c] = vals
        for c in categorical:
            frame[c] = frame[c].map(lambda v: None if pd.isna(v) else str(v))
        if task["task_type"] == "classification":
            if frame[task["target"]].isna().any() or not frame[task["target"]].map(str).isin(labels.values()).all():
                raise ValueError(f"TARGET_LABEL_OUTSIDE_D0_BINARY_SCHEMA:{role}")
            frame[task["target"]] = frame[task["target"]].map(str).eq(labels["positive"]).astype(int)
        else:
            vals = pd.to_numeric(frame[task["target"]], errors="raise").to_numpy(dtype=float)
            if not np.isfinite(vals).all():
                raise ValueError("REGRESSION_TARGET_MUST_BE_FINITE_NUMERIC")
            frame[task["target"]] = vals
        entity = task["entity_column"]
        if entity and frame[entity].isna().any():
            raise ValueError("ENTITY_COLUMN_HAS_MISSING_VALUES")
        clock = task["time_column"]
        if clock:
            parsed = pd.to_datetime(frame[clock], errors="coerce", utc=True)
            if parsed.isna().any():
                raise ValueError("TIME_COLUMN_HAS_INVALID_OR_MISSING_VALUES")
        normalized[role] = frame
    return normalized, numeric, categorical, labels


def _canonical_feature(value: Any) -> Any:
    """Per-cell, dtype-independent grouping; never learns a column schema."""
    if pd.isna(value):
        return None
    try:
        numeric = float(value)
    except (ValueError, TypeError, OverflowError):
        return {"string": str(value)}
    if not math.isfinite(numeric):
        raise ValueError("NONFINITE_FEATURE_VALUE")
    return {"numeric": numeric}


def _groups(frame: pd.DataFrame, features: list[str], entity: str | None, id_columns: list[str] | None = None) -> tuple[list[str], list[str]]:
    hashes = [_hash(_json([_canonical_feature(v) for v in row]))
              for row in frame[features].itertuples(index=False, name=None)]
    parent = list(range(len(frame)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen: dict[tuple, int] = {}
    for i, h in enumerate(hashes):
        keys = [("feature", h)]
        if entity:
            keys.append(("entity", str(frame.iloc[i][entity])))
        keys.extend(("record_id", column, str(frame.iloc[i][column])) for column in (id_columns or []))
        for key in keys:
            if key in seen:
                left, right = find(i), find(seen[key])
                parent[max(left, right)] = min(left, right)
            else:
                seen[key] = i
    members: dict[int, list[str]] = {}
    for i, h in enumerate(hashes):
        members.setdefault(find(i), []).append(h)
    labels = {root: _hash(_json(sorted(set(items)))) for root, items in members.items()}
    return hashes, [labels[find(i)] for i in range(len(frame))]


def _partition(frame: pd.DataFrame, groups: list[str], fractions: list[float], names: list[str], task: dict) -> dict[str, list[int]]:
    # Source-first order keeps a heldout value's changed hash from reordering
    # every group before the fixed-seed permutation. Duplicate links stay whole.
    unique = list(dict.fromkeys(groups))
    if len(unique) < len(names):
        raise ValueError("INSUFFICIENT_DISTINCT_GROUPS_FOR_FIXED_SPLIT")
    group_positions = {g: [] for g in unique}
    for i, g in enumerate(groups):
        group_positions[g].append(i)
    if task["split_mode"] == "time":
        times = pd.to_datetime(frame[task["time_column"]], utc=True)
        unique.sort(key=lambda g: (times.iloc[group_positions[g]].min(), g))
    else:
        np.random.default_rng(task["seed"]).shuffle(unique)
    # Boundaries are fixed fractions of groups, never adjusted using labels/loss.
    cuts = [0] + [int(len(unique)*sum(fractions[:i])) for i in range(1, len(names))] + [len(unique)]
    if any(a == b for a, b in zip(cuts, cuts[1:])):
        raise ValueError("FIXED_SPLIT_HAS_EMPTY_PARTITION")
    result = {name: sorted(i for g in unique[a:b] for i in group_positions[g])
              for name, a, b in zip(names, cuts, cuts[1:])}
    if task["split_mode"] == "time":
        _chronology({name: frame.iloc[pos] for name, pos in result.items()}, names, task)
    return result


def _chronology(parts: dict[str, pd.DataFrame], order: list[str], task: dict) -> None:
    clock = task["time_column"]
    for left, right in zip(order, order[1:]):
        l = pd.to_datetime(parts[left][clock], utc=True)
        r = pd.to_datetime(parts[right][clock], utc=True)
        if l.max() >= r.min():
            raise ValueError(f"TIME_SPLIT_REQUIRES_STRICT_CHRONOLOGY:{left}:{right}")


def _isolation(parts: dict[str, pd.DataFrame], features: list[str], task: dict) -> None:
    names = list(parts)
    hashes = {n: set(_groups(parts[n], features, None)[0]) for n in names}
    for j, left in enumerate(names):
        for right in names[j+1:]:
            if hashes[left] & hashes[right]:
                raise ValueError(f"FEATURE_GROUP_OVERLAP:{left}:{right}")
            for column in task["id_columns"]:
                if set(parts[left][column].map(str)) & set(parts[right][column].map(str)):
                    raise ValueError(f"RECORD_ID_OVERLAP:{column}:{left}:{right}")
            entity = task["entity_column"]
            if entity and set(parts[left][entity].map(str)) & set(parts[right][entity].map(str)):
                raise ValueError(f"ENTITY_OVERLAP:{left}:{right}")


def _pipeline(numeric: list, categorical: list, kind: str, seed: int) -> Pipeline:
    transformers = []
    if numeric:
        transformers.append(("numeric", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True,
            keep_empty_features=True)), ("scale", StandardScaler())]), numeric))
    if categorical:
        transformers.append(("categorical", Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="__MISSING__",
            missing_values=None, keep_empty_features=True)), ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]), categorical))
    estimator = (LogisticRegression(C=1., max_iter=2000, tol=1e-5, solver="lbfgs", random_state=seed)
        if kind == "classification" else Ridge(alpha=1., solver="cholesky"))
    return Pipeline([("preprocess", ColumnTransformer(transformers, sparse_threshold=0)), ("model", estimator)])


def _predict(model: Pipeline, frame: pd.DataFrame, kind: str) -> np.ndarray:
    pred = model.predict_proba(frame)[:, 1] if kind == "classification" else model.predict(frame)
    if not np.isfinite(pred).all():
        raise ValueError("NONFINITE_MODEL_PREDICTION")
    return np.asarray(pred, dtype=float)


def _binding(frame: pd.DataFrame) -> dict:
    digest = _table_hash(frame)
    return {"content_hash": digest, "version": f"{ENGINE_VERSION}-{digest[:16]}",
        "hash_basis": "canonical_dataframe_csv_not_original_uploaded_bytes", "row_count": len(frame),
        "columns": frame.columns.tolist()}


def _model_state_hash(model: Pipeline) -> str:
    """Hash learned numeric state without exporting an executable model file."""
    states = {}
    objects = [("model", model.named_steps["model"])]
    preprocessing = model.named_steps["preprocess"]
    for name, transform, columns in preprocessing.transformers_:
        if hasattr(transform, "named_steps"):
            objects.extend((f"{name}.{step}", obj) for step, obj in transform.named_steps.items())
    for name, obj in objects:
        states[name] = {}
        for attr in ("statistics_", "categories_", "mean_", "scale_", "var_", "coef_", "intercept_", "classes_", "n_iter_"):
            if hasattr(obj, attr):
                value = getattr(obj, attr)
                states[name][attr] = ([v.tolist() for v in value] if attr == "categories_" else np.asarray(value).tolist())
        if hasattr(obj, "indicator_") and obj.indicator_ is not None:
            states[name]["missing_indicator_features"] = obj.indicator_.features_.tolist()
    return _hash(_json(states))


def read_report(output_dir: Path) -> dict:
    """Read the report produced by this new measurement engine."""
    return json.loads((Path(output_dir)/"report.json").read_text(encoding="utf-8"))


@threadpool_limits.wrap(limits=1)
def run_measurement(candidate: pd.DataFrame, task: dict, output_dir: Path, *,
    buyer: pd.DataFrame | None = None, validation: pd.DataFrame | None = None,
    test: pd.DataFrame | None = None, input_records: dict | None = None) -> dict:
    """Perform fixed-protocol actual fits and paired evaluation on new inputs."""
    settings = _settings(task)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out/"report.json").exists() or (out/"protocol.json").exists():
        raise ValueError("OUTPUT_DIRECTORY_ALREADY_CONTAINS_MEASUREMENT")
    tables = {"candidate": candidate}
    tables.update({n: f for n, f in (("buyer", buyer), ("validation", validation), ("test", test)) if f is not None})
    for role, frame in tables.items():
        if not isinstance(frame, pd.DataFrame):
            raise ValueError(f"{role.upper()}_MUST_BE_DATAFRAME")
    if sum(len(f) for f in tables.values()) > MAX_ROWS:
        raise ValueError("COMBINED_INPUT_ROWS_EXCEED_10000")
    if any(len(f.columns) > MAX_COLUMNS for f in tables.values()):
        raise ValueError("INPUT_COLUMNS_EXCEED_64")
    binding = {"registered_inputs": copy.deepcopy(input_records or {}),
        "tables": {name: _binding(f) for name, f in tables.items()},
        "registered_bytes_and_canonical_tables_are_distinct": True}
    missing = []
    if settings["task_type"] not in {"classification", "regression"}:
        missing.append("TASK_TYPE_NOT_SPECIFIED_OR_UNSUPPORTED")
    if not isinstance(settings["target"], str) or not settings["target"]:
        missing.append("TARGET_NOT_SPECIFIED")
    elif any(settings["target"] not in f.columns for f in tables.values()):
        missing.append("TARGET_COLUMN_NOT_PRESENT")
    if missing:
        result = {"status": "NOT_EVALUATED", "engine_version": ENGINE_VERSION,
            "reasons": missing, "task": settings, "input_binding": binding, "fit_count": 0,
            "splits": {}, "validation": {}, "test": {}, "marginal_utility": {"validation": None, "test": None},
            "trust": {"status": "NEED_MORE_EVIDENCE", "factual_truth_verified": False},
            "recommendation": {"action": "do_not_use", "uses_test": False, "reason": "TASK_NOT_EVALUATED"}}
        _write(out/"report.json", result)
        return result
    if (validation is None) != (test is None):
        raise ValueError("EXPLICIT_VALIDATION_AND_TEST_MUST_BE_PROVIDED_TOGETHER")
    simulated = settings["buyer_mode"] == "SIMULATED_BUYER_SCENARIO"
    if simulated and any(v is not None for v in (buyer, validation, test)):
        raise ValueError("SIMULATED_MODE_USES_ONE_CANDIDATE_TABLE_ONLY")
    if not simulated and buyer is None:
        raise ValueError("REAL_BUYER_MODE_REQUIRES_BUYER_TABLE")
    features = _validate_tables(tables, settings)
    # Raw values are used only for canonical duplicate/ID/entity grouping here.
    # No full-table dtype, test category count or target distribution sets schema.
    grouping_entity = settings["entity_column"]
    grouping_ids = settings["id_columns"]
    source_membership = []
    if simulated:
        frame = tables["candidate"].reset_index(drop=True)
        _, group_ids = _groups(frame, features, grouping_entity, grouping_ids)
        positions = _partition(frame, group_ids, [.35,.25,.20,.20], ["D0", "Di", "validation", "test"], settings)
        parts = {name: frame.iloc[pos].reset_index(drop=True) for name, pos in positions.items()}
        for name, pos in positions.items():
            source_membership.extend({"source": "candidate", "source_row": i, "role": name, "group_id": group_ids[i]} for i in pos)
    else:
        frame = tables["buyer"].reset_index(drop=True)
        _, group_ids = _groups(frame, features, grouping_entity, grouping_ids)
        if validation is None:
            positions = _partition(frame, group_ids, [.60,.20,.20], ["D0", "validation", "test"], settings)
            parts = {name: frame.iloc[pos].reset_index(drop=True) for name, pos in positions.items()}
            for name, pos in positions.items():
                source_membership.extend({"source": "buyer", "source_row": i, "role": name, "group_id": group_ids[i]} for i in pos)
        else:
            parts = {"D0": frame, "validation": tables["validation"].reset_index(drop=True), "test": tables["test"].reset_index(drop=True)}
            for name, source in (("D0", "buyer"), ("validation", "validation"), ("test", "test")):
                _, ids = _groups(parts[name], features, grouping_entity, grouping_ids)
                source_membership.extend({"source": source, "source_row": i, "role": name, "group_id": ids[i]} for i in range(len(ids)))
        parts["Di"] = tables["candidate"].reset_index(drop=True)
        _, ids = _groups(parts["Di"], features, grouping_entity, grouping_ids)
        source_membership.extend({"source": "candidate", "source_row": i, "role": "Di", "group_id": ids[i]} for i in range(len(ids)))
    _isolation(parts, features, settings)
    parts, numeric, categorical, labels = _normalize(parts, features, settings)
    _isolation(parts, features, settings)
    if settings["split_mode"] == "time":
        if simulated:
            _chronology(parts, ["D0", "Di", "validation", "test"], settings)
        else:
            combined_train = pd.concat([parts["D0"], parts["Di"]], ignore_index=True)
            _chronology({"train": combined_train, "validation": parts["validation"], "test": parts["test"]},
                ["train", "validation", "test"], settings)
    for split in ("validation", "test"):
        if len(set(_groups(parts[split], features, grouping_entity, grouping_ids)[1])) < 2:
            raise ValueError(f"{split.upper()}_REQUIRES_AT_LEAST_TWO_GROUPS")
    train = {"base": parts["D0"], "augmented": pd.concat([parts["D0"], parts["Di"]], ignore_index=True)}
    if settings["task_type"] == "classification" and any(f[settings["target"]].nunique() != 2 for f in train.values()):
        raise ValueError("FIXED_TRAINING_SPLIT_REQUIRES_BOTH_CLASSES_NO_SEED_RETRY")
    split_info = {name: {**_binding(f), "group_count": len(set(_groups(f, features, grouping_entity, grouping_ids)[1]))} for name, f in parts.items()}
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "threadpoolctl")}
    protocol = {"engine_version": ENGINE_VERSION, "task": settings, "input_binding": binding,
        "feature_columns": features, "numeric_columns": numeric, "categorical_columns": categorical,
        "class_labels": labels, "splits": split_info, "software_versions": versions,
        "models": {"classification": {"name": "LogisticRegression", "C": 1., "max_iter": 2000, "tol": 1e-5, "solver": "lbfgs"},
            "regression": {"name": "Ridge", "alpha": 1., "solver": "cholesky"}},
        "preprocessing": "train-only median+indicator+scaling numeric; constant missing+onehot-ignore categorical",
        "schema_inference": "D0 observed values only: numeric iff all parse; capacity from D0+Di only; validation/test must satisfy frozen schema",
        "grouping_canonicalization": "per-cell finite number and numeric string share numeric token; otherwise exact string; no column dtype inference",
        "split_fraction_basis": "connected_components_not_individual_rows",
        "bootstrap_family_size": 1, "seed_aggregation": "mean paired row losses over fixed model seeds; seeds are not independent samples",
        "model_seed_effect": "lbfgs and cholesky are deterministic for fixed data; repeated seeds do not create independent uncertainty",
        "ci_scope": CI_SCOPE, "test_policy": "validation saved and hash-frozen before any test prediction"}
    protocol["implementation_hashes"] = {"engine.py": _hash(Path(__file__).read_bytes()),
        "scenario_value/core.py": _hash(Path(value_core.__file__).read_bytes())}
    protocol["isolation_assessment"] = {"record_feature_groups": "SEPARATED",
        "entity": "SUPPLIED_ENTITY_COMPONENTS_SEPARATED" if settings["entity_column"] else "UNKNOWN_NO_ENTITY_COLUMN",
        "time": "STRICT_CHRONOLOGY_CHECKED_NOT_ASOF_CERTIFICATION" if settings["split_mode"] == "time" else "UNKNOWN_TEMPORAL_GENERALIZATION_NOT_VERIFIED",
        "interval_for_time": "NOT_EVALUATED_FOR_TEMPORAL_DEPENDENCE" if settings["split_mode"] == "time" else "CONDITIONAL_EXCHANGEABLE_GROUP_BOOTSTRAP"}
    protocol_hash = _write(out/"protocol.json", protocol)
    pd.DataFrame(source_membership).to_csv(out/"split_membership.csv", index=False, lineterminator="\n")
    fit_records = []
    fitted = []
    kind, target = settings["task_type"], settings["target"]
    for seed in settings["model_seeds"]:
        models = {}
        for role, data in train.items():
            model = _pipeline(numeric, categorical, kind, seed)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    model.fit(data[features], data[target].to_numpy())
                except (ValueError, TypeError, FloatingPointError) as exc:
                    _write(out/"fit_failure.json", {"status": "INCOMPLETE", "role": role,
                        "model_seed": seed, "reason": str(exc), "completed_fit_records": fit_records})
                    raise ValueError(f"MODEL_FIT_FAILED:{role}:{type(exc).__name__}:{exc}") from exc
            width = model.named_steps["preprocess"].transform(data[features].iloc[:1]).shape[1]
            if width > 512:
                raise ValueError("ACTUAL_TRANSFORMED_COLUMNS_EXCEED_512")
            models[role] = model
            fit_records.append({"model_seed": seed, "role": role, "training_rows": len(data),
                "training_target_count": int(data[target].count()),
                "dummy_value": float(data[target].mean() if kind == "classification" else data[target].median()),
                "dummy_statistic": "mean" if kind == "classification" else "median",
                "training_hash": _table_hash(data), "transformed_columns": width,
                "model_state_sha256": _model_state_hash(model),
                "convergence_warning": any(issubclass(w.category, ConvergenceWarning) for w in caught),
                "warnings": [str(w.message) for w in caught], "actual_fit_executed": True})
        fitted.append((seed, models))
    fit_records_hash = _write(out/"fit_records.json", fit_records)
    predictions = []
    metrics = []

    def evaluate(split: str) -> dict:
        data = parts[split]
        y = data[target].to_numpy(dtype=float)
        feature_hashes, evaluation_groups = _groups(data, features, grouping_entity, grouping_ids)
        base_losses, augmented_losses, reliabilities, per_seed = [], [], [], []
        for seed, models in fitted:
            preds = {role: _predict(model, data[features], kind) for role, model in models.items()}
            loss = {role: per_row_losses(y, p, kind) for role, p in preds.items()}
            dummy_value = {role: float(f[target].mean() if kind == "classification" else f[target].median()) for role, f in train.items()}
            dummy_losses = {role: per_row_losses(y, np.full(len(y), v), kind) for role, v in dummy_value.items()}
            base_losses.append(loss["base"])
            augmented_losses.append(loss["augmented"])
            row = {"split": split, "model_seed": seed, "base_loss": float(loss["base"].mean()),
                "augmented_loss": float(loss["augmented"].mean()), "delta_u": float(np.mean(loss["base"]-loss["augmented"])),
                "base_dummy_loss": float(dummy_losses["base"].mean()), "augmented_dummy_loss": float(dummy_losses["augmented"].mean())}
            metrics.append(row)
            per_seed.append(row.copy())
            if split == "validation":
                converged = {r: not next(f["convergence_warning"] for f in fit_records if f["role"] == r and f["model_seed"] == seed) for r in train}
                reliability = validation_model_reliability(row["base_loss"], row["base_dummy_loss"], row["augmented_loss"], row["augmented_dummy_loss"],
                    base_fit_success=converged["base"], candidate_fit_success=converged["augmented"])
                reliabilities.append(reliability)
            for i in range(len(data)):
                predictions.append({"split": split, "model_seed": seed, "evaluation_row": i, "group_id": evaluation_groups[i],
                    "feature_hash": feature_hashes[i], "target": float(y[i]), "base_prediction": float(preds["base"][i]),
                    "augmented_prediction": float(preds["augmented"][i]), "base_loss": float(loss["base"][i]),
                    "augmented_loss": float(loss["augmented"][i]), "paired_loss_difference": float(loss["base"][i]-loss["augmented"][i]),
                    "base_dummy_loss": float(dummy_losses["base"][i]), "augmented_dummy_loss": float(dummy_losses["augmented"][i])})
        mean_base = np.mean(base_losses, axis=0)
        mean_augmented = np.mean(augmented_losses, axis=0)
        temporal = settings["split_mode"] == "time"
        if temporal:
            grouped = []
            for group_id in sorted(set(evaluation_groups)):
                mask = np.asarray(evaluation_groups) == group_id
                grouped.append({"group_id": group_id, "row_count": int(mask.sum()),
                    "base_loss_sum": float(mean_base[mask].sum()),
                    "candidate_loss_sums": {"Di": float(mean_augmented[mask].sum())},
                    "delta_loss_sums": {"Di": float((mean_base[mask]-mean_augmented[mask]).sum())}})
            bootstrap = {"status": "NOT_EVALUATED_FOR_TEMPORAL_DEPENDENCE", "group_count": len(grouped),
                "groups": grouped, "bootstrap_draws": None, "draws_sha256": None,
                "bootstrap_replicates": 0, "assets": {"Di": {"delta_u": float(np.mean(mean_base-mean_augmented)),
                    "base_loss_mean": float(mean_base.mean()), "asset_loss_mean": float(mean_augmented.mean()),
                    "ci_lower": None, "ci_upper": None, "value_status": "NOT_EVALUATED_FOR_TEMPORAL_DEPENDENCE"}},
                "reason": "Strict chronological separation does not establish exchangeable evaluation groups. No temporal bootstrap method is assumed."}
        else:
            bootstrap = paired_group_bootstrap(evaluation_groups, mean_base, {"Di": mean_augmented},
                n_bootstrap=settings["n_bootstrap"], seed=settings["seed"]+(1 if split == "test" else 0),
                alpha=settings["alpha"], family_size=1, simultaneous=(split == "validation"), include_draws=True)
        _write(out/f"bootstrap_{split}.json", bootstrap)
        summary = dict(bootstrap["assets"]["Di"])
        summary.update({"row_count": len(data), "group_count": bootstrap["group_count"], "per_seed": per_seed,
            "loss_name": "log_loss" if kind == "classification" else "mae",
            "loss_unit": "natural_log_units" if kind == "classification" else settings["target_unit"],
            "ci_scope": bootstrap["reason"] if temporal else CI_SCOPE,
            "bootstrap_replicates": bootstrap["bootstrap_replicates"], "bootstrap_draws_sha256": bootstrap["draws_sha256"],
            "uses_test_for_recommendation": False})
        if split == "validation":
            reliable = all(r["model_reliable"] for r in reliabilities)
            summary.update(model_reliable=reliable, reliability_by_seed=reliabilities,
                value_status="NOT_EVALUATED_FOR_TEMPORAL_DEPENDENCE" if temporal else value_evidence_status(summary, model_reliable=reliable))
        else:
            summary.update(evaluation_use="POSTHOC_ONLY_NOT_AN_INPUT_TO_CURRENT_RECOMMENDATION")
        return summary

    val_result = evaluate("validation")
    recommendation = {"action": "consider_research_use" if val_result["value_status"] == "POSITIVE_VALUE_EVIDENCE" else "do_not_use",
        "uses_test": False, "value_status": val_result["value_status"], "formal_factual_use": "NEED_MORE_EVIDENCE",
        "reason": "VALIDATION_LOWER_BOUND_AND_MODEL_RELIABILITY", "no_purchase_delta_u": 0.0}
    freeze = {"protocol_hash": protocol_hash, "validation": val_result, "recommendation": recommendation,
        "fit_records_sha256": fit_records_hash, "model_state_sha256": [r["model_state_sha256"] for r in fit_records],
        "test_predictions_executed": False, "validation_decision_uses_test": False}
    freeze_hash = _write(out/"validation_freeze.json", freeze)
    if _hash((out/"protocol.json").read_bytes()) != protocol_hash or _hash((out/"fit_records.json").read_bytes()) != fit_records_hash:
        raise ValueError("PROTOCOL_OR_FIT_RECORDS_CHANGED_BEFORE_TEST")
    with (out/"test_started.json").open("x", encoding="utf-8") as marker:
        json.dump({"protocol_hash": protocol_hash, "validation_freeze_sha256": freeze_hash,
            "replay_scope": "same-input runs are reproductions, not new blind measurements"}, marker, sort_keys=True)
    test_result = evaluate("test")
    if _hash((out/"validation_freeze.json").read_bytes()) != freeze_hash:
        raise ValueError("VALIDATION_FREEZE_CHANGED_DURING_TEST_EVALUATION")
    pd.DataFrame(metrics).to_csv(out/"metrics.csv", index=False, lineterminator="\n", float_format="%.17g")
    pd.DataFrame(predictions).to_csv(out/"predictions.csv", index=False, lineterminator="\n", float_format="%.17g")
    result = {"status": "EVALUATED", "engine_version": ENGINE_VERSION, "task": settings,
        "input_binding": binding, "protocol_hash": protocol_hash, "validation_freeze_sha256": freeze_hash,
        "splits": split_info, "fit_count": len(fit_records), "validation": val_result, "test": test_result,
        "isolation_assessment": protocol["isolation_assessment"],
        "marginal_utility": {"validation": val_result["delta_u"], "test": test_result["delta_u"]},
        "trust": {"status": "NEED_MORE_EVIDENCE", "factual_truth_verified": False}, "recommendation": recommendation,
        "limitations": [CI_SCOPE, "Feature/entity isolation cannot prove independent entities or factual labels.",
            "Fixed model seeds share evaluation rows and are not independent replications.",
            "Conditional task utility is not a market price or future guarantee."],
        "artifacts": {p.name: {"sha256": _hash(p.read_bytes()), "bytes": p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()}}
    _write(out/"report.json", result)
    return result
