"""Small, read-only adapters for frozen task benefit and live local credit.

The utility store contains only validation measurements.  Local delivery credit
is supplied by the coordinator; saved simulated credit is a separate interface.
All arithmetic and eligibility calculations reuse the frozen Valuation_engine core.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd

from ..valuation.core import assess_status, compare_values, conditional_value_interval, risk_adjusted_value
from ..valuation.credit_adapter import parse_time
from ..valuation.service import SavedValuationService


ADAPTER_VERSION = "delivery_core-exact-utility-local-credit-v1"
INDEX_VERSION = "delivery_core-utility-index-v1"
LEGACY_COVERAGE = {"case": "A_stable", "utility_seed": 101, "credit_seed": 17,
                   "rotation": 0, "day": 180}
LOCAL_ASSUMPTIONS = [
    "Task benefits reuse frozen REAL_PUBLIC_UCI validation predictions; no online fitting is performed.",
    "Local HTTP delivery observations and injected faults are not commercial default-frequency samples.",
    "Economic parameters are hypothetical decision-loss units, never RMB or market prices.",
    "A positive research value is conditional on the exact data bytes and the buyer task remaining applicable.",
    "Demo signatures and local policy checks do not establish real ownership or legal authorization.",
    "Fixed-q bootstrap excludes credit uncertainty, economic uncertainty and distribution drift.",
]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _decoded_row(row: Mapping[str, str]) -> dict[str, Any]:
    """Decode CSV cells without rounding binary floats or guessing identities."""
    result: dict[str, Any] = {}
    identity = {"asset_id", "asset_version", "version", "task_id", "economics_id", "round_id",
                "mapping_id", "model_key", "base_model_key"}
    for key, value in row.items():
        if key in identity:
            result[key] = value
        elif value in {"True", "False"}:
            result[key] = value == "True"
        elif value == "":
            result[key] = None
        elif value.startswith("["):
            result[key] = json.loads(value)
        else:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def build_utility_index(source: Path, output: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    """Derive a new small SQLite store without changing any research artifact.

    All validation utility rows are retained.  The legacy demonstration window
    is fixed before reading values and is not selected by observed performance.
    Creation is exclusive: an existing output is never overwritten.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Index already exists; choose a new derived path: {output}")
    config_path = source / "config.json"
    metric_path = source / "task_metrics.csv"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    with metric_path.open(encoding="utf-8", newline="") as handle:
        metrics = [_decoded_row(row) for row in csv.DictReader(handle)]
    metrics = [row for row in metrics if row["evaluation_split"] == "validation"]
    if not metrics:
        raise ValueError("No validation measurements; refusing an empty utility index")
    variants = config["variants"]
    inputs: dict[str, dict[str, Any]] = {}
    def provenance(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"Frozen utility evidence missing: {path}")
        key = path.relative_to(source).as_posix()
        if key not in inputs:
            inputs[key] = {"relative_path": key, "size_bytes": path.stat().st_size, "sha256": _hash(path)}
        return inputs[key]
    provenance(config_path)
    provenance(metric_path)
    entries = []
    for row in metrics:
        task, economic, asset = row["task_id"], row["economics_id"], row["asset_id"]
        task_dir = (source / "tasks" / task).resolve()
        if not task_dir.is_relative_to(source / "tasks"):
            raise ValueError("Unsafe frozen task path")
        draw_path = task_dir / f"validation.{economic}.benefit_draws.npy"
        proof_path = task_dir / "validation.provenance.json"
        draw_proof, proof = provenance(draw_path), provenance(proof_path)
        array = np.load(draw_path, allow_pickle=False)
        if array.ndim != 2 or array.shape[1] != len(variants) or array.shape[0] < 20 or not np.isfinite(array).all():
            raise ValueError("Frozen bootstrap array has invalid shape or values")
        draws = array[:, variants.index(asset)].tolist()
        source_provenance = json.loads(proof_path.read_text(encoding="utf-8"))
        if source_provenance.get("task_output_use") != "PROCUREMENT_ESTIMATION":
            raise ValueError("Utility must have procurement-estimation provenance, never heldout test")
        if row.get("diagnostic_use") != "PREDECISION_VALIDATION_SCREEN":
            raise ValueError("Reliability gate must use validation only")
        available = datetime.fromtimestamp(max(metric_path.stat().st_mtime, proof_path.stat().st_mtime,
                                              draw_path.stat().st_mtime), timezone.utc).isoformat()
        entries.append((asset, row["asset_version"], row["asset_content_hash"], task, economic,
                        _json(row), _json(draws), _json({"available": True, "details": source_provenance,
                        "sha256": proof["sha256"], "relative_path": proof["relative_path"],
                        "bootstrap_source": draw_proof, "availability_basis": "LOCAL_FILE_MTIME_NOT_TRUSTED_TIMESTAMP"}), available))

    # Only 90 rows from a predeclared demonstration context are indexed.  CSV
    # streaming is a build-time operation; the service never reads values.csv.
    legacy = []
    values_path = source / "values.csv"
    with values_path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if all(str(raw[key]) == str(value) for key, value in LEGACY_COVERAGE.items()):
                legacy.append(_decoded_row(raw))
    if len(legacy) != 90:
        raise ValueError(f"Expected complete 90-row declared legacy window, got {len(legacy)}")
    provenance(values_path)
    map_ids = {row["mapping_id"] for row in legacy}
    with (source / "mapping.csv").open(encoding="utf-8", newline="") as handle:
        mappings = [_decoded_row(row) for row in csv.DictReader(handle) if row["mapping_id"] in map_ids]
    if len(mappings) != 3:
        raise ValueError("Legacy window must retain all three exact mappings")
    provenance(source / "mapping.csv")
    summary: dict[str, Any] = {"version": INDEX_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run": str(source), "source_kind": "FROZEN_REAL_PUBLIC_DATA_UTILITY_AND_SEPARATE_SIMULATED_LEGACY_WINDOW",
        "validation_utility_rows": len(entries), "heldout_rows_in_index": 0, "legacy_rows": len(legacy),
        "legacy_coverage": LEGACY_COVERAGE, "runtime_reads_original_values_csv": False,
        "no_models_retrained": True, "deployment_eligible": False, "inputs": inputs,
        "economics": config["economics"], "recommended_demo_assets": []}
    if project_root is not None:
        root = Path(project_root).resolve()
        for name in ("clean", "label_020"):
            row = next(item for item in metrics if item["asset_id"] == name and
                       item["task_id"] == "random_seed101_d01500_logistic" and item["economics_id"] == "fp1_fn5")
            meta_path = root / "experiments/runs/credit_model/random/assets/seed_101" / f"{name}.metadata.json"
            data_path = meta_path.with_name(f"{name}.csv")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["content_hash"] != row["asset_content_hash"] or "sha256:" + _hash(data_path) != row["asset_content_hash"]:
                raise ValueError("Recommended demo bytes do not match frozen task identity")
            summary["recommended_demo_assets"].append({"asset_id": name, "version": row["asset_version"],
                "content_hash": row["asset_content_hash"], "task_id": row["task_id"], "economics_id": row["economics_id"],
                "csv_path": str(data_path), "metadata_path": str(meta_path), "schema": meta["schema"],
                "row_count": meta["row_count"], "license": meta.get("license"), "source": meta.get("source"),
                "frozen_metadata": meta})
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive file creation closes the exists/create race; failures leave an
    # explicitly incomplete new artifact rather than replace any previous store.
    with output.open("xb"):
        pass
    with sqlite3.connect(output) as connection:
        connection.executescript("""
            CREATE TABLE utility(asset_id TEXT, version TEXT, content_hash TEXT, task_id TEXT,
                economics_id TEXT, row_json TEXT NOT NULL, draws_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL, available_at TEXT NOT NULL,
                PRIMARY KEY(asset_id,version,content_hash,task_id,economics_id));
            CREATE TABLE legacy_values(row_json TEXT NOT NULL);
            CREATE TABLE legacy_mapping(row_json TEXT NOT NULL);
            CREATE TABLE metadata(key TEXT PRIMARY KEY, json TEXT NOT NULL);
        """)
        connection.executemany("INSERT INTO utility VALUES (?,?,?,?,?,?,?,?,?)", entries)
        connection.executemany("INSERT INTO legacy_values VALUES (?)", [(_json(row),) for row in legacy])
        connection.executemany("INSERT INTO legacy_mapping VALUES (?)", [(_json(row),) for row in mappings])
        connection.execute("INSERT INTO metadata VALUES (?,?)", ("summary", _json(summary)))
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Derived SQLite integrity check failed")
    summary["index_path"] = str(output)
    summary["index_sha256"] = _hash(output)
    summary["index_size_bytes"] = output.stat().st_size
    return summary


class UtilityIndex:
    """A connection per query; no global mutable tables and no unbounded cache."""
    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Build the frozen utility index first: {self.path}")
        with self.connection() as connection:
            row = connection.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()
            self.summary = json.loads(row[0])
        if self.summary["version"] != INDEX_VERSION:
            raise ValueError("Unsupported frozen utility index version")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            yield connection
        finally:
            connection.close()

    def exact(self, asset: Mapping[str, Any], task_id: str, economics_id: str) -> dict[str, Any] | None:
        if any(not isinstance(value, str) or not value for value in (task_id, economics_id)):
            raise ValueError("task_id and economics_id must be nonempty strings")
        identity = tuple(str(asset.get(key, "")) for key in ("asset_id", "version", "content_hash"))
        if not all(identity) or any(asset.get(key) is None for key in ("asset_id", "version", "content_hash")):
            raise ValueError("asset_id, exact version and content_hash are required")
        with self.connection() as connection:
            row = connection.execute("SELECT row_json,draws_json,provenance_json,available_at FROM utility "
                "WHERE asset_id=? AND version=? AND content_hash=? AND task_id=? AND economics_id=?",
                (*identity, task_id, economics_id)).fetchone()
        return None if row is None else {"metrics": json.loads(row[0]), "draws": json.loads(row[1]),
                                        "provenance": json.loads(row[2]), "available_at": row[3]}


class LocalValuationService:
    """Five research interfaces using exact frozen utility and current local q.

    Root service owns identity, signed evidence and authorization checks.  This
    adapter requires their explicit booleans and never turns provider claims or
    missing evidence into positive authorization.  It does not write registry DB.
    """
    def __init__(self, index: Path | str | UtilityIndex):
        self.index = index if isinstance(index, UtilityIndex) else UtilityIndex(index)

    @staticmethod
    def _base(asset: Mapping[str, Any], task_id: str, as_of_time: str, interface: str) -> dict[str, Any]:
        if not isinstance(asset, Mapping) or any(not isinstance(asset.get(key), str) or not asset.get(key)
                for key in ("asset_id", "version", "content_hash")):
            raise ValueError("asset_id, version and content_hash must be explicit nonempty strings")
        if not isinstance(task_id, str) or not task_id or not isinstance(as_of_time, str) or not as_of_time:
            raise ValueError("task_id and timezone-aware as_of_time must be nonempty strings")
        parse_time(as_of_time)
        return {"interface": interface, "version": ADAPTER_VERSION, "asset_id": asset["asset_id"],
                "asset_version": asset["version"], "content_hash": asset["content_hash"], "task_id": task_id,
                "as_of_time": as_of_time, "deployment_eligible": False, "formal_procurement_recommendation": False,
                "legal_or_professional_appraisal": False, "assumptions": LOCAL_ASSUMPTIONS}

    def evaluate_conditional_utility(self, asset: Mapping[str, Any], task_id: str, as_of_time: str,
                                     *, economics_id: str = "fp1_fn5") -> dict[str, Any]:
        base = self._base(asset, task_id, as_of_time, "evaluate_conditional_utility")
        item = self.index.exact(asset, task_id, economics_id)
        if item is None:
            return {**base, "status": "NOT_EVALUATED", "reason": "NO_EXACT_FROZEN_TASK_ASSET_VERSION_CONTENT_HASH_ECONOMICS_MATCH",
                    "result": None, "economics_id": economics_id}
        if parse_time(item["available_at"]) > parse_time(as_of_time):
            return {**base, "status": "NOT_EVALUATED", "reason": "TASK_MEASUREMENT_NOT_YET_LOCALLY_AVAILABLE",
                    "result": None, "available_at": item["available_at"], "economics_id": economics_id}
        return {**base, "status": "EVALUATED", "result": item["metrics"], "economics_id": economics_id,
                "provenance": item["provenance"], "available_at": item["available_at"],
                "evidence_type": "REAL_PUBLIC_DATA_FROZEN_VALIDATION_TASK_UTILITY",
                "benefit_source": "validation", "heldout_test_used_for_online_value": False}

    def estimate_delivery_reliability(self, asset: Mapping[str, Any], task_id: str, as_of_time: str,
                                      *, credit: Mapping[str, Any]) -> dict[str, Any]:
        base = self._base(asset, task_id, as_of_time, "estimate_delivery_reliability")
        required = {"q", "q_lower", "q_upper", "n_history", "as_of_time", "source"}
        if not required.issubset(credit):
            raise ValueError(f"Local credit fields missing: {sorted(required-set(credit))}")
        if parse_time(credit["as_of_time"]) > parse_time(as_of_time):
            raise ValueError("Future credit must not enter historical valuation")
        for field in ("asset_id", "version", "provider_id"):
            if field in credit and field in asset and credit[field] != asset[field]:
                raise ValueError(f"Local credit {field} differs from the registered asset")
        if any(isinstance(credit[field], bool) or not isinstance(credit[field], (int, float))
               for field in ("q", "q_lower", "q_upper")):
            raise ValueError("Local credit probabilities must be explicit numeric scalars")
        q, lo, hi = (float(credit[field]) for field in ("q", "q_lower", "q_upper"))
        if not np.isfinite([q, lo, hi]).all() or not 0 <= lo <= q <= hi <= 1:
            raise ValueError("Local q and sensitivity endpoints must be finite ordered probabilities")
        history = credit["n_history"]
        if isinstance(history, bool) or not isinstance(history, int) or history < 0:
            raise ValueError("n_history must be a nonnegative integer")
        source = str(credit["source"])
        if "SIMULAT" in source.upper() or "LEGACY" in source.upper():
            raise ValueError("Legacy simulated q must use the separate legacy interface")
        return {**base, "status": "LOCAL_RESEARCH_CREDIT", "result": dict(credit),
                "insufficient_history": history < 5, "minimum_history_warning_threshold": 5,
                "interval_scope": "UNCALIBRATED_LOCAL_MODEL_SENSITIVITY_NOT_REAL_DEFAULT_RISK",
                "statistical_estimate_is_not_trade_authorization": True}

    def estimate_risk_adjusted_value(self, asset: Mapping[str, Any], task_id: str, as_of_time: str,
                                     *, credit: Mapping[str, Any], economics_id: str = "fp1_fn5",
                                     evidence_verified: bool = False, policy_verified: bool = False,
                                     economic_overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
        base = self._base(asset, task_id, as_of_time, "estimate_risk_adjusted_value")
        if not isinstance(evidence_verified, bool) or not isinstance(policy_verified, bool):
            raise ValueError("Evidence and policy verification must be explicit booleans")
        utility = self.evaluate_conditional_utility(asset, task_id, as_of_time, economics_id=economics_id)
        reliability = self.estimate_delivery_reliability(asset, task_id, as_of_time, credit=credit)
        if utility["status"] != "EVALUATED":
            return {**base, "status": "NOT_EVALUATED", "reason": utility["reason"], "result": None,
                    "conditional_utility": utility, "delivery_reliability": reliability,
                    "evidence_verified": evidence_verified, "policy_verified": policy_verified,
                    "research_recommendation_eligible": False}
        item = self.index.exact(asset, task_id, economics_id)
        metric = utility["result"]
        economics = next(e for e in self.index.summary["economics"] if e["id"] == economics_id).copy()
        if economic_overrides:
            if not set(economic_overrides).issubset({"failure_benefit", "fixed_cost"}):
                raise ValueError("Only explicit failure_benefit and fixed_cost overrides are supported; new thresholds need new measured utility")
            economics.update(economic_overrides)
        q, bs, bf, cost = float(credit["q"]), float(metric["b_success"]), float(economics["failure_benefit"]), float(economics["fixed_cost"])
        value = risk_adjusted_value(q, bs, bf, cost)
        interval = conditional_value_interval(q, item["draws"], bf, cost)
        reasons = list(metric.get("reliability_reasons", []))
        uncertain = interval["crosses_zero"] or credit["q_upper"]-credit["q_lower"] > .5 or reliability["insufficient_history"]
        if interval["crosses_zero"]:
            reasons.append("FIXED_Q_BOOTSTRAP_VALUE_CROSSES_ZERO")
        if credit["q_upper"]-credit["q_lower"] > .5:
            reasons.append("WIDE_UNCALIBRATED_CREDIT_INTERVAL")
        if reliability["insufficient_history"]:
            reasons.append("LOCAL_DELIVERY_HISTORY_INSUFFICIENT")
        model_status = "VALUATION_UNRELIABLE" if not metric["reliability_screen_pass"] else "VALUATION_UNCERTAIN" if uncertain else "VALUATION_VALID"
        state = assess_status(value=value, benefit_success=bs, evidence_verified=evidence_verified,
            policy_verified=policy_verified, model_status=model_status, conditional_lower=interval["lower"],
            conditional_upper=interval["upper"], model_reasons=reasons)
        endpoints = [risk_adjusted_value(float(credit[key]), bs, bf, cost) for key in ("q_lower", "q_upper")]
        result = {"asset_id": asset["asset_id"], "version": asset["version"], "asset_content_hash": asset["content_hash"],
            "task_id": task_id, "economics_id": economics_id, "q": q, "q_source": credit["source"],
            "credit_origin": credit.get("origin", "EXPLICIT_ADAPTER_INPUT_ORIGIN_NOT_SUPPLIED"),
            "fault_injection_not_natural_failure_rate": credit.get("origin") == "fault_injection",
            "q_as_of_time": credit["as_of_time"], "q_lower": credit["q_lower"], "q_upper": credit["q_upper"],
            "credit_n_history": credit["n_history"], "b_success": bs, "b_success_source": "validation",
            "b_failure": bf, "fixed_cost": cost, "value": value, "conditional_low": interval["lower"],
            "conditional_high": interval["upper"], "conditional_interval": interval,
            "credit_sensitivity_low": min(endpoints), "credit_sensitivity_high": max(endpoints),
            "model_status": model_status, "state": state, "reasons": state["reasons"],
            "evidence_verified": evidence_verified, "policy_verified": policy_verified,
            "economics": economics, "economic_unit": "HYPOTHETICAL_LOSS_UNIT_NOT_CURRENCY",
            "economic_parameters_source": "EXPLICIT_REQUEST_OVERRIDE" if economic_overrides else "FROZEN_VALUATION_ENGINE_HYPOTHETICAL_CONFIG",
            "quality_multiplier_applied": False, "deployment_eligible": False}
        return {**base, "status": "EVALUATED", "result": result, "conditional_utility": utility,
                "delivery_reliability": reliability, "formula": "W=q*B_success+(1-q)*B_failure-C_fixed; before licence price",
                "recommendation_scope": "RESEARCH_ONLY_NOT_LEGAL_OR_PROFESSIONAL_APPRAISAL"}

    def generate_valuation_report(self, asset: Mapping[str, Any], task_id: str, as_of_time: str, **kwargs: Any) -> dict[str, Any]:
        result = self.estimate_risk_adjusted_value(asset, task_id, as_of_time, **kwargs)
        return json.loads(_json({**result, "interface": "generate_valuation_report"}))

    def compare_asset_values(self, reports: Sequence[Mapping[str, Any]], *, policy: str = "point") -> dict[str, Any]:
        if not reports:
            raise ValueError("At least one valuation report is required")
        contexts = {(item["task_id"], item["as_of_time"]) for item in reports}
        if len(contexts) != 1:
            raise ValueError("Compared reports must share task and evaluation time")
        evaluated, unmeasured = [], []
        economic_contexts = set()
        credit_origins = set()
        for report in reports:
            row = report.get("result")
            if report.get("status") != "EVALUATED" or row is None:
                unmeasured.append({"asset_id": report["asset_id"], "version": report["asset_version"],
                                   "status": "NOT_EVALUATED", "reason": report.get("reason")})
                continue
            economic_contexts.add(_json(row["economics"]))
            credit_origins.add(row["credit_origin"])
            evaluated.append({"asset_id": row["asset_id"], "version": row["version"], "value": row["value"],
                "benefit_success": row["b_success"], "evidence_verified": row["evidence_verified"],
                "policy_verified": row["policy_verified"], "model_status": row["model_status"],
                "conditional_lower": row["conditional_low"], "conditional_upper": row["conditional_high"],
                "model_reasons": row["reasons"]})
        if len(economic_contexts) > 1:
            raise ValueError("Compared reports must share exact hypothetical economic parameters")
        if len(credit_origins) > 1:
            raise ValueError("Compared reports must share one credit origin; operational and fault evidence cannot be pooled")
        comparison = compare_values(evaluated, policy=policy)
        original_decision = comparison["decision"]
        if original_decision == "FORMAL_RECOMMENDATION":
            comparison["decision"] = "CONSERVATIVE_RESEARCH_RECOMMENDATION"
        if not evaluated:
            comparison["decision"] = "NO_ELIGIBLE_RECOMMENDATION"
        # Frozen mathematical eligibility is retained with an explicit name; it
        # is never presented as a legally formal appraisal recommendation.
        comparison["frozen_algorithm_decision"] = original_decision
        return {"interface": "compare_asset_values", "version": ADAPTER_VERSION, "comparison": comparison,
            "not_evaluated_candidates": unmeasured, "deployment_eligible": False,
            "formal_procurement_recommendation": False, "legal_or_professional_appraisal": False,
            "recommendation_scope": "RESEARCH_ONLY_NOT_LEGAL_OR_PROFESSIONAL_APPRAISAL", "assumptions": LOCAL_ASSUMPTIONS}


class IndexedLegacyValuationService(SavedValuationService):
    """Execute all five *original* interfaces on the explicit 90-row snapshot.

    `_table` and `_provenance` alone are adapted.  The frozen parent performs
    exact selectors, time/hash mapping checks, comparison and strict JSON logic.
    Requests outside the advertised saved window fail with NO_EXACT_MATCH.
    """
    def __init__(self, index: Path | str | UtilityIndex):
        self.index = index if isinstance(index, UtilityIndex) else UtilityIndex(index)
        self.output = self.index.path.parent
        self._tables: dict[str, pd.DataFrame] = {}

    def _table(self, name: str) -> pd.DataFrame:
        if name not in self._tables:
            with self.index.connection() as connection:
                if name == "task_metrics":
                    rows = connection.execute("SELECT row_json FROM utility").fetchall()
                elif name in {"values", "mapping"}:
                    table = {"values": "legacy_values", "mapping": "legacy_mapping"}[name]
                    rows = connection.execute(f"SELECT row_json FROM {table}").fetchall()
                else:
                    raise ValueError(f"Unsupported indexed table: {name}")
            self._tables[name] = pd.DataFrame([json.loads(row[0]) for row in rows])
        return self._tables[name]

    def _provenance(self, task_id: str) -> dict[str, Any]:
        with self.index.connection() as connection:
            row = connection.execute("SELECT provenance_json FROM utility WHERE task_id=? LIMIT 1", (task_id,)).fetchone()
        return {"available": False, "reason": "TASK_NOT_INDEXED"} if row is None else json.loads(row[0])

    def coverage(self) -> dict[str, Any]:
        return {"scope": "HISTORICAL_SIMULATED_PROCUREMENT_NOT_LOCAL_HTTP_EVENTS", "coverage": LEGACY_COVERAGE,
                "rows": self.index.summary["legacy_rows"], "outside_coverage": "NO_EXACT_MATCH",
                "deployment_eligible": False}
