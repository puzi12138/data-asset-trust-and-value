"""Strict server-resolved same-version joint reports, additive to Visual_evidence_live.

The browser supplies registry identifiers only. No uploaded result, path,
receipt string or display name is accepted as a binding/authentication proof.
Saved reports are re-resolved and reverified before download; a changed source
cannot silently turn an old report into evidence for a new version.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..self_service_value.gateway import (RUN_ID,
    checked_path, load_json, parse_json, registered_input, shared_read_bytes)
from ..visual_evidence_live.gateway import create_live_gateway, bounded_json
from ..visual_evidence_live.registry import RegistryError
from . import TrustMaterial, MeasurementMaterial, canonical, digest, coordinate_joint_assessment

CASE_ID = re.compile(r"case_[0-9a-f]{32}")
JOINT_ID = re.compile(r"joint_[0-9a-f]{32}")
ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
MAX_ACTION_BYTES = 65536
MAX_SAVED_BYTES = 64 * 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path, root):
    path = checked_path(path, root)
    if path.stat().st_size > MAX_SAVED_BYTES:
        raise ValueError("JOINT_SAVED_ARTIFACT_SIZE_LIMIT")
    return shared_read_bytes(path)


def create_joint_gateway(config_file: Path | str, project_root: Path | str | None = None,
        registry_root: Path | str | None = None, adapter=None):
    """Retain every historical route and the inherited loopback middleware."""
    app = create_live_gateway(config_file, project_root, registry_root, adapter)
    self_root = app.state.self_service_root
    workspace = self_root.parent
    output = checked_path(workspace / "same_asset_joint", workspace)
    output.mkdir(exist_ok=True)
    app.state.same_asset_joint_root = output
    app.title = "Exact-version dataset trust and measured task-value assessment"

    def trust_material(case_id):
        registry = app.state.visual_case_registry
        case = registry.get_case(case_id)  # Includes local MAC and every file hash.
        folder = checked_path(registry.root / "cases" / case_id, registry.root)
        raw = _read(folder / "dataset.csv", registry.root)
        evidence = {row["evidence_id"]: registry.evidence_bytes(case_id, row["evidence_id"])[0]
                    for row in case["evidence"]}
        return TrustMaterial(kind="VISUAL_CASE", asset=case["asset"], asset_bytes=raw,
            report_bytes=canonical(case), protocol_bytes=canonical(case["protocol"]), evidence_bytes=evidence)

    def measurement_material(run_id):
        folder = checked_path(self_root / "runs" / run_id, self_root)
        status_raw = _read(folder / "status.json", self_root)
        state = parse_json(status_raw)
        if state.get("status") != "COMPLETED":
            # Pending/failed jobs have no legitimate completed value artifact.
            # The core records NOT_EVALUATED from the actual saved status.
            return MeasurementMaterial(b"{}", status_raw, b"{}", {}, {})
        report_raw = _read(folder / "report.json", self_root)
        report = parse_json(report_raw)
        if report.get("status") == "NOT_EVALUATED":
            return MeasurementMaterial(report_raw, status_raw, b"{}", {}, {})
        request_raw = _read(folder / "request.json", self_root)
        request = parse_json(request_raw)
        names = report.get("artifacts")
        if not isinstance(names, dict):
            raise ValueError("JOINT_MEASUREMENT_ARTIFACT_MAP_INVALID")
        artifacts = {}
        for name in set(names) | {"report.json"}:
            if not isinstance(name, str) or not ARTIFACT_NAME.fullmatch(name):
                raise ValueError("JOINT_MEASUREMENT_ARTIFACT_NAME_INVALID")
            artifacts[name] = _read(folder / "measurement" / name, self_root)
        inputs = request.get("protocol", {}).get("inputs")
        if not isinstance(inputs, dict):
            raise ValueError("JOINT_MEASUREMENT_INPUT_MAP_INVALID")
        input_bytes = {}
        for role, pinned in inputs.items():
            metadata, raw, _ = registered_input(self_root, pinned["dataset_id"])
            # Preserve all schema, separator, version and byte-identity checks.
            actual = {key: metadata[key] for key in pinned}
            if actual != pinned:
                raise ValueError("JOINT_REGISTERED_INPUT_BINDING_CHANGED")
            input_bytes[role] = raw
        return MeasurementMaterial(report_bytes=report_raw,
            status_bytes=status_raw, request_bytes=request_raw,
            artifacts=artifacts, input_bytes=input_bytes)

    def resolve(payload, evaluated_at):
        run_id = payload.get("value_run_id")
        run_path = self_root / "runs" / run_id if run_id is not None else None
        # All actual path reads stay inside the resolver/core failure boundary.
        run_exists = run_path is not None and (run_path.exists() or run_path.is_symlink())
        return coordinate_joint_assessment(trust_resolver=lambda: trust_material(payload["case_id"]),
            measurement_resolver=(lambda: measurement_material(run_id)) if run_exists else None,
            evaluated_at=evaluated_at, expected_binding=payload.get("expected_binding"))

    def public(record):
        result = dict(record["result"])
        result.update(joint_id=record["joint_id"], case_id=record["request"]["case_id"],
            value_run_id=record["request"].get("value_run_id"), created_at=record["created_at"],
            record_sha256=record["record_sha256"], source_resolution="SERVER_REGISTRY_IDS_ONLY")
        return result

    def read_record(identity):
        if not JOINT_ID.fullmatch(identity):
            raise HTTPException(404, "JOINT_REPORT_NOT_FOUND")
        path = checked_path(output / (identity + ".json"), output)
        if not path.is_file():
            raise HTTPException(404, "JOINT_REPORT_NOT_FOUND")
        try:
            record = parse_json(_read(path, output))
            if not isinstance(record, dict):
                raise ValueError("JOINT_RECORD_OBJECT_REQUIRED")
            pinned = record.pop("record_sha256")
            if record.get("joint_id") != identity or digest(canonical(record)) != pinned:
                raise ValueError("JOINT_SAVED_RECORD_HASH_CHANGED")
            record["record_sha256"] = pinned
            current = resolve(record["request"], record["created_at"])
            if canonical(current) != canonical(record["result"]):
                raise ValueError("JOINT_SOURCE_SNAPSHOT_CHANGED")
            return record
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RegistryError) as exc:
            raise HTTPException(409, {"status": "BINDING_MISMATCH",
                "issues": [{"code": "SAVED_JOINT_OR_SOURCE_CHANGED", "path": "server_saved_record"}]}) from exc

    def run_options():
        runs = []
        for folder in sorted(checked_path(self_root / "runs", self_root).iterdir()):
            checked_path(folder, self_root)
            if not folder.is_dir() or not RUN_ID.fullmatch(folder.name):
                continue
            row = {"run_id": folder.name, "status": "INVALID_SAVED_RECORD"}
            try:
                state = load_json(checked_path(folder / "status.json", self_root))
                request = load_json(checked_path(folder / "request.json", self_root))
                if not isinstance(state, dict) or not isinstance(request, dict):
                    raise ValueError("JOINT_RUN_METADATA_OBJECT_REQUIRED")
                candidate = request.get("protocol", {}).get("inputs", {}).get("candidate", {})
                row.update(status=state.get("status", "UNKNOWN"), candidate={key: candidate.get(key)
                    for key in ("dataset_id", "version", "content_hash")})
            except (ValueError, OSError, KeyError, TypeError, AttributeError):
                row["error"] = "SERVER_SAVED_RUN_METADATA_INVALID"
            runs.append(row)
        return runs

    @app.get("/api/same-asset-joint/capabilities")
    def capabilities():
        return {"method_version": "joint_assessment-exact-joint-binding-v1", "value_runs": run_options(),
            "request_fields": ["case_id", "value_run_id", "expected_binding"],
            "request_schema": {"case_id": "registered visual case identifier",
                "value_run_id": "server saved measurement identifier or null",
                "expected_binding": "optional complete exact binding object; partial aliases rejected"},
            "limits": {"request_bytes": MAX_ACTION_BYTES},
            "statuses": ["SAME_ASSET_BOUND", "BINDING_MISMATCH", "NOT_EVALUATED"],
            "fact_status_without_independent_confirmation": "UNKNOWN",
            "message": "只合并服务器核验的同一精确资产版本；可信与任务效用并列，不相乘。"}

    @app.post("/api/same-asset-joint")
    async def create_joint(request: Request):
        payload = await bounded_json(request, MAX_ACTION_BYTES)
        if set(payload) not in ({"case_id", "value_run_id"}, {"case_id", "value_run_id", "expected_binding"}):
            raise HTTPException(422, "JOINT_REGISTRY_IDENTIFIERS_ONLY_NO_RESULTS_OR_PATHS")
        if not isinstance(payload["case_id"], str) or not CASE_ID.fullmatch(payload["case_id"]):
            raise HTTPException(422, "JOINT_CASE_ID_INVALID")
        run_id = payload["value_run_id"]
        if run_id is not None and (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)):
            raise HTTPException(422, "JOINT_VALUE_RUN_ID_INVALID")
        if "expected_binding" in payload and not isinstance(payload["expected_binding"], dict):
            raise HTTPException(422, "JOINT_EXPECTED_BINDING_FULL_OBJECT_REQUIRED")
        now = _now()
        result = await run_in_threadpool(resolve, payload, now)
        identity = "joint_" + uuid.uuid4().hex
        record = {"joint_id": identity, "created_at": now, "request": payload, "result": result}
        record["record_sha256"] = digest(canonical(record))
        destination = checked_path(output / (identity + ".json"), output)
        # An exclusive new filename never overwrites a historical decision.
        with destination.open("xb") as stream:
            stream.write(canonical(record))
        document = public(record)
        if result["status"] == "BINDING_MISMATCH":
            raise HTTPException(409, document)
        return document

    @app.get("/api/same-asset-joint")
    def list_joint():
        records = []
        for path in sorted(output.glob("joint_*.json")):
            try:
                record = read_record(path.stem)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                # A changed/invalid historical snapshot cannot hide unrelated
                # current reports. Its strict download gate remains unchanged.
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                records.append({"joint_id": path.stem, "status": "SOURCE_CHANGED_OR_INVALID",
                    "issues": detail.get("issues", [{"code": "SAVED_JOINT_OR_SOURCE_CHANGED",
                                                     "path": "server_saved_record"}])})
                continue
            document = public(record)
            records.append({key: document.get(key) for key in
                ("joint_id", "case_id", "value_run_id", "status", "created_at", "binding", "issues")})
        return {"reports": records}

    @app.get("/api/same-asset-joint/{joint_id}")
    def get_joint(joint_id: str, format: str = "json"):
        if format not in {"json", "md"}:
            raise HTTPException(422, "JOINT_REPORT_FORMAT_MUST_BE_MD_OR_JSON")
        record = read_record(joint_id)
        document = public(record)
        headers = {"Content-Disposition": f'attachment; filename="{joint_id}.{format}"',
            "Cache-Control": "no-store", "X-Joint-Result-SHA256": document["result_sha256"]}
        if format == "json":
            return JSONResponse(document, headers=headers)
        return Response(document["report_markdown"].encode("utf-8"), media_type="text/markdown", headers=headers)

    return app
