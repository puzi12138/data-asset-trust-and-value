"""Explicit transfer of SIMULATED credit profiles, never a real asset join."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data_asset_poc.credit.evidence import DemoIdentities, EvidenceLedger, read_jsonl


def parse_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Credit assessment time must include timezone")
    return moment


class SavedCreditProfiles:
    """Load already frozen predictions; hide simulator outcome/p from normal API.

    This is a synthetic association between different experiments, not evidence
    that a Credit_model asset truly has the historical contracts of a Simulation asset.
    The full signed log is checked once, and per-call chronology is enforced.
    """
    def __init__(self, root: Path):
        self.root = Path(root)
        self.source = self.root / "experiments/runs/simulation"
        raw = pd.read_csv(self.source / "predictions.csv", float_precision="round_trip")
        columns = ["seed", "scenario", "day", "model", "contract_id", "provider_id", "asset_id", "as_of_time",
                   "probability", "lower", "upper", "n_history", "provider_history_count", "evidence_status",
                   "history_max_observed_at", "history_max_event_time", "algorithm_version"]
        self.predictions = raw[columns].set_index(["seed", "scenario", "day", "model"], verify_integrity=True)
        self._ledgers: dict[tuple[int, str], EvidenceLedger] = {}
        self._commitments: dict[tuple[int, str], dict] = {}
        self._cache: dict[tuple, dict] = {}

    def _ledger(self, seed: int, profile: str) -> EvidenceLedger:
        key = seed, profile
        if key not in self._ledgers:
            case = self.source / f"seed_{seed}" / profile
            identities = DemoIdentities.from_public_registry(json.loads((case / "public_registry.json").read_text()))
            commitments = read_jsonl(case / "commitments.jsonl")
            ledger = EvidenceLedger(commitments, read_jsonl(case / "events.jsonl"), identities,
                                    ["SIM_independent_observer"], json.loads((case / "anchor.json").read_text()))
            if not ledger.chain_status["valid"] or not all(v["valid"] for v in ledger.validations + ledger.commitment_validations):
                raise ValueError(f"Invalid saved evidence for credit profile {key}")
            self._ledgers[key] = ledger
            self._commitments[key] = {c["contract_id"]: c for c in commitments}
        return self._ledgers[key]

    def estimate(self, seed: int, profile: str, day: int, model: str, as_of_time: str | None = None) -> dict:
        cache_key = seed, profile, day, model, as_of_time
        if cache_key in self._cache:
            return dict(self._cache[cache_key])
        row = self.predictions.loc[(seed, profile, day, model)].to_dict()
        as_of = row["as_of_time"] if as_of_time is None else as_of_time
        now = parse_time(as_of)
        if parse_time(row["as_of_time"]) > now:
            raise ValueError("Credit prediction is from the future of the procurement decision")
        for key in ("history_max_observed_at", "history_max_event_time"):
            if pd.notna(row[key]) and parse_time(row[key]) > parse_time(row["as_of_time"]):
                raise ValueError("Credit history contains future evidence")
        ledger = self._ledger(seed, profile)
        commitment = self._commitments[(seed, profile)][row["contract_id"]]
        snapshot = ledger.query(row["as_of_time"])
        target = [r for r in snapshot["outcomes"] if r["asset_id"] == row["asset_id"] and r["provider_id"] == row["provider_id"]]
        if len(target) != int(row["n_history"]) or any(r["contract_id"] == row["contract_id"] for r in snapshot["outcomes"]):
            raise ValueError("Saved prediction history/contract isolation does not match signed evidence")
        if not 0 <= row["probability"] <= 1:
            raise ValueError("Invalid credit probability")
        result = {"q": float(row["probability"]), "q_lower": float(row["lower"]), "q_upper": float(row["upper"]),
                "credit_seed": seed, "credit_profile": profile, "credit_model": model,
                "credit_prediction_time": row["as_of_time"], "procurement_time": as_of,
                "credit_contract_id": row["contract_id"], "credit_asset_id": row["asset_id"],
                "credit_asset_version": commitment["asset_version"],
                "credit_content_hash": commitment["expected_content_hash"],
                "credit_provider_id": row["provider_id"], "credit_n_history": int(row["n_history"]),
                "credit_algorithm_version": row["algorithm_version"],
                "credit_evidence_status": "VERIFIED_LOCAL_DEMO_ED25519_NOT_REAL_IDENTITY",
                "profile_transfer_status": "SIMULATED_MAPPING_BETWEEN_DISTINCT_ASSETS",
                "credit_interval_scope": "UNCALIBRATED_MODEL_CONDITIONAL_SENSITIVITY_ONLY"}
        self._cache[cache_key] = result
        return dict(result)


def demo_commitment_evidence(mapping: dict, kind: str = "valid_demo") -> dict:
    """Actual signature check for the *new demo commitment*, no delivery yet.

    Policy verification here is an explicit simulation assumption. No real data
    rights, independent observer governance, or legal appraisal is established.
    """
    provider = mapping["credit_provider_id"]
    keys = DemoIdentities(seed=mapping["credit_seed"], provider_ids=[provider])
    created = mapping["credit_prediction_time"]
    credit_prediction_time = created
    if kind == "future_credit_time":
        credit_prediction_time = (parse_time(mapping["procurement_time"]) + timedelta(days=1)).isoformat()
    unsigned = {"contract_id": mapping["mapping_id"], "provider_id": provider,
                "asset_id": mapping["asset_id"], "asset_version": mapping["version"],
                "expected_content_hash": mapping["content_hash"].removeprefix("sha256:"),
                "expected_schema_hash": mapping["schema_hash"], "created_at": created,
                "agreed_deadline": (parse_time(created) + timedelta(days=1)).isoformat(),
                "usage_policy_id": "SIMULATED_RESEARCH_POLICY_NOT_REAL_AUTHORIZATION"}
    signed = keys.sign_commitment(unsigned)
    registry = keys.public_registry()
    if kind == "invalid_signature":
        signed["expected_content_hash"] = "f" * 64
    if kind == "unknown_identity":
        registry["identities"] = {}
    public = DemoIdentities.from_public_registry(registry)
    verifier = EvidenceLedger([signed], [], public, [], None)
    verified = bool(verifier.commitment_validations[0]["valid"])
    time_valid = parse_time(credit_prediction_time) <= parse_time(mapping["procurement_time"])
    return {"kind": kind, "commitment": signed, "public_registry": registry,
            "signature_valid": verified, "time_valid": time_valid,
            "tested_credit_prediction_time": credit_prediction_time,
            "tested_procurement_time": mapping["procurement_time"],
            "evidence_verified": bool(verified and time_valid),
            "policy_verified": kind != "policy_unknown",
            "policy_basis": "SIMULATION_ASSUMPTION_ONLY_NOT_REAL_AUTHORIZATION",
            "deployment_eligible": False,
            "validation": verifier.commitment_validations[0]}
