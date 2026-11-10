"""Fixed-sample reference conformance, with unresolved records kept in n.

The HG interval describes a specified record/field comparison protocol relative
to pinned reference bytes. It never certifies the facts in either CSV.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
from numbers import Integral, Real
from pathlib import Path
import random
import re
from typing import Any

import numpy as np
import pandas as pd

# Loading the exact frozen file avoids audit_risk/__init__.py, whose historical
# evidence exports otherwise pull cryptography into this purely numeric API.
_HG_PATH = Path(__file__).resolve().parents[1] / "audit_risk" / "intervals.py"
_HG_SPEC = importlib.util.spec_from_file_location("_self_service_trust_frozen_hg_intervals", _HG_PATH)
if _HG_SPEC is None or _HG_SPEC.loader is None:
    raise ImportError("FROZEN_HG_INTERVAL_MODULE_NOT_AVAILABLE")
_HG_MODULE = importlib.util.module_from_spec(_HG_SPEC)
_HG_SPEC.loader.exec_module(_HG_MODULE)
finite_population_interval = _HG_MODULE.finite_population_interval
interval_decision = _HG_MODULE.interval_decision
HG_SOURCE_SHA256 = hashlib.sha256(_HG_PATH.read_bytes()).hexdigest()

METHOD_VERSION = "self_service_trust-fixed-reference-conformance-v1"
_CONFIG_KEYS = {"key_mapping", "rules", "sample_size", "seed", "epsilon", "alpha", "sampling_method", "positions"}
_RULE_KEYS = {"candidate_column", "reference_column", "comparison", "atol", "rtol"}
_RELATIONSHIPS = {"SAME_UPSTREAM", "UNKNOWN", "DECLARED_INDEPENDENT"}


def canonical_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("FINITE_JSON_DOCUMENT_REQUIRED") from exc
    return hashlib.sha256(raw).hexdigest()


def _integer(value, name: str, minimum=0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name}_MUST_BE_INTEGER_AT_LEAST_{minimum}")
    return int(value)


def _number(value, name: str, *, probability=False, interior=False) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name}_MUST_BE_FINITE_NUMBER")
    result = float(value)
    if probability and not (0 < result < 1 if interior else 0 <= result <= 1):
        raise ValueError(f"{name}_OUT_OF_RANGE")
    return result


def _missing(value) -> bool:
    return bool(pd.isna(value)) or (isinstance(value, str) and not value.strip())


def _scalar(value):
    """A typed, finite token. Numeric 1 and 1.0 match; text '1' stays text."""
    if _missing(value):
        return ["missing", None]
    if isinstance(value, (bool, np.bool_)):
        return ["bool", bool(value)]
    if isinstance(value, Integral):
        return ["number", str(int(value))]
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            return ["nonfinite", str(number)]
        return ["number", str(int(number)) if number.is_integer() else format(number, ".17g")]
    if isinstance(value, str):
        return ["text", value]
    raise ValueError("CSV_SCALAR_VALUES_REQUIRED")


def frame_hash(frame: pd.DataFrame) -> str:
    return canonical_hash(frame_payload(frame))


def frame_payload(frame: pd.DataFrame) -> dict:
    """A portable typed-frame artifact, separate from the raw-byte identity."""
    return {"columns": list(frame.columns),
        "rows": [[_scalar(value) for value in row] for row in frame.itertuples(index=False, name=None)]}


def _frame(value, role: str) -> pd.DataFrame:
    frame = value.get("frame") if isinstance(value, Mapping) else value
    if (not isinstance(frame, pd.DataFrame) or not len(frame) or not len(frame.columns)
            or frame.columns.has_duplicates
            or any(not isinstance(c, str) or not c.strip() for c in frame.columns)):
        raise ValueError(f"{role.upper()}_NONEMPTY_DISTINCT_COLUMN_DATAFRAME_REQUIRED")
    return frame


def _descriptor(value, frame: pd.DataFrame, binding: Mapping, role: str) -> tuple[dict, bool]:
    if not isinstance(binding, Mapping):
        raise ValueError(f"{role.upper()}_BINDING_REQUIRED")
    descriptor = deepcopy(dict(binding))
    for name in ("dataset_id", "version", "content_hash"):
        if not isinstance(descriptor.get(name), str) or not descriptor[name].strip():
            raise ValueError(f"{role.upper()}_{name.upper()}_REQUIRED")
    digest = descriptor["content_hash"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"{role.upper()}_SHA256_REQUIRED")
    if descriptor["version"].startswith("sha256:") and descriptor["version"] != "sha256:" + digest:
        raise ValueError(f"{role.upper()}_HASH_VERSION_MISMATCH")
    declared_columns = descriptor.get("columns", descriptor.get("schema"))
    if declared_columns is None:
        raise ValueError(f"{role.upper()}_SCHEMA_BINDING_REQUIRED")
    if isinstance(declared_columns, Mapping):
        column_names = list(declared_columns)
        matches = set(column_names) == set(frame.columns)
    elif isinstance(declared_columns, list):
        column_names = [c.get("name") if isinstance(c, Mapping) else c for c in declared_columns]
        matches = column_names == frame.columns.tolist()
    else:
        raise ValueError(f"{role.upper()}_INVALID_SCHEMA_BINDING")
    if not matches:
        raise ValueError(f"{role.upper()}_SCHEMA_BINDING_MISMATCH")
    if "row_count" in descriptor and descriptor["row_count"] != len(frame):
        raise ValueError(f"{role.upper()}_ROW_COUNT_BINDING_MISMATCH")
    if "column_count" in descriptor and descriptor["column_count"] != len(frame.columns):
        raise ValueError(f"{role.upper()}_COLUMN_COUNT_BINDING_MISMATCH")
    raw = value.get("raw_bytes") if isinstance(value, Mapping) else frame.attrs.get("raw_bytes")
    verified = raw is not None
    if verified:
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"{role.upper()}_RAW_BYTES_HASH_MISMATCH")
    canonical_hash(descriptor)
    return descriptor, verified


def _config(config: Mapping, candidate: pd.DataFrame, reference: pd.DataFrame) -> dict:
    if not isinstance(config, Mapping) or set(config) - _CONFIG_KEYS:
        raise ValueError("UNKNOWN_OR_INVALID_AUDIT_CONFIG_FIELDS")
    if not {"key_mapping", "rules", "sample_size", "seed", "epsilon", "alpha"} <= set(config):
        raise ValueError("AUDIT_CONFIG_REQUIRED_FIELDS_MISSING")
    mapping = config["key_mapping"]
    if not isinstance(mapping, Mapping) or not mapping:
        raise ValueError("NONEMPTY_KEY_MAPPING_REQUIRED")
    if (any(not isinstance(k, str) or not isinstance(v, str) or k not in candidate.columns or v not in reference.columns
            for k, v in mapping.items()) or len(set(mapping.values())) != len(mapping)):
        raise ValueError("KEY_MAPPING_MUST_MAP_DISTINCT_EXISTING_COLUMNS")
    rules = config["rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("AT_LEAST_ONE_NONKEY_FIELD_RULE_REQUIRED")
    normalized = []
    pairs = set()
    for rule in rules:
        if not isinstance(rule, Mapping) or set(rule) - _RULE_KEYS:
            raise ValueError("UNKNOWN_OR_INVALID_COMPARISON_RULE_FIELDS")
        if not {"candidate_column", "reference_column", "comparison"} <= set(rule):
            raise ValueError("COMPARISON_RULE_FIELDS_REQUIRED")
        left, right, comparison = rule["candidate_column"], rule["reference_column"], rule["comparison"]
        if (not isinstance(left, str) or not isinstance(right, str)
                or left not in candidate.columns or right not in reference.columns or left in mapping or right in mapping.values()):
            raise ValueError("RULES_MUST_COMPARE_EXISTING_NONKEY_FIELDS")
        if (left, right) in pairs:
            raise ValueError("DUPLICATE_FIELD_RULE")
        pairs.add((left, right))
        if not isinstance(comparison, str) or comparison not in {"exact", "numeric"}:
            raise ValueError("COMPARISON_MUST_BE_EXACT_OR_NUMERIC")
        atol, rtol = _number(rule.get("atol", 0.), "ATOL"), _number(rule.get("rtol", 0.), "RTOL")
        if atol < 0 or rtol < 0 or (comparison == "exact" and (atol or rtol)):
            raise ValueError("NONNEGATIVE_TOLERANCES_FOR_NUMERIC_RULES_ONLY")
        normalized.append({"candidate_column": left, "reference_column": right,
            "comparison": comparison, "atol": atol, "rtol": rtol})
    n = _integer(config["sample_size"], "SAMPLE_SIZE")
    seed = _integer(config["seed"], "SEED")
    if n > len(candidate):
        raise ValueError("SAMPLE_SIZE_EXCEEDS_CANDIDATE_POPULATION")
    method = config.get("sampling_method", "SRSWOR")
    if not isinstance(method, str) or method not in {"SRSWOR", "CENSUS", "NON_PROBABILITY"}:
        raise ValueError("INVALID_SAMPLING_METHOD")
    result = {"key_mapping": dict(mapping), "rules": normalized, "sample_size": n, "seed": seed,
        "epsilon": _number(config["epsilon"], "EPSILON", probability=True),
        "alpha": _number(config["alpha"], "ALPHA", probability=True, interior=True), "sampling_method": method}
    if method == "CENSUS" and n != len(candidate):
        raise ValueError("CENSUS_MUST_INCLUDE_ALL_CANDIDATE_RECORDS")
    if method == "NON_PROBABILITY":
        positions = config.get("positions")
        if not isinstance(positions, list) or len(positions) != n:
            raise ValueError("NONPROBABILITY_POSITIONS_MUST_MATCH_SAMPLE_SIZE")
        positions = [_integer(p, "POSITION") for p in positions]
        if len(set(positions)) != n or any(p >= len(candidate) for p in positions):
            raise ValueError("POSITIONS_MUST_BE_UNIQUE_AND_IN_RANGE")
        result["positions"] = sorted(positions)
    elif "positions" in config:
        raise ValueError("PROBABILITY_SAMPLE_POSITIONS_MUST_BE_GENERATED_NOT_SUPPLIED")
    return result


def _keys(frame: pd.DataFrame, columns: list[str], role: str) -> list[str]:
    result = []
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        if any(_missing(value) or (_scalar(value)[0] == "nonfinite") for value in row):
            raise ValueError(f"{role.upper()}_KEYS_MUST_BE_NONEMPTY_FINITE")
        result.append(json.dumps([_scalar(value) for value in row], ensure_ascii=False, separators=(",", ":")))
    if len(set(result)) != len(result):
        raise ValueError(f"{role.upper()}_KEYS_MUST_BE_UNIQUE")
    return result


def _positions(total: int, config: dict) -> list[int]:
    if config["sampling_method"] == "CENSUS":
        return list(range(total))
    if config["sampling_method"] == "NON_PROBABILITY":
        return config["positions"].copy()
    return sorted(random.Random(config["seed"]).sample(range(total), config["sample_size"]))


def make_plan(candidate, reference, config: Mapping, bindings: Mapping) -> dict:
    """Commit a candidate-row sample before inspecting comparison outcomes.

    Inputs are DataFrames plus registered descriptors in bindings. Optional
    DataFrame.attrs['raw_bytes'] (or {frame,raw_bytes}) enables an extra raw SHA
    check. The gateway must authenticate raw registration bytes independently.
    """
    left, right = _frame(candidate, "candidate"), _frame(reference, "reference")
    if not isinstance(bindings, Mapping) or set(bindings) != {"candidate", "reference", "source_declaration"}:
        raise ValueError("CANDIDATE_REFERENCE_AND_SOURCE_BINDINGS_REQUIRED")
    normalized = _config(config, left, right)
    # Validate keys over entire tables, never silently inner-join an eligible subset.
    _keys(left, list(normalized["key_mapping"]), "candidate")
    _keys(right, list(normalized["key_mapping"].values()), "reference")
    pinned, raw_checks = {}, {}
    for role, value, frame in (("candidate", candidate, left), ("reference", reference, right)):
        pinned[role], raw_checks[role] = _descriptor(value, frame, bindings[role], role)
    source = bindings["source_declaration"]
    allowed_source = {"description", "source_url", "license", "observed_at", "version", "relationship"}
    if not isinstance(source, Mapping) or set(source) - allowed_source:
        raise ValueError("INVALID_SOURCE_DECLARATION_FIELDS")
    if (not isinstance(source.get("relationship", "UNKNOWN"), str)
            or source.get("relationship", "UNKNOWN") not in _RELATIONSHIPS):
        raise ValueError("INVALID_DECLARED_SOURCE_RELATIONSHIP")
    if any(not isinstance(v, str) for v in source.values()):
        raise ValueError("SOURCE_DECLARATIONS_MUST_BE_TEXT")
    pinned["source_declaration"] = deepcopy(dict(source))
    canonical_hash(pinned)
    plan = {"method_version": METHOD_VERSION, "config": normalized, "bindings": pinned,
        "interval_implementation_sha256": HG_SOURCE_SHA256,
        "population_size": len(left), "sample_size": normalized["sample_size"],
        "sampled_positions": _positions(len(left), normalized),
        "candidate_frame_hash": frame_hash(left), "reference_frame_hash": frame_hash(right),
        "raw_bytes_verified_by_core_at_plan": raw_checks,
        "sampling_algorithm": "python_random_sample_without_replacement_sorted_v1" if normalized["sampling_method"] == "SRSWOR" else normalized["sampling_method"],
        "comparison_scope": "AT_LEAST_ONE_DECLARED_NONKEY_FIELD_DISAGREES_RELATIVE_TO_PINNED_REFERENCE",
        "plan_registration": "LOCAL_HASH_COMMITMENT_NOT_EXTERNAL_IMMUTABLE_PREREGISTRATION"}
    return dict(plan, plan_hash=canonical_hash(plan))


def _compare(left, right, rule: dict) -> tuple[str, str]:
    if _missing(left) or _missing(right):
        return "INCOMPARABLE", "MISSING_CANDIDATE_OR_REFERENCE_VALUE"
    if rule["comparison"] == "exact":
        a, b = _scalar(left), _scalar(right)
        if a[0] == "nonfinite" or b[0] == "nonfinite":
            return "INCOMPARABLE", "NONFINITE_VALUE"
        return ("PASS", "EXACT_VALUES_MATCH") if a == b else ("DISAGREEMENT", "EXACT_VALUES_DIFFER")
    if isinstance(left, (bool, np.bool_)) or isinstance(right, (bool, np.bool_)):
        return "INCOMPARABLE", "BOOLEAN_IS_NOT_NUMERIC_MEASUREMENT"
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError, OverflowError):
        return "INCOMPARABLE", "NUMERIC_PARSE_FAILED"
    if not math.isfinite(a) or not math.isfinite(b):
        return "INCOMPARABLE", "NONFINITE_NUMERIC_VALUE"
    def fraction(original, parsed):
        if isinstance(original, Integral):
            return Fraction(int(original))
        if isinstance(original, str) and re.fullmatch(r"[+-]?[0-9]+", original.strip()):
            return Fraction(int(original))
        return Fraction(parsed)
    # Preserve integers above 2**53 and avoid overflow/cancellation in finite
    # float tolerance arithmetic. Fractions encode the represented values;
    # they do not recover precision absent from the original CSV parser.
    try:
        a_exact, b_exact = fraction(left, a), fraction(right, b)
    except (ValueError, OverflowError):
        return "INCOMPARABLE", "NUMERIC_EXACT_PARSE_FAILED"
    passes = abs(a_exact - b_exact) <= Fraction(rule["atol"]) + Fraction(rule["rtol"]) * abs(b_exact)
    return ("PASS", "NUMERIC_WITHIN_DECLARED_TOLERANCE") if passes else ("DISAGREEMENT", "NUMERIC_OUTSIDE_DECLARED_TOLERANCE")


def evaluate_plan(candidate, reference, plan: Mapping) -> dict:
    """Evaluate all fixed sampled positions; no result-dependent resampling."""
    if not isinstance(plan, Mapping) or not isinstance(plan.get("plan_hash"), str):
        raise ValueError("VALID_HASHED_PLAN_REQUIRED")
    payload = {key: value for key, value in plan.items() if key != "plan_hash"}
    if canonical_hash(payload) != plan["plan_hash"] or plan.get("method_version") != METHOD_VERSION:
        raise ValueError("PLAN_HASH_OR_METHOD_MISMATCH")
    if not {"config", "bindings", "raw_bytes_verified_by_core_at_plan"} <= set(plan):
        raise ValueError("REQUIRED_PLAN_FIELDS_MISSING")
    left, right = _frame(candidate, "candidate"), _frame(reference, "reference")
    expected = make_plan(candidate, reference, plan["config"], plan["bindings"])
    # Raw verification availability can change between functions; it is not the data identity.
    expected["raw_bytes_verified_by_core_at_plan"] = plan["raw_bytes_verified_by_core_at_plan"]
    expected["plan_hash"] = canonical_hash({k: v for k, v in expected.items() if k != "plan_hash"})
    if expected != dict(plan):
        raise ValueError("PLAN_INPUT_VERSION_SCHEMA_FRAME_OR_SAMPLE_CHANGED")
    config = plan["config"]
    left_keys = _keys(left, list(config["key_mapping"]), "candidate")
    right_keys = _keys(right, list(config["key_mapping"].values()), "reference")
    lookup = {key: position for position, key in enumerate(right_keys)}
    observations = []
    for position in plan["sampled_positions"]:
        reference_position = lookup.get(left_keys[position])
        fields = []
        if reference_position is not None:
            for rule in config["rules"]:
                outcome, reason = _compare(left[rule["candidate_column"]].iloc[position],
                    right[rule["reference_column"]].iloc[reference_position], rule)
                fields.append({**rule, "outcome": outcome, "reason": reason})
            outcomes = {field["outcome"] for field in fields}
            outcome = "DISAGREEMENT" if "DISAGREEMENT" in outcomes else "INCOMPARABLE" if "INCOMPARABLE" in outcomes else "PASS"
            reason = {"DISAGREEMENT": "AT_LEAST_ONE_KNOWN_FIELD_DISAGREEMENT", "INCOMPARABLE": "FIELD_COMPARISON_UNRESOLVED", "PASS": "ALL_DECLARED_NONKEY_FIELDS_MATCH"}[outcome]
        else:
            outcome, reason = "INCOMPARABLE", "KEY_NOT_FOUND_IN_REFERENCE"
        observations.append({"candidate_position": position, "reference_position": reference_position,
            "key": json.loads(left_keys[position]), "outcome": outcome, "reason": reason, "fields": fields})
    n, total = plan["sample_size"], plan["population_size"]
    passed = sum(o["outcome"] == "PASS" for o in observations)
    errors = sum(o["outcome"] == "DISAGREEMENT" for o in observations)
    unresolved = sum(o["outcome"] == "INCOMPARABLE" for o in observations)
    interval = None
    decision = "DESCRIPTIVE_ONLY"
    if config["sampling_method"] != "NON_PROBABILITY":
        low = finite_population_interval(total, n, errors, config["alpha"])
        high = finite_population_interval(total, n, errors + unresolved, config["alpha"])
        interval = {"lower": low["lower"], "upper": high["upper"],
            "error_total_lower": low["error_total_lower"], "error_total_upper": high["error_total_upper"],
            "alpha": config["alpha"], "tail_risk_each": config["alpha"] / 2,
            "method": "fixed_sample_hypergeometric_unknown_completion_envelope",
            "object": "PINNED_REFERENCE_PROTOCOL_DISAGREEMENT_RATE_NOT_FACTUAL_ERROR_RATE",
            "coverage_claim": "AT_LEAST_1_MINUS_ALPHA_CONDITIONAL_ON_FIXED_SRS_AND_COMPARISON_PROTOCOL"}
        decision = interval_decision(interval["lower"], interval["upper"], config["epsilon"])
    return {"status": "EVALUATED", "method_version": METHOD_VERSION, "plan_hash": plan["plan_hash"],
        "population_size": total, "sample_size": n, "sampled_positions": list(plan["sampled_positions"]),
        "observations": observations,
        "counts": {"population_size": total, "sample_size": n, "comparable": n - unresolved,
            "passed": passed, "disagreements": errors, "incomparable": unresolved},
        "sample_disagreement_fraction_among_comparable": errors / (n - unresolved) if n > unresolved else None,
        "sample_disagreement_fraction_lower": errors / n if n else None,
        "sample_disagreement_fraction_upper": (errors + unresolved) / n if n else None,
        "disagreement_interval": interval, "statistical_decision": decision,
        "epsilon": config["epsilon"], "alpha": config["alpha"],
        "source_declaration": deepcopy(plan["bindings"]["source_declaration"]),
        "source_verification": "DECLARATIONS_ONLY_NOT_INDEPENDENTLY_VERIFIED",
        "key_semantics": "USER_DECLARED_NOT_INDEPENDENTLY_VERIFIED",
        "fact_status": "UNKNOWN", "factual_truth_verified": False,
        "factual_error_interval": None, "formal_decision": "NEED_MORE_EVIDENCE",
        "unresolved_policy": "KEEP_ALL_FIXED_SAMPLE_POSITIONS_COMPLETE_UNKNOWN_AS_PASS_AND_DISAGREEMENT",
        "sampling_result_independent": config["sampling_method"] != "NON_PROBABILITY",
        "limitations": ["Local hashes cannot prove no earlier seed search or immutable preregistration.",
            "Keys and source independence are declarations, not independent factual verification.",
            "SRS intervals concern fixed candidate records and the stated nonkey comparison protocol.",
            "Repeated plans or optional budget/threshold selection are not covered jointly by one alpha."]}
