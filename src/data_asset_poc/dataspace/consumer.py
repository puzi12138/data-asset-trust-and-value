"""Independent Consumer/Verifier role: fetch HTTP bytes, then sign observations."""
from __future__ import annotations
import json
from pathlib import Path
import socket
import urllib.error
import urllib.request
from .security import sign, verify, byte_hash, canonical_bytes, utcnow, parse_time
from .validation import check_schema


class HTTPFailure(RuntimeError):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"HTTP {status}: {body}")


class HTTPClient:
    def __init__(self, trace: Path | None = None):
        self.trace = trace

    def request(self, method: str, url: str, payload=None, *, timeout: float = 10) -> tuple[int, dict, bytes]:
        entry = {"recorded_at": utcnow(), "request": {"method": method, "url": url, "body": payload}}
        request = urllib.request.Request(url, data=canonical_bytes(payload) if payload is not None else None,
            headers={"Content-Type": "application/json"}, method=method)
        try:
            try:
                response = urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status, headers, data = response.status, dict(response.headers.items()), response.read(5*1024*1024+1)
                if len(data) > 5*1024*1024:
                    raise ValueError("PROTOTYPE_RESPONSE_SIZE_LIMIT_5_MIB")
            try:
                body = json.loads(data)
            except (ValueError, UnicodeError):
                body = None
            entry["response"] = {"status": status, "headers": headers, "body": body,
                "body_sha256": byte_hash(data), "received_bytes": len(data)}
            return status, headers, data
        except Exception as exc:
            entry["response"] = {"error_type": type(exc).__name__, "error": str(exc)}
            raise
        finally:
            if self.trace:
                self.trace.parent.mkdir(parents=True, exist_ok=True)
                with self.trace.open("a", encoding="utf-8") as stream:
                    stream.write(canonical_bytes(entry).decode() + "\n")

    def json(self, method: str, url: str, payload=None, *, timeout=10):
        status, _, data = self.request(method, url, payload, timeout=timeout)
        body = json.loads(data)
        if not 200 <= status < 300:
            raise HTTPFailure(status, body)
        return body


def perform_delivery(registry_url: str, token: dict, evidence_dir: Path, client: HTTPClient | None = None) -> dict:
    """No provider success flag is used. Independent signed observations only."""
    client = client or HTTPClient()
    c = verify(token, expected_actor="registry")
    request = sign(c["consumer_id"], {"contract": token})
    commitment = client.json("POST", c["provider_url"] + "/commitments", request)
    try:
        promised = verify(commitment, expected_actor=c["provider_id"])
        if any(promised.get(k) != v for k, v in c.items()):
            raise ValueError("COMMITMENT_BINDING_MISMATCH")
    except ValueError as exc:
        # Submit the invalid proof so the registry rejection is independently
        # exercised. Its contract remains unknown/pending, not a binary failure.
        bad = sign("verifier", {"contract_id": c["contract_id"], "commitment": commitment})
        try:
            client.json("POST", registry_url + "/deliveries", bad)
            raise AssertionError("Registry accepted invalid provider proof")
        except HTTPFailure as rejected:
            return {"contract_id": c["contract_id"], "status": "EVIDENCE_REJECTED", "reason": str(exc),
                    "registry_http_status": rejected.status, "origin": c["origin"], "signed_submission": bad,
                    "received_file": None, "received_bytes": 0, "credit_event_created": False}
    data, headers, transport, error = b"", {}, "UNKNOWN", None
    try:
        remaining = max(.05, (parse_time(c["agreed_deadline"])-parse_time(utcnow())).total_seconds()+.05)
        status, headers, data = client.request("POST", c["provider_url"] + "/transfers/" + c["contract_id"], request, timeout=remaining)
        transport = "RECEIVED" if status == 200 else "UNKNOWN"
        if status != 200:
            error, data = f"HTTP_{status}", b""
    except (TimeoutError, socket.timeout):
        transport, error = "TIMEOUT", "RECEIVE_DEADLINE_EXCEEDED"
    except urllib.error.URLError as exc:
        # Connectivity errors before the deadline are not a known violation.
        transport, error = ("TIMEOUT" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "UNKNOWN"), str(exc)
    event_time = utcnow()
    schema_ok, schema_hash, rows = check_schema(data, c["schema"]) if transport == "RECEIVED" else (False, None, 0)
    normalized = {k.lower(): v for k, v in headers.items()}
    actual_hash = byte_hash(data) if transport == "RECEIVED" else None
    checks = {"hash_match": actual_hash == c["expected_content_hash"],
        "schema_match": schema_ok and schema_hash == c["expected_schema_hash"],
        "version_match": normalized.get("x-asset-version") == c["asset_version"],
        "contract_match": normalized.get("x-contract-id") == c["contract_id"],
        "on_time": parse_time(event_time) <= parse_time(c["agreed_deadline"])}
    state = ("SUCCESS" if all(checks.values()) else "FAILURE") if transport == "RECEIVED" else (
        "FAILURE" if transport == "TIMEOUT" and not checks["on_time"] else "PENDING" if checks["on_time"] else "UNKNOWN")
    receipt = sign("verifier", {"contract_id": c["contract_id"], "commitment": commitment, "event_time": event_time,
        "evidence_origin": c["origin"], "transport_status": transport, "received_bytes": len(data),
        "actual_content_hash": actual_hash, "actual_schema_hash": schema_hash, "row_count": rows,
        "received_version": normalized.get("x-asset-version"), "received_contract_id": normalized.get("x-contract-id"),
        "checks": checks, "status": state, "transport_error": error})
    received_file = None
    if transport == "RECEIVED":
        evidence_dir = Path(evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        target = evidence_dir / (c["contract_id"] + ".csv")
        target.write_bytes(data)
        received_file = str(target)
    result = client.json("POST", registry_url + "/deliveries", receipt)
    return {"contract_id": c["contract_id"], "status": state, "origin": c["origin"], "checks": checks,
        "received_bytes": len(data), "received_hash": actual_hash, "received_file": received_file,
        "signed_submission": receipt, "registry_result": result, "credit_event_created": state in {"SUCCESS", "FAILURE"}}
