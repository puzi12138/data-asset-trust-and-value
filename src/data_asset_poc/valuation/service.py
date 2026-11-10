"""Read-only, exact-identity JSON interfaces over saved Valuation_engine experiments.

These interfaces expose a research scenario. They never execute a purchase or
certify authorization, market price, causal bank profit, or deployed identity.
Ambiguous methods/economics/rounds are rejected instead of silently selecting.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .core import compare_values
from .credit_adapter import parse_time

SERVICE_VERSION = "valuation_engine-readonly-service-v1"
ASSUMPTIONS = [
    "Delivery histories and procurement outcomes are SIMULATED, not real commercial records.",
    "Credit_model and Simulation assets are connected by an explicit simulated mapping, not common real identity.",
    "Economics are hypothetical decision-loss units, not currency, real bank profit or market price.",
    "Successful-delivery task benefit is estimated on validation; held-out test is realization/audit only.",
    "Local demo signature verification and assumed policy do not establish real identity or legal authorization.",
    "Credit limits are uncalibrated sensitivity bounds; fixed-q evaluation bootstrap excludes other uncertainties.",
]


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    return value


def _explicit_bool(value: Any, field: str) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError(f"{field} must be an explicit saved boolean")


def _row_dict(row: pd.Series) -> dict[str, Any]:
    result = _native(row.to_dict())
    for field in ("reasons", "reliability_reasons", "model_reasons"):
        if field in result and isinstance(result[field], str):
            try:
                reasons = json.loads(result[field])
            except json.JSONDecodeError as exc:
                raise ValueError(f"Saved {field} is not valid structured JSON") from exc
            if not isinstance(reasons, list):
                raise ValueError(f"Saved {field} must encode a list")
            result[field] = reasons
    return result


class SavedValuationService:
    """Exact (asset, version, task, decision time) access to immutable CSVs.

    A selector may omit method/economics/round only when the remaining match is
    unique. No latest-version/latest-time or first-row fallback exists.
    """
    def __init__(self, output: Path | str):
        self.output = Path(output).resolve()
        self._tables: dict[str, pd.DataFrame] = {}

    def _table(self, name: str) -> pd.DataFrame:
        if name not in self._tables:
            path = self.output / f"{name}.csv"
            if not path.is_file():
                raise FileNotFoundError(f"Saved Valuation_engine table missing: {path}")
            self._tables[name] = pd.read_csv(path, float_precision="round_trip")
        return self._tables[name]

    @staticmethod
    def _identity(asset_id: str, version: str, task_id: str, as_of_time: str) -> None:
        if any(not isinstance(value, str) or not value for value in (asset_id, version, task_id, as_of_time)):
            raise ValueError("asset_id, version, task_id and timezone-aware as_of_time are required")
        parse_time(as_of_time)

    @staticmethod
    def _unique(frame: pd.DataFrame, label: str) -> pd.Series:
        if len(frame) == 0:
            raise ValueError(f"NO_EXACT_MATCH: {label}")
        if len(frame) != 1:
            raise ValueError(f"AMBIGUOUS: {label}; provide exact method, economics_id and round_id where applicable")
        return frame.iloc[0]

    def _value_row(self, asset_id: str, version: str, task_id: str, as_of_time: str,
                   *, method: str | None = None, economics_id: str | None = None,
                   round_id: str | None = None) -> dict[str, Any]:
        self._identity(asset_id, version, task_id, as_of_time)
        values = self._table("values")
        required = {"asset_id", "version", "task_id", "procurement_time", "method", "economics_id", "round_id"}
        if not required.issubset(values):
            raise ValueError(f"Saved values schema is missing: {sorted(required-set(values))}")
        candidates = values.loc[values.asset_id.eq(asset_id) & values.version.eq(version) & values.task_id.eq(task_id)]
        requested_time = parse_time(as_of_time)
        candidates = candidates.loc[candidates.procurement_time.map(lambda item: parse_time(item) == requested_time)]
        for field, selected in (("method", method), ("economics_id", economics_id), ("round_id", round_id)):
            if selected is not None:
                candidates = candidates.loc[candidates[field].astype(str).eq(str(selected))]
        row = _row_dict(self._unique(candidates, "valuation identity"))
        if row.get("credit_prediction_time") is not None and parse_time(row["credit_prediction_time"]) > requested_time:
            raise ValueError("Saved credit prediction occurs after the procurement decision")
        if row.get("b_success_source", "validation") != "validation":
            raise ValueError("Procurement benefits must originate from validation, never held-out test")
        if "deployment_eligible" in row and _explicit_bool(row["deployment_eligible"], "deployment_eligible"):
            raise ValueError("Research-only source must not claim deployment eligibility")
        return row

    def _provenance(self, task_id: str) -> dict[str, Any]:
        path = (self.output / "tasks" / task_id / "validation.provenance.json").resolve()
        if not path.is_relative_to(self.output / "tasks"):
            raise ValueError("task_id cannot escape the saved task directory")
        if not path.is_file():
            return {"available": False, "reason": "VALIDATION_PROVENANCE_FILE_MISSING"}
        return {"available": True, "relative_path": path.relative_to(self.output).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "details": json.loads(path.read_text(encoding="utf-8"))}

    def evaluate_conditional_utility(self, asset_id: str, version: str, task_id: str, as_of_time: str,
                                     *, method: str | None = None, economics_id: str | None = None,
                                     round_id: str | None = None) -> dict[str, Any]:
        """Return validation metrics, never use test metrics as valuation inputs."""
        self._identity(asset_id, version, task_id, as_of_time)
        metrics = self._table("task_metrics")
        required = {"asset_id", "asset_version", "task_id", "economics_id", "evaluation_split"}
        if not required.issubset(metrics):
            raise ValueError(f"Saved task metrics missing fields: {sorted(required-set(metrics))}")
        selected = metrics.loc[metrics.asset_id.eq(asset_id) & metrics.asset_version.eq(version)
                               & metrics.task_id.eq(task_id) & metrics.evaluation_split.eq("validation")]
        if economics_id is not None:
            selected = selected.loc[selected.economics_id.eq(economics_id)]
        row = _row_dict(self._unique(selected, "validation task utility"))
        # Method and round do not alter shared task utility, but an explicitly
        # supplied selector must be a valid saved procurement context.
        if method is not None or round_id is not None:
            self._value_row(asset_id, version, task_id, as_of_time, method=method,
                            economics_id=row["economics_id"], round_id=round_id)
        return {"interface": "evaluate_conditional_utility", "version": SERVICE_VERSION,
                "asset_id": asset_id, "asset_version": version, "task_id": task_id,
                "as_of_time": as_of_time, "result": row, "provenance": self._provenance(task_id),
                "time_assumption": "Validation utility availability on the simulated procurement timeline is an explicit counterfactual assumption",
                "deployment_eligible": False, "assumptions": ASSUMPTIONS}

    def estimate_delivery_reliability(self, asset_id: str, version: str, task_id: str, as_of_time: str,
                                      *, method: str | None = None, economics_id: str | None = None,
                                      round_id: str | None = None) -> dict[str, Any]:
        row = self._value_row(asset_id, version, task_id, as_of_time, method=method,
                              economics_id=economics_id, round_id=round_id)
        mappings = self._table("mapping")
        if "mapping_id" not in row or "mapping_id" not in mappings:
            raise ValueError("Explicit simulated procurement mapping is required")
        mapping = _row_dict(self._unique(mappings.loc[mappings.mapping_id.eq(row["mapping_id"])], "simulated mapping"))
        if mapping["asset_id"] != asset_id or mapping["version"] != version:
            raise ValueError("Mapping asset/version differs from the requested valuation")
        if parse_time(mapping["procurement_time"]) != parse_time(as_of_time):
            raise ValueError("Mapping procurement time differs from the valuation")
        if parse_time(mapping["credit_prediction_time"]) > parse_time(as_of_time):
            raise ValueError("Mapping references future credit")
        if row.get("asset_content_hash") is not None and mapping.get("content_hash") != row["asset_content_hash"]:
            raise ValueError("Mapping content hash differs from the valuation")
        q = float(row["q"])
        if not np.isfinite(q) or not 0 <= q <= 1:
            raise ValueError("Saved credit probability is invalid")
        fields = ("q", "q_lower", "q_upper", "credit_prediction_time", "credit_model", "credit_profile",
                  "credit_provider_id", "credit_asset_id", "credit_asset_version", "evidence_verified",
                  "policy_verified", "policy_basis", "method", "mapping_id", "round_id")
        return {"interface": "estimate_delivery_reliability", "version": SERVICE_VERSION,
                "asset_id": asset_id, "asset_version": version, "task_id": task_id, "as_of_time": as_of_time,
                "result": {key: row.get(key) for key in fields}, "mapping": mapping,
                "statistical_estimate_is_not_trade_authorization": True,
                "credit_interval_scope": "UNCALIBRATED_MODEL_CONDITIONAL_SENSITIVITY_ONLY",
                "deployment_eligible": False, "assumptions": ASSUMPTIONS}

    def estimate_risk_adjusted_value(self, asset_id: str, version: str, task_id: str, as_of_time: str,
                                     *, method: str | None = None, economics_id: str | None = None,
                                     round_id: str | None = None) -> dict[str, Any]:
        row = self._value_row(asset_id, version, task_id, as_of_time, method=method,
                              economics_id=economics_id, round_id=round_id)
        return {"interface": "estimate_risk_adjusted_value", "version": SERVICE_VERSION,
                "asset_id": asset_id, "asset_version": version, "task_id": task_id, "as_of_time": as_of_time,
                "result": row, "provenance": self._provenance(task_id),
                "formula": "W=q*B_success+(1-q)*B_failure-C_fixed; before licence price",
                "deployment_eligible": False, "assumptions": ASSUMPTIONS}

    def compare_asset_values(self, asset_versions: Sequence[tuple[str, str]], task_id: str, as_of_time: str,
                             *, method: str | None = None, economics_id: str | None = None,
                             round_id: str | None = None, policy: str | None = None) -> dict[str, Any]:
        if not asset_versions or any(not isinstance(item, (tuple, list)) or len(item) != 2 for item in asset_versions):
            raise ValueError("Each candidate must explicitly provide (asset_id, version)")
        rows = [self._value_row(asset_id, version, task_id, as_of_time, method=method,
                               economics_id=economics_id, round_id=round_id)
                for asset_id, version in asset_versions]
        contexts = {(row["method"], row["economics_id"], str(row["round_id"])) for row in rows}
        if len(contexts) != 1:
            raise ValueError("Candidates must share one exact method, economics and procurement round")
        resolved_method, resolved_economics, resolved_round = next(iter(contexts))
        selected_policy = policy or ("conservative" if resolved_method == "conservative_hier30" else "point")
        comparison = compare_values([{
            "asset_id": row["asset_id"], "version": row["version"], "value": row["value"],
            "benefit_success": row["b_success"],
            "evidence_verified": _explicit_bool(row["evidence_verified"], "evidence_verified"),
            "policy_verified": _explicit_bool(row["policy_verified"], "policy_verified"),
            "model_status": row["model_status"], "conditional_lower": row.get("conditional_low"),
            "conditional_upper": row.get("conditional_high"), "model_reasons": row.get("reasons", []),
        } for row in rows], selected_policy)
        return {"interface": "compare_asset_values", "version": SERVICE_VERSION,
                "task_id": task_id, "as_of_time": as_of_time, "method": resolved_method,
                "economics_id": resolved_economics, "round_id": resolved_round,
                "comparison_under_simulated_assumptions": comparison,
                "formal_procurement_recommendation": False, "deployment_eligible": False,
                "assumptions": ASSUMPTIONS}

    def generate_valuation_report(self, asset_id: str, version: str, task_id: str, as_of_time: str,
                                  *, method: str | None = None, economics_id: str | None = None,
                                  round_id: str | None = None) -> dict[str, Any]:
        row = self._value_row(asset_id, version, task_id, as_of_time, method=method,
                              economics_id=economics_id, round_id=round_id)
        selectors = {"method": row["method"], "economics_id": row["economics_id"], "round_id": str(row["round_id"])}
        result = {"interface": "generate_valuation_report", "version": SERVICE_VERSION,
                  "identity": {"asset_id": asset_id, "asset_version": version, "task_id": task_id, "as_of_time": as_of_time},
                  "conditional_utility": self.evaluate_conditional_utility(asset_id, version, task_id, as_of_time, **selectors),
                  "delivery_reliability": self.estimate_delivery_reliability(asset_id, version, task_id, as_of_time, **selectors),
                  "conditional_value": self.estimate_risk_adjusted_value(asset_id, version, task_id, as_of_time, **selectors),
                  "deployment_eligible": False, "formal_procurement_recommendation": False,
                  "assumptions": ASSUMPTIONS}
        # Verify the public result really is strict, interoperable JSON.
        return json.loads(json.dumps(_native(result), ensure_ascii=False, allow_nan=False))


def evaluate_conditional_utility(output: Path | str, *args, **kwargs) -> dict[str, Any]:
    return SavedValuationService(output).evaluate_conditional_utility(*args, **kwargs)


def estimate_delivery_reliability(output: Path | str, *args, **kwargs) -> dict[str, Any]:
    return SavedValuationService(output).estimate_delivery_reliability(*args, **kwargs)


def estimate_risk_adjusted_value(output: Path | str, *args, **kwargs) -> dict[str, Any]:
    return SavedValuationService(output).estimate_risk_adjusted_value(*args, **kwargs)


def compare_asset_values(output: Path | str, *args, **kwargs) -> dict[str, Any]:
    return SavedValuationService(output).compare_asset_values(*args, **kwargs)


def generate_valuation_report(output: Path | str, *args, **kwargs) -> dict[str, Any]:
    return SavedValuationService(output).generate_valuation_report(*args, **kwargs)
