"""Adapt independently verified local deliveries to frozen Simulation models.

This module has no database, network, signing key, or data-quality dependency.
``verified`` and the other trust flags are facts computed by the registry after
checking the provider commitment and independent verifier signature. They must
never be copied from an untrusted provider request. The adapter cannot verify a
signature from those flags alone.

The new HTTP envelope differs from Simulation's chain envelope. Contract reduction
below preserves its counting semantics (one contract, as-of availability,
dispute suspension, sticky deadline failure), while every probability and
interval is calculated by the unchanged ``credit.models.predict_credit``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from data_asset_poc.credit.models import predict_credit


ADAPTER_VERSION = "delivery_core-local-credit-adapter-1.0.0"
ORIGINS = frozenset({"local_operational", "fault_injection", "simulated"})
SOURCE_LABELS = {
    "local_operational": "LOCAL_HTTP_VERIFIED_TRIAL_DELIVERIES_NOT_COMMERCIAL_REPUTATION",
    "fault_injection": "EXECUTED_FAULT_INJECTION_NOT_NATURAL_PROVIDER_FAILURE_RATE",
    "simulated": "LEGACY_SIMULATED_CONTRACTS_NOT_LOCAL_DELIVERIES",
}
_METHODS = {
    "beta": {"method": "beta"},
    "decay30": {"method": "decay", "half_life_days": 30},
    "decay90": {"method": "decay", "half_life_days": 90},
    "hierarchy": {"method": "hierarchy"},
    "decay_hier30": {"method": "decay_hierarchy", "half_life_days": 30},
    "decay_hier90": {"method": "decay_hierarchy", "half_life_days": 90},
}
_STATES = frozenset({"SUCCESS", "FAILURE", "PENDING", "UNKNOWN", "DISPUTED",
                     "ACCESS_DENIED", "CHECK"})
_TIMEOUT_REASONS = frozenset({"TIMEOUT", "DELIVERY_TIMEOUT", "DEADLINE_EXCEEDED",
                             "SLA_FAILURE", "LATE_DELIVERY"})


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a timezone-aware ISO 8601 string")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return moment.astimezone(timezone.utc)


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _asset_version(record: Mapping[str, Any]) -> str | None:
    value = record.get("asset_version", record.get("version"))
    return value if isinstance(value, str) and value else None


def _history(
    records: Sequence[Mapping[str, Any]], asset_id: str, provider_id: str,
    now: datetime, origin: str, version: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reduce authenticated records; malformed/untrusted rows are quarantined.

    Records are immutable snapshots, not a mutable current-state-only table.
    Retaining their event and observation times is necessary for past queries.
    A signed DISPUTED snapshot suspends its contract; this prototype has no new
    resolution endpoint, so a later bare SUCCESS cannot resolve that dispute.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded: Counter[str] = Counter()
    visible_origin_records = 0
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ValueError("Every credit record must be a mapping")
        if raw.get("origin") != origin:
            excluded["OTHER_ORIGIN"] += 1
            continue
        if raw.get("provider_id") != provider_id:
            excluded["OTHER_PROVIDER"] += 1
            continue
        # Same-asset versions are not treated as interchangeable histories or
        # hidden provider-level transfer assets. Different assets can inform
        # only the explicitly labelled empirical provider-prior methods.
        if raw.get("asset_id") == asset_id and version is not None and _asset_version(raw) != version:
            excluded["OTHER_TARGET_VERSION"] += 1
            continue
        try:
            event = _time(raw.get("event_time"), "event_time")
            observed = _time(raw.get("observed_at"), "observed_at")
            created = _time(raw.get("contract_created_at"), "contract_created_at")
            deadline = _time(raw.get("agreed_deadline"), "agreed_deadline")
        except ValueError:
            excluded["INVALID_OR_MISSING_TIME"] += 1
            continue
        if event > now or observed > now or created > now:
            excluded["FUTURE_EVIDENCE"] += 1
            continue
        if observed < event or event < created or deadline <= created:
            excluded["INVALID_CHRONOLOGY"] += 1
            continue
        visible_origin_records += 1
        state = raw.get("status")
        if state not in _STATES:
            excluded["UNSUPPORTED_STATUS"] += 1
            continue
        if state in {"CHECK", "ACCESS_DENIED"}:
            excluded[state] += 1
            continue
        # These flags must be computed at the trusted storage boundary. Integer
        # 1 or an arbitrary truthy string is deliberately not accepted as True.
        if not all(raw.get(flag) is True for flag in
                   ("verified", "independent_verifier", "commitment_verified")):
            excluded["UNVERIFIED_OR_NOT_INDEPENDENT"] += 1
            continue
        try:
            cid = _id(raw.get("contract_id"), "contract_id")
            _id(raw.get("asset_id"), "asset_id")
            _id(_asset_version(raw), "asset_version")
        except ValueError:
            excluded["INVALID_IDENTIFIER"] += 1
            continue
        record = dict(raw)
        record.update(_event=event, _observed=observed, _created=created, _deadline=deadline)
        groups[cid].append(record)

    history: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    repeated_reports = 0
    for cid, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (row["_observed"], row["_event"], str(row.get("event_id", ""))))
        first = rows[0]
        summary = {"contract_id": cid, "provider_id": provider_id, "asset_id": first["asset_id"],
                   "asset_version": _asset_version(first), "outcome": None,
                   "state": "PENDING" if now < first["_deadline"] else "UNKNOWN"}
        # A contract cannot change identity, version or its signed deadline.
        identity = lambda row: (row["asset_id"], _asset_version(row), row["_created"], row["_deadline"])
        if any(identity(row) != identity(first) for row in rows):
            summary.update(state="DISPUTED", reason="CONFLICTING_CONTRACT_IDENTITY")
            states.append(summary)
            continue
        if any(row["status"] == "DISPUTED" for row in rows):
            summary.update(state="DISPUTED", reason="SIGNED_DISPUTE_SUSPENDS_CONTRACT")
            states.append(summary)
            continue
        substantive: list[tuple[dict[str, Any], int, bool]] = []
        inconsistent = False
        for row in rows:
            state = row["status"]
            if state not in {"SUCCESS", "FAILURE"}:
                if row["status"] == "UNKNOWN" and not substantive:
                    summary["state"] = "UNKNOWN"
                continue
            value = row.get("outcome")
            if not (type(value) is bool or type(value) is int and value in (0, 1)):
                inconsistent = True
                continue
            if int(value) != int(state == "SUCCESS"):
                inconsistent = True
                continue
            reason = str(row.get("reason", row.get("failure_reason", ""))).upper()
            deadline_failure = row["_event"] > row["_deadline"] or reason in _TIMEOUT_REASONS
            if reason in _TIMEOUT_REASONS and row["_event"] < row["_deadline"]:
                excluded["UNMATURED_TIMEOUT_NOT_FAILURE"] += 1
                continue
            # A success observed late is still success if delivery occurred on
            # time; a delivery which actually occurred late is a sticky failure.
            effective_value = 0 if deadline_failure else int(value)
            substantive.append((row, effective_value, deadline_failure))
        if inconsistent:
            summary.update(state="DISPUTED", reason="INCONSISTENT_SIGNED_OUTCOME")
            states.append(summary)
            continue
        if substantive:
            repeated_reports += len(substantive) - 1
            failures = [item for item in substantive if item[2]]
            if failures:
                chosen, outcome, _ = failures[0]
            elif len({item[1] for item in substantive}) > 1:
                summary.update(state="DISPUTED", reason="CONFLICTING_VERIFIED_REPORTS")
                states.append(summary)
                continue
            else:
                chosen, outcome, _ = substantive[0]
            summary.update(state="SUCCESS" if outcome else "FAILURE", outcome=outcome,
                           event_time=chosen["event_time"], observed_at=chosen["observed_at"])
            history.append({"contract_id": cid, "provider_id": provider_id,
                            "asset_id": first["asset_id"], "outcome": outcome,
                            "event_time": chosen["event_time"], "observed_at": chosen["observed_at"],
                            "verified": True, "status": summary["state"]})
        states.append(summary)
    history.sort(key=lambda row: (_time(row["observed_at"], "observed_at"), row["contract_id"]))
    return history, {
        "visible_origin_records": visible_origin_records,
        "excluded_records_by_reason": dict(sorted(excluded.items())),
        "deduplicated_substantive_reports": repeated_reports,
        "contract_states": states,
        "history_max_event_time": max((row["event_time"] for row in history),
                                      key=lambda value: _time(value, "event_time"), default=None),
        "history_max_observed_at": max((row["observed_at"] for row in history),
                                       key=lambda value: _time(value, "observed_at"), default=None),
    }


def assess_credit(
    records: Sequence[Mapping[str, Any]], asset_id: str, provider_id: str, at: str,
    origin: str = "local_operational", version: str | None = None,
    *, method: str = "decay30", minimum_history: int = 5,
) -> dict[str, Any]:
    """Return separate-origin credit estimates without retraining any model.

    Required trusted terminal fields: contract_id/provider_id/asset_id,
    asset_version, origin, status, outcome (bool or integer 0/1), event_time,
    observed_at, contract_created_at, agreed_deadline and the three strict bool
    flags verified/independent_verifier/commitment_verified. An event_id and
    reason aid audit. Several checks/results of one contract count only once.

    ``version=None`` explicitly pools versions of the target asset; the API
    should pass its selected version. Other versions of that target are excluded
    completely when version is specified. Provider prior transfer uses other
    assets only. ``minimum_history`` is a display warning, not a calibration or
    deployment threshold. No result is a commercial reputation claim.
    """
    _id(asset_id, "asset_id")
    _id(provider_id, "provider_id")
    if origin not in ORIGINS:
        raise ValueError(f"origin must be exactly one of {sorted(ORIGINS)}; mixed-origin scoring is forbidden")
    if version is not None:
        _id(version, "version")
    if method not in _METHODS:
        raise ValueError(f"method must be one of {sorted(_METHODS)}")
    if type(minimum_history) is not int or minimum_history < 1:
        raise ValueError("minimum_history must be a positive integer")
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("records must be a sequence of trusted storage records")
    now = _time(at, "at")
    as_of = now.isoformat()
    history, diagnostics = _history(records, asset_id, provider_id, now, origin, version)
    methods = {name: predict_credit(history, provider_id, asset_id, as_of, **parameters)
               for name, parameters in _METHODS.items()}
    selected = methods[method]
    insufficient = selected["n_history"] < minimum_history
    return {
        "asset_id": asset_id, "provider_id": provider_id, "version": version,
        "as_of_time": as_of, "origin": origin, "source": SOURCE_LABELS[origin],
        "method": method, "q": selected["probability"], "q_lower": selected["lower"],
        "q_upper": selected["upper"], "n_history": selected["n_history"],
        "successes": selected["successes"], "failures": selected["failures"],
        "history_status": "INSUFFICIENT_HISTORY" if insufficient else "LIMITED_RESEARCH_HISTORY",
        "insufficient_history": insufficient, "minimum_history_warning_threshold": minimum_history,
        "interval_scope": "UNCALIBRATED_MODEL_CONDITIONAL_INTERVAL_NOT_COMMERCIAL_CONFIDENCE",
        "trust_boundary": "SIGNATURE_AND_INDEPENDENCE_FLAGS_COMPUTED_BY_REGISTRY_NOT_THIS_ADAPTER",
        "state_adapter_scope": "SIMULATION_STATE_SEMANTICS_ADAPTED_TO_NEW_HTTP_ENVELOPE_NO_RESOLUTION_ENDPOINT",
        "deployment_eligible": False, "adapter_version": ADAPTER_VERSION,
        "methods": methods, "diagnostics": diagnostics,
    }
