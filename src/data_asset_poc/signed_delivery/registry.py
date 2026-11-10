"""Registry/assessment API with immutable identities, contracts and evidence."""
from __future__ import annotations
from datetime import timedelta
import json
import os
from pathlib import Path
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from .credit import assess_credit
from .security import keyring_from_config, canonical_hash, canonical_bytes, policy_allows, utcnow, parse_time
from .storage import Store
from .validation import validate_metadata, MODES
from .valuation import LocalValuationService, IndexedLegacyValuationService


def create_registry(config_file: Path | str) -> FastAPI:
    config_file = Path(config_file).resolve()
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    keys = keyring_from_config(cfg, config_file.parent, "registry")
    sign, verify = keys.sign, keys.verify
    store = Store(config_file.parent / cfg.get("database", "registry.sqlite"))
    utility_path = (config_file.parent / cfg["utility_index"]).resolve()
    values = LocalValuationService(utility_path)
    legacy = IndexedLegacyValuationService(utility_path)
    app = FastAPI(title="Signed_delivery local research dataspace", version="0.2.0")

    @app.exception_handler(ValueError)
    def invalid(request, exc):
        with store.transaction() as db:
            store.audit(db, "INPUT_OR_SIGNATURE_REJECTED", {"path": request.url.path, "reason": str(exc)})
        return JSONResponse(status_code=400, content={"detail": str(exc), "deployment_eligible": False})

    @app.exception_handler(KeyError)
    def absent(_, exc):
        missing_resource = exc.args and exc.args[0] in {"ASSET_VERSION_NOT_FOUND", "CONTRACT_NOT_FOUND", "REPORT_NOT_FOUND", "UNKNOWN_LEGACY_INTERFACE", "OBSERVATION_REQUIRED_BEFORE_DISPUTE"}
        return JSONResponse(status_code=404 if missing_resource else 400, content={"detail": str(exc)})

    def asset_at(aid, version, at=None):
        if any(not isinstance(item, str) or not item for item in (aid, version)):
            raise ValueError("ASSET_ID_AND_VERSION_MUST_BE_EXPLICIT_STRINGS")
        if at is not None and not isinstance(at, str):
            raise ValueError("ASSESSMENT_TIME_MUST_BE_ISO_STRING")
        asset = store.asset(aid, version, at)
        if at and parse_time(asset["published_at"]) > parse_time(at):
            raise ValueError("ASSET_NOT_PUBLISHED_AT_QUERY_TIME")
        return asset

    @app.get("/health")
    def health():
        return {"status": "OK", "role": "registry", "pid": os.getpid(), "deployment_eligible": False}

    @app.get("/trust")
    def trust():
        return {"identities": keys.public_registry(), "trust_scope": "LOCAL_CONFIGURATION_ONLY"}

    @app.post("/assets")
    def register(envelope: dict):
        meta = verify(envelope, expected_role="provider")
        if meta["provider_id"] != envelope["signer_id"] or meta["provider_id"] not in cfg["provider_urls"]:
            raise ValueError("PROVIDER_NOT_PRETRUSTED")
        validate_metadata(meta)
        with store.transaction() as db:
            owner = db.execute("SELECT provider_id FROM assets WHERE asset_id=? LIMIT 1", (meta["asset_id"],)).fetchone()
            if owner and owner[0] != meta["provider_id"]:
                raise HTTPException(409, "ASSET_ID_OWNED_BY_ANOTHER_DEMO_PROVIDER")
            old = db.execute("SELECT envelope FROM assets WHERE asset_id=? AND version=?", (meta["asset_id"], meta["version"])).fetchone()
            if old:
                if json.loads(old[0]) != envelope:
                    raise HTTPException(409, "IMMUTABLE_VERSION_CONFLICT")
                return {"asset": asset_at(meta["asset_id"], meta["version"]), "replayed": True}
            db.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (meta["asset_id"], meta["version"], meta["provider_id"],
                canonical_bytes(envelope).decode(), "VERIFIED_METADATA"))
            db.execute("INSERT INTO asset_state_events VALUES(?,?,?,?,?,?)", (canonical_hash(envelope), meta["asset_id"], meta["version"],
                "VERIFIED_METADATA", utcnow(), canonical_bytes(envelope).decode()))
            store.audit(db, "ASSET_REGISTERED", {"asset_id": meta["asset_id"], "version": meta["version"], "content_hash": meta["content_hash"]})
        return {"asset": {**meta, "state": "VERIFIED_METADATA", "content_verification": "NOT_YET_DELIVERED"}, "replayed": False}

    @app.get("/assets")
    def list_assets():
        return {"assets": [{**json.loads(r["envelope"])["payload"], "state": r["state"]} for r in store.read("SELECT envelope,state FROM assets ORDER BY asset_id,version")]}

    @app.get("/assets/{asset_id}/versions")
    def versions(asset_id: str):
        return {"asset_id": asset_id, "versions": store.read("SELECT version,state FROM assets WHERE asset_id=? ORDER BY version", (asset_id,))}

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str, version: str, at: str | None = None):
        return asset_at(asset_id, version, at)

    @app.get("/assets/{asset_id}/state-events")
    def state_events(asset_id: str, version: str):
        asset_at(asset_id, version)
        return {"events": [{**r, "envelope": json.loads(r["envelope"])} for r in store.read(
            "SELECT * FROM asset_state_events WHERE asset_id=? AND version=? ORDER BY rowid", (asset_id, version))],
            "clock": "REGISTRY_SERVER_OBSERVATION_TIME"}

    @app.post("/assets/{asset_id}/status")
    def change_state(asset_id: str, envelope: dict):
        p = verify(envelope, expected_role="provider")
        if p["asset_id"] != asset_id:
            raise ValueError("SIGNED_STATE_ASSET_ID_MUST_MATCH_ROUTE")
        a = asset_at(asset_id, p["version"])
        if a["provider_id"] != envelope["signer_id"] or not isinstance(p["state"], str) or p["state"] not in {"REVOKED", "EXPIRED"}:
            raise ValueError("INVALID_REVOCATION")
        if any(k in p for k in ("effective_at", "observed_at")):
            raise ValueError("STATE_TIME_IS_ASSIGNED_BY_REGISTRY")
        digest = canonical_hash(envelope)
        with store.transaction() as db:
            if db.execute("SELECT event_id FROM asset_state_events WHERE event_id=?", (digest,)).fetchone():
                return {"state": store.asset(asset_id, p["version"])["state"], "replayed": True, "deployment_eligible": False}
            current = db.execute("SELECT state FROM assets WHERE asset_id=? AND version=?", (asset_id, p["version"])).fetchone()[0]
            if current == p["state"]:
                return {"state": current, "replayed": True, "deployment_eligible": False}
            if current == "REVOKED" or p.get("expected_state", current) != current:
                raise HTTPException(409, "TERMINAL_OR_STALE_ASSET_STATE")
            db.execute("UPDATE assets SET state=? WHERE asset_id=? AND version=?", (p["state"], asset_id, p["version"]))
            effective = utcnow()
            db.execute("INSERT INTO asset_state_events VALUES(?,?,?,?,?,?)", (digest, asset_id, p["version"], p["state"], effective, canonical_bytes(envelope).decode()))
            store.audit(db, "ASSET_STATUS_CHANGED", {"asset_id": asset_id, **p, "effective_at": effective})
        return {"state": p["state"], "effective_at": effective, "replayed": False, "deployment_eligible": False}

    @app.post("/access-requests")
    def access(envelope: dict):
        p = verify(envelope, expected_role="consumer")
        actor, at = envelope["signer_id"], utcnow()
        a = asset_at(p["asset_id"], p["version"])
        if a["state"] != "VERIFIED_METADATA" or not policy_allows(a, actor, p["purpose"], at):
            with store.transaction() as db:
                store.audit(db, "ACCESS_DENIED", {"asset_id": a["asset_id"], "actor": actor, "request_id": p.get("request_id"), "credit_event_created": False})
            raise HTTPException(403, "USAGE_POLICY_DENIED_NO_CONTRACT_CREATED")
        ttl = p.get("ttl_seconds", 5)
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not .2 <= ttl <= 30:
            raise ValueError("TTL_MUST_BE_BETWEEN_0_2_AND_30_SECONDS")
        mode = p.get("fault_mode", "normal")
        if not isinstance(mode, str) or mode not in MODES or mode != "normal" and not cfg.get("enable_fault_injection", False):
            raise ValueError("INVALID_OR_DISABLED_FAULT_MODE")
        rid = p["request_id"]
        if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
            raise ValueError("INVALID_REQUEST_ID")
        request_hash = canonical_hash(envelope)
        with store.transaction() as db:
            current_state = db.execute("SELECT state FROM assets WHERE asset_id=? AND version=?", (a["asset_id"], a["version"])).fetchone()[0]
            if current_state != "VERIFIED_METADATA":
                raise HTTPException(403, "ASSET_REVOKED_OR_EXPIRED_NO_CONTRACT_CREATED")
            old = db.execute("SELECT body_hash,contract FROM requests WHERE request_id=?", (rid,)).fetchone()
            if old:
                if old[0] != request_hash:
                    raise HTTPException(409, "IDEMPOTENCY_KEY_BODY_CONFLICT")
                return {"contract": json.loads(old[1]), "replayed": True}
            c = {"contract_id": "contract-" + uuid.uuid4().hex, "request_id": rid,
                "provider_id": a["provider_id"], "asset_id": a["asset_id"], "asset_version": a["version"],
                "expected_content_hash": a["content_hash"], "expected_schema_hash": a["schema_hash"], "schema": a["schema"],
                "metadata_hash": canonical_hash({k: v for k, v in a.items() if k != "state"}),
                "consumer_id": actor, "purpose": p["purpose"], "created_at": at,
                "agreed_deadline": (parse_time(at)+timedelta(seconds=ttl)).isoformat(),
                "origin": "local_operational" if mode == "normal" else "fault_injection", "fault_mode": mode,
                "provider_url": cfg["provider_urls"][a["provider_id"]]}
            token = sign("registry", c)
            db.execute("INSERT INTO requests VALUES(?,?,?)", (rid, request_hash, canonical_bytes(token).decode()))
            db.execute("INSERT INTO contracts VALUES(?,?)", (c["contract_id"], canonical_bytes(c).decode()))
            store.audit(db, "CONTRACT_ACCEPTED", {"contract_id": c["contract_id"], "origin": c["origin"]})
        return {"contract": token, "replayed": False}

    @app.get("/contracts")
    def contracts():
        return {"contracts": [json.loads(r["body"]) for r in store.read("SELECT body FROM contracts ORDER BY rowid")]}

    @app.get("/contracts/{contract_id}/authorization")
    def authorization(contract_id: str):
        rows = store.read("SELECT body FROM contracts WHERE contract_id=?", (contract_id,))
        if not rows:
            raise KeyError("CONTRACT_NOT_FOUND")
        c, now = json.loads(rows[0]["body"]), utcnow()
        a = asset_at(c["asset_id"], c["asset_version"])
        authorized = (a["state"] == "VERIFIED_METADATA" and parse_time(c["created_at"]) <= parse_time(now) <= parse_time(c["agreed_deadline"])
            and policy_allows(a, c["consumer_id"], c["purpose"], now))
        return sign("registry", {"contract_id": contract_id, "asset_id": c["asset_id"], "version": c["asset_version"],
            "current_state": a["state"], "authorized": authorized, "checked_at": now,
            "scope": "CURRENT_ASSET_STATUS_POLICY_AND_CONTRACT_DEADLINE"})

    @app.post("/deliveries")
    def accept_receipt(envelope: dict):
        p = verify(envelope, expected_actor="verifier", expected_role="verifier")
        rows = store.read("SELECT body FROM contracts WHERE contract_id=?", (p["contract_id"],))
        if not rows:
            raise KeyError("CONTRACT_NOT_FOUND")
        c = json.loads(rows[0]["body"])
        commitment = verify(p["commitment"], expected_actor=c["provider_id"], expected_role="provider")
        if any(commitment.get(k) != v for k, v in c.items()) or commitment.get("kind") != "DELIVERY_COMMITMENT":
            raise ValueError("COMMITMENT_DOES_NOT_BIND_EXACT_CONTRACT")
        at = utcnow()
        event_time = parse_time(p["event_time"])
        if not parse_time(c["created_at"]) <= event_time <= parse_time(at):
            raise ValueError("INVALID_OR_FUTURE_RECEIPT_TIME")
        if p["evidence_origin"] != c["origin"]:
            raise ValueError("ORIGIN_MUST_MATCH_ACCEPTED_CONTRACT")
        if type(p["row_count"]) is not int or p["row_count"] < 0:
            raise ValueError("ROW_COUNT_MUST_BE_NONNEGATIVE_INTEGER")
        checks = {"hash_match": p["actual_content_hash"] == c["expected_content_hash"],
            "schema_match": p["actual_schema_hash"] == c["expected_schema_hash"] and p["row_count"] > 0,
            "version_match": p["received_version"] == c["asset_version"],
            "contract_match": p["received_contract_id"] == c["contract_id"],
            "on_time": event_time <= parse_time(c["agreed_deadline"])}
        if p["checks"] != checks or any(type(x) is not bool for x in p["checks"].values()):
            raise ValueError("RECEIPT_CHECKS_DISAGREE_WITH_SIGNED_OBSERVATIONS")
        transport = p["transport_status"]
        if transport == "RECEIVED":
            if not isinstance(p["received_bytes"], int) or isinstance(p["received_bytes"], bool) or p["received_bytes"] <= 0:
                raise ValueError("RECEIVED_BYTES_REQUIRED")
            status = "SUCCESS" if all(checks.values()) else "FAILURE"
        elif transport == "TIMEOUT" and event_time >= parse_time(c["agreed_deadline"]):
            status = "FAILURE"
        elif isinstance(transport, str) and transport in {"TIMEOUT", "UNKNOWN"}:
            status = "PENDING" if event_time < parse_time(c["agreed_deadline"]) else "UNKNOWN"
        else:
            raise ValueError("INVALID_TRANSPORT_STATUS")
        if p["status"] != status:
            raise ValueError("PROVIDER_OR_VERIFIER_CANNOT_SELF_DECLARE_SUCCESS")
        if status == "SUCCESS":
            delivered_asset = asset_at(c["asset_id"], c["asset_version"], p["event_time"])
            if delivered_asset["state"] != "VERIFIED_METADATA" or not policy_allows(delivered_asset, c["consumer_id"], c["purpose"], p["event_time"]):
                raise ValueError("SUCCESS_RECEIPT_EVENT_ASSET_NOT_AUTHORIZED")
        record = {"contract_id": c["contract_id"], "provider_id": c["provider_id"], "asset_id": c["asset_id"],
            "asset_version": c["asset_version"], "origin": c["origin"], "status": status,
            "outcome": True if status == "SUCCESS" else False if status == "FAILURE" else None,
            "contract_created_at": c["created_at"], "agreed_deadline": c["agreed_deadline"],
            "event_time": p["event_time"], "observed_at": at, "verified": True,
            "independent_verifier": envelope["signer_id"] != c["provider_id"], "commitment_verified": True,
            "reason": "TIMEOUT" if transport == "TIMEOUT" else "INTEGRITY_OR_VERSION" if status == "FAILURE" else transport,
            "receipt_hash": canonical_hash(envelope), "checks": checks, "signed_receipt": envelope}
        digest = canonical_hash(envelope)
        with store.transaction() as db:
            previous_event = db.execute("SELECT record FROM receipt_events WHERE event_id=?", (digest,)).fetchone()
            if previous_event:
                return {"record": json.loads(previous_event[0]), "replayed": True}
            old = db.execute("SELECT body_hash,record FROM receipts WHERE contract_id=?", (c["contract_id"],)).fetchone()
            if old:
                if old[0] == digest:
                    return {"record": json.loads(old[1]), "replayed": True}
                if json.loads(old[1])["status"] not in {"PENDING", "UNKNOWN"}:
                    raise HTTPException(409, "FINAL_RECEIPT_IMMUTABLE_OR_DISPUTE_REQUIRED")
            db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?)", (c["contract_id"], digest, canonical_bytes(envelope).decode(), canonical_bytes(record).decode()))
            db.execute("INSERT INTO receipt_events VALUES(?,?,?)", (digest, c["contract_id"], canonical_bytes(record).decode()))
            store.audit(db, "DELIVERY_VERIFIED", record)
        return {"record": record, "replayed": False}

    @app.post("/deliveries/{contract_id}/disputes")
    def dispute(contract_id: str, envelope: dict):
        p = verify(envelope, expected_actor="verifier")
        if p["contract_id"] != contract_id or not isinstance(p.get("reason"), str) or not p["reason"]:
            raise ValueError("EXPLICIT_DISPUTE_REQUIRED")
        event_time, observed = parse_time(p["event_time"]), utcnow()
        digest = canonical_hash(envelope)
        with store.transaction() as db:
            previous = db.execute("SELECT record FROM receipt_events WHERE event_id=?", (digest,)).fetchone()
            if previous:
                return {"record": json.loads(previous[0]), "replayed": True, "resolution_not_implemented": True}
            old = db.execute("SELECT record FROM receipts WHERE contract_id=?", (contract_id,)).fetchone()
            if not old:
                raise KeyError("OBSERVATION_REQUIRED_BEFORE_DISPUTE")
            record = json.loads(old[0])
            if not parse_time(record["event_time"]) <= event_time <= parse_time(observed):
                raise ValueError("INVALID_DISPUTE_TIME")
            record.update(status="DISPUTED", outcome=None, event_time=p["event_time"], observed_at=observed,
                reason=p["reason"], signed_dispute=envelope)
            db.execute("UPDATE receipts SET body_hash=?,envelope=?,record=? WHERE contract_id=?", (digest, canonical_bytes(envelope).decode(), canonical_bytes(record).decode(), contract_id))
            db.execute("INSERT INTO receipt_events VALUES(?,?,?)", (digest, contract_id, canonical_bytes(record).decode()))
            store.audit(db, "DELIVERY_DISPUTED_NOT_AUTOMATIC_FAILURE", record)
        return {"record": record, "replayed": False, "resolution_not_implemented": True}

    @app.get("/deliveries/{contract_id}")
    def delivery(contract_id: str):
        row = store.read("SELECT body FROM contracts WHERE contract_id=?", (contract_id,))
        if not row:
            raise KeyError("CONTRACT_NOT_FOUND")
        c = json.loads(row[0]["body"])
        receipt = store.read("SELECT record FROM receipts WHERE contract_id=?", (contract_id,))
        return {"contract": c, "record": json.loads(receipt[0]["record"]) if receipt else None,
                "status": json.loads(receipt[0]["record"])["status"] if receipt else "PENDING" if parse_time(utcnow()) < parse_time(c["agreed_deadline"]) else "UNKNOWN"}

    def credit_for(a, at, origin="local_operational", method="decay30"):
        if not isinstance(origin, str) or not isinstance(method, str):
            raise ValueError("CREDIT_ORIGIN_AND_METHOD_MUST_BE_STRINGS")
        if parse_time(at) > parse_time(utcnow()):
            raise ValueError("FUTURE_ASSESSMENT_NOT_ALLOWED")
        if origin == "simulated":
            raise ValueError("USE_SEPARATE_LEGACY_INTERFACE_FOR_FROZEN_SIMULATED_CREDIT")
        result = assess_credit(store.records(), a["asset_id"], a["provider_id"], at, origin, a["version"], method=method)
        disputed = any(r["asset_id"] == a["asset_id"] and r["asset_version"] == a["version"] and r["state"] == "DISPUTED"
            for r in result["diagnostics"]["contract_states"])
        return {**result, "asset_state_at_assessment": a["state"],
            "current_asset_state": store.asset(a["asset_id"], a["version"])["state"],
            "active_dispute": disputed, "recommendation_blocked": disputed or a["state"] != "VERIFIED_METADATA",
            "state_time_semantics": "REGISTRY_OBSERVED_STATE_EVENTS_AS_OF_QUERY"}

    @app.get("/credit/{asset_id}")
    def credit(asset_id: str, version: str, origin: str = "local_operational", at: str | None = None, method: str = "decay30"):
        at = at or utcnow()
        return credit_for(asset_at(asset_id, version, at), at, origin, method)

    def evaluate(p, actor, at):
        if not isinstance(p["task_id"], str) or not p["task_id"] or not isinstance(p.get("economics_id", "fp1_fn5"), str):
            raise ValueError("TASK_AND_ECONOMICS_ID_MUST_BE_STRINGS")
        a = asset_at(p["asset_id"], p["version"], at)
        q = credit_for(a, at, p.get("origin", "local_operational"), p.get("method", "decay30"))
        # Metadata signatures establish registration, not receipt integrity.
        delivered = any(r["asset_id"] == a["asset_id"] and r["asset_version"] == a["version"]
            and r["state"] == "SUCCESS" for r in q["diagnostics"]["contract_states"])
        result = values.generate_valuation_report(a, p["task_id"], at, credit=q,
            economics_id=p.get("economics_id", "fp1_fn5"), evidence_verified=delivered and not q["recommendation_blocked"],
            policy_verified=a["state"] == "VERIFIED_METADATA" and policy_allows(a, actor, p.get("purpose", "research"), at),
            economic_overrides=p.get("economic_overrides"))
        return {**result, "asset_state_at_assessment": a["state"], "current_asset_state": q["current_asset_state"],
            "active_dispute": q["active_dispute"], "recommendation_blocked": q["recommendation_blocked"],
            "assessment_time_scope": "HISTORICAL_AS_OF_INFORMATION_NOT_CURRENT_AUTHORIZATION" if p.get("as_of_time") else "CURRENT_INFORMATION"}

    def save_report(result):
        rid = "report-" + uuid.uuid4().hex
        with store.transaction() as db:
            db.execute("INSERT INTO reports VALUES(?,?)", (rid, canonical_bytes(result).decode()))
            store.audit(db, "VALUATION_RECORDED", {"report_id": rid, "deployment_eligible": False})
        return {"report_id": rid, **result}

    @app.post("/valuations")
    def value(envelope: dict):
        p = verify(envelope, expected_role="consumer")
        return save_report(evaluate(p, envelope["signer_id"], p.get("as_of_time") or utcnow()))

    @app.post("/valuations/compare")
    def compare(envelope: dict):
        p = verify(envelope, expected_role="consumer")
        if not isinstance(p["assets"], list) or not 1 <= len(p["assets"]) <= 100 or any(not isinstance(a, dict) for a in p["assets"]):
            raise ValueError("ASSET_COMPARISON_REQUIRES_ONE_TO_100_EXPLICIT_OBJECTS")
        at = p.get("as_of_time") or utcnow()
        reports = [evaluate({**p, **asset}, envelope["signer_id"], at) for asset in p["assets"]]
        return save_report({"reports": reports, **values.compare_asset_values(reports, policy=p.get("policy", "point"))})

    @app.get("/legacy/coverage")
    def legacy_coverage():
        return legacy.coverage()

    @app.post("/legacy/{interface}")
    def legacy_interface(interface: str, envelope: dict):
        p = verify(envelope, expected_role="consumer")
        names = {"evaluate_conditional_utility", "estimate_delivery_reliability", "estimate_risk_adjusted_value", "compare_asset_values", "generate_valuation_report"}
        if interface not in names:
            raise KeyError("UNKNOWN_LEGACY_INTERFACE")
        # This explicit historical namespace never updates local credit.
        if interface == "compare_asset_values":
            result = legacy.compare_asset_values([tuple(x) for x in p["asset_versions"]], p["task_id"], p["as_of_time"],
                method=p.get("method"), economics_id=p.get("economics_id"), round_id=p.get("round_id"))
        else:
            result = getattr(legacy, interface)(p["asset_id"], p["version"], p["task_id"], p["as_of_time"],
                method=p.get("method"), economics_id=p.get("economics_id"), round_id=p.get("round_id"))
        return {"coverage": legacy.coverage(), "response": result, "deployment_eligible": False}

    @app.get("/audit/events")
    def audit(after: int = 0, limit: int = 1000):
        if after < 0 or not 1 <= limit <= 5000:
            raise ValueError("INVALID_AUDIT_PAGE")
        events = store.read("SELECT * FROM audit WHERE seq>? ORDER BY seq LIMIT ?", (after, limit))
        for item in events:
            item["body"] = json.loads(item["body"])
        return {"events": events, "next_after": events[-1]["seq"] if events else after}

    @app.get("/reports/{report_id}")
    def report(report_id: str):
        row = store.read("SELECT body FROM reports WHERE report_id=?", (report_id,))
        if not row:
            raise KeyError("REPORT_NOT_FOUND")
        return {"report_id": report_id, "report": json.loads(row[0]["body"])}

    @app.get("/reports")
    def report_list():
        return {"reports": [{"report_id": r["report_id"], "report": json.loads(r["body"])} for r in store.read(
            "SELECT report_id,body FROM reports ORDER BY rowid DESC LIMIT 100")]}

    return app
