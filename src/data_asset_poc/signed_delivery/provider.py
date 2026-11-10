"""Independent provider connector: policy-enforced commitments and HTTP bytes."""
from __future__ import annotations
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse
from .security import keyring_from_config, canonical_hash, policy_allows, utcnow, parse_time
from .storage import Store


def create_provider(config_file: Path | str) -> FastAPI:
    config_file = Path(config_file).resolve()
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    provider = cfg["provider_id"]
    keys = keyring_from_config(cfg, config_file.parent, provider)
    sign, verify = keys.sign, keys.verify
    registry_url = cfg["registry_url"].rstrip("/")
    store = Store(config_file.parent / "provider.sqlite")
    app = FastAPI(title="Signed_delivery isolated provider connector", version="0.2.0")

    @app.exception_handler(ValueError)
    def invalid(_, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    def missing(_, exc):
        return JSONResponse(status_code=400, content={"detail": "MISSING_FIELD:" + str(exc)})

    def authorize(envelope):
        request = verify(envelope, expected_role="consumer")
        token = request["contract"]
        contract = verify(token, expected_actor="registry")
        now = utcnow()
        if contract["provider_id"] != provider or contract["consumer_id"] != envelope["signer_id"]:
            raise HTTPException(403, "CONSUMER_OR_PROVIDER_MISMATCH")
        if not parse_time(contract["created_at"]) <= parse_time(now) <= parse_time(contract["agreed_deadline"]):
            raise HTTPException(410, "CONTRACT_NOT_ACTIVE")
        items = [entry for entry in cfg["assets"] if entry["metadata"]["payload"]["asset_id"] == contract["asset_id"]
                 and entry["metadata"]["payload"]["version"] == contract["asset_version"]]
        if len(items) != 1:
            raise HTTPException(404, "PROVIDER_VERSION_NOT_FOUND")
        item = items[0]
        metadata = verify(item["metadata"], expected_actor=provider)
        if (canonical_hash(metadata) != contract["metadata_hash"] or metadata["content_hash"] != contract["expected_content_hash"]
                or metadata["schema_hash"] != contract["expected_schema_hash"]):
            raise HTTPException(409, "TOKEN_METADATA_MISMATCH")
        if not policy_allows(metadata, envelope["signer_id"], contract["purpose"], now):
            with store.transaction() as db:
                store.audit(db, "ACCESS_DENIED", {"contract_id": contract["contract_id"]})
            raise HTTPException(403, "PROVIDER_POLICY_DENIED")
        # No bearer capability remains usable solely because its TTL is valid.
        # A registry outage or unverifiable status is fail-closed for delivery.
        try:
            with urllib.request.urlopen(registry_url + "/contracts/" + contract["contract_id"] + "/authorization", timeout=2) as response:
                status = verify(json.load(response), expected_actor="registry")
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            raise HTTPException(503, "REGISTRY_STATUS_UNAVAILABLE_FAIL_CLOSED") from exc
        if (status.get("contract_id") != contract["contract_id"] or status.get("asset_id") != contract["asset_id"]
                or status.get("version") != contract["asset_version"]):
            raise HTTPException(403, "REGISTRY_STATUS_BINDING_MISMATCH")
        age = (parse_time(utcnow()) - parse_time(status["checked_at"])).total_seconds()
        if not 0 <= age <= 5 or status.get("authorized") is not True or status.get("current_state") != "VERIFIED_METADATA":
            raise HTTPException(403, "ASSET_CURRENTLY_REVOKED_EXPIRED_OR_NOT_AUTHORIZED")
        return contract, item

    @app.get("/health")
    def health():
        import os
        return {"status": "OK", "role": "provider", "provider_id": provider, "pid": os.getpid(), "deployment_eligible": False}

    @app.get("/assets")
    def assets():
        return {"assets": [entry["metadata"] for entry in cfg["assets"]]}

    @app.post("/status-changes")
    def demo_status_change(envelope: dict):
        """Explicit local demonstration delegation, not a production admin API.

        The consumer never obtains a provider signing key.  This provider
        validates its own asset and signs its own Registry status request.
        """
        if cfg.get("enable_local_demo_control") is not True:
            raise HTTPException(403, "LOCAL_DEMO_STATUS_CONTROL_DISABLED")
        p = verify(envelope, expected_actor="consumer", expected_role="consumer")
        if not isinstance(p.get("state"), str) or p["state"] not in {"REVOKED", "EXPIRED"}:
            raise ValueError("INVALID_DEMO_STATUS")
        own = [entry for entry in cfg["assets"] if entry["metadata"]["payload"]["asset_id"] == p["asset_id"]
            and entry["metadata"]["payload"]["version"] == p["version"]]
        if len(own) != 1:
            raise HTTPException(404, "PROVIDER_VERSION_NOT_FOUND")
        payload = {"asset_id": p["asset_id"], "version": p["version"], "state": p["state"]}
        if "expected_state" in p:
            payload["expected_state"] = p["expected_state"]
        request = urllib.request.Request(registry_url + "/assets/" + p["asset_id"] + "/status",
            data=json.dumps(sign(provider, payload)).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("detail", "REGISTRY_STATUS_CHANGE_REJECTED")
            except (ValueError, AttributeError):
                message = "REGISTRY_STATUS_CHANGE_REJECTED"
            raise HTTPException(exc.code, message) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "REGISTRY_STATUS_CHANGE_UNAVAILABLE") from exc
        with store.transaction() as db:
            store.audit(db, "LOCAL_DEMO_STATUS_CHANGE_DELEGATED", {"asset_id": p["asset_id"], "version": p["version"],
                "state": p["state"], "actor": envelope["signer_id"], "credit_event_created": False})
        return {**result, "scope": "EXPLICIT_LOCAL_RESEARCH_CONTROL_NOT_PRODUCTION_ADMIN"}

    @app.post("/commitments")
    def commit(envelope: dict):
        contract, _ = authorize(envelope)
        commitment = sign(provider, {**contract, "kind": "DELIVERY_COMMITMENT"})
        if contract["fault_mode"] == "invalid_signature":
            commitment["signature"] = "A" * 88
        with store.transaction() as db:
            old = db.execute("SELECT body FROM contracts WHERE contract_id=?", (contract["contract_id"],)).fetchone()
            body = json.dumps(commitment, sort_keys=True)
            if old is not None and old[0] != body:
                raise HTTPException(409, "CONTRACT_COLLISION")
            db.execute("INSERT OR IGNORE INTO contracts VALUES(?,?)", (contract["contract_id"], body))
            store.audit(db, "COMMITMENT_RETURNED", {"contract_id": contract["contract_id"], "origin": contract["origin"]})
        return commitment

    @app.post("/transfers/{contract_id}")
    def transfer(contract_id: str, envelope: dict):
        contract, item = authorize(envelope)
        if contract_id != contract["contract_id"]:
            raise HTTPException(409, "WRONG_CONTRACT")
        if not store.read("SELECT contract_id FROM contracts WHERE contract_id=?", (contract_id,)):
            raise HTTPException(409, "COMMITMENT_REQUIRED")
        mode = contract["fault_mode"]
        if mode != "normal" and not cfg.get("enable_fault_injection", False):
            raise HTTPException(403, "FAULT_INJECTION_DISABLED")
        if mode == "unknown":
            raise HTTPException(503, "INJECTED_MISSING_EVIDENCE_NOT_DEFAULT")
        if mode == "timeout":
            time.sleep(max(0, (parse_time(contract["agreed_deadline"])-parse_time(utcnow())).total_seconds()) + .4)
        target = (config_file.parent / item["file"]).resolve()
        target.relative_to(config_file.parent)
        data = target.read_bytes()
        if mode == "tamper":
            data += b"\n"
        if mode == "schema_mismatch":
            data = b"wrong_header\n1\n"
        version = contract["asset_version"] + "-WRONG" if mode == "wrong_version" else contract["asset_version"]
        with store.transaction() as db:
            store.audit(db, "HTTP_BYTES_SENT", {"contract_id": contract_id, "bytes": len(data), "origin": contract["origin"]})
        return Response(data, media_type="text/csv", headers={"X-Asset-Version": version, "X-Contract-Id": contract_id})

    return app
