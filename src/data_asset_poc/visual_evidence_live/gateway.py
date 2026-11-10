"""Bounded local HTTP entry points for newly registered visual evidence.

The local request middleware is inherited. Browser input cannot configure a
provider, a trusted validator, a local path, or a network fetch. A provider
failure never becomes a replay.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from ..self_service_trust.gateway import create_evidence_gateway


MAX_REGISTRATION_BYTES = 30 * 1024 * 1024
MAX_ACTION_BYTES = 65536
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REGISTER_FIELDS = frozenset({
    "asset_id", "asset_version", "asset_sha256", "dataset_csv_base64",
    "task_id", "entity_key", "entity_value", "field", "observation_target",
    "observation_rule", "capture_time", "source", "authorization", "evidence"})
SOURCE_FIELDS = frozenset({"description", "url", "license", "independence_declaration"})
AUTHORIZATION_FIELDS = frozenset({"local_processing", "external_model_upload"})
EVIDENCE_FIELDS = frozenset({"evidence_id", "filename", "sha256", "data_base64"})
LIVE_FIELDS = frozenset({"mode", "asset_sha256", "asset_version", "evidence_id", "external_upload_authorized"})


def strict_json(raw: bytes) -> dict:
    """Reject nested duplicate keys and nonfinite constants before any write."""
    def unique_pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                raise ValueError("DUPLICATE_JSON_KEY")
            value[key] = child
        return value

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("NONFINITE_JSON")
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs, parse_float=finite_float,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))
    if not isinstance(value, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return value


def exact_fields(value: object, expected: frozenset | set, code: str):
    if not isinstance(value, dict) or set(value) != expected:
        raise HTTPException(422, code)


def _registration_fields(value: dict):
    if frozenset(value) not in {REGISTER_FIELDS, REGISTER_FIELDS | {"allowed_outcomes"}}:
        raise HTTPException(422, "VISUAL_CASE_REGISTRATION_FIELDS_INVALID")
    exact_fields(value["source"], SOURCE_FIELDS, "VISUAL_SOURCE_FIELDS_INVALID")
    exact_fields(value["authorization"], AUTHORIZATION_FIELDS, "VISUAL_AUTHORIZATION_FIELDS_INVALID")
    if (value["authorization"]["local_processing"] is not True
            or type(value["authorization"]["external_model_upload"]) is not bool):
        raise HTTPException(422, "VISUAL_EXPLICIT_LOCAL_AUTHORIZATION_REQUIRED")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        raise HTTPException(422, "VISUAL_EVIDENCE_COUNT_MUST_BE_1_TO_4")
    for item in evidence:
        exact_fields(item, EVIDENCE_FIELDS, "VISUAL_EVIDENCE_FIELDS_INVALID")


def _execution_fields(value: dict):
    mode = value.get("mode")
    if not isinstance(mode, str):
        raise HTTPException(422, "VISUAL_EXECUTION_MODE_MUST_BE_LIVE_OR_REPLAY")
    fields = LIVE_FIELDS | {"run_id"} if mode == "REPLAY" else LIVE_FIELDS
    exact_fields(value, fields, "VISUAL_EXECUTION_FIELDS_INVALID")
    if mode not in {"LIVE", "REPLAY"}:
        raise HTTPException(422, "VISUAL_EXECUTION_MODE_MUST_BE_LIVE_OR_REPLAY")
    if (not isinstance(value["asset_sha256"], str)
            or not SHA256.fullmatch(value["asset_sha256"])):
        raise HTTPException(422, "VISUAL_ASSET_SHA256_REQUIRED")
    if (not isinstance(value["asset_version"], str) or not value["asset_version"]
            or len(value["asset_version"]) > 256):
        raise HTTPException(422, "VISUAL_ASSET_VERSION_REQUIRED")
    if type(value["external_upload_authorized"]) is not bool:
        raise HTTPException(422, "VISUAL_EXPLICIT_UPLOAD_AUTHORIZATION_REQUIRED")
    for key in ("evidence_id", "run_id"):
        if key in value and (not isinstance(value[key], str) or not value[key] or len(value[key]) > 256):
            raise HTTPException(422, "VISUAL_EXECUTION_IDENTIFIER_INVALID")


async def bounded_json(request: Request, limit: int) -> dict:
    """Check declared size and streamed size independently, including chunks."""
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or (lengths and (len(lengths[0]) > 16 or not re.fullmatch(r"[0-9]+", lengths[0]))):
        raise HTTPException(400, "VISUAL_CONTENT_LENGTH_INVALID")
    expected = int(lengths[0]) if lengths else None
    if expected is not None and expected > limit:
        raise HTTPException(413, "VISUAL_REQUEST_SIZE_LIMIT")
    if lengths and request.headers.get("transfer-encoding") is not None:
        raise HTTPException(400, "VISUAL_AMBIGUOUS_BODY_FRAMING")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise HTTPException(415, "VISUAL_CONTENT_ENCODING_UNSUPPORTED")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "VISUAL_APPLICATION_JSON_REQUIRED")
    raw = bytearray()
    try:
        async for block in request.stream():
            if len(raw) + len(block) > limit:
                raise HTTPException(413, "VISUAL_REQUEST_SIZE_LIMIT")
            raw.extend(block)
    except ClientDisconnect as exc:
        raise HTTPException(400, "VISUAL_BODY_INTERRUPTED") from exc
    if expected is not None and len(raw) != expected:
        raise HTTPException(400, "VISUAL_CONTENT_LENGTH_MISMATCH")
    try:
        return strict_json(bytes(raw))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(422, "INVALID_VISUAL_JSON:" + str(exc)) from exc


def chinese_report(document: dict) -> str:
    """Export the actual registry document without inventing a fact judgment."""
    rendered = document.get("report_markdown")
    if isinstance(rendered, str) and rendered.strip():
        return rendered
    return ("# 数据集视觉证据与条件可信评估报告\n\n"
        "来源声明不等于来源认证；本地字节完整性不等于事实真实。\n"
        "AI视觉审阅仅支持指定时点、实体和窄范围属性；事实状态仍需独立依据。\n"
        "同一影像重复审阅不增加独立物理样本；一致率不是事实准确率。\n"
        "任务价值独立报告，缺少合法任务条件时保持NOT_EVALUATED。\n\n"
        "## 版本、原始证据、执行收据及完整结果\n\n```json\n"
        + json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n```\n")


def create_live_gateway(config_file: Path | str, project_root: Path | str | None = None,
        registry_root: Path | str | None = None, adapter=None):
    """Extend the local evidence gateway with server-side dependency injection."""
    from .registry import Registry, RegistryError
    from .adapters import configured_adapter

    app = create_evidence_gateway(config_file)
    root = Path(registry_root) if registry_root is not None else Path(config_file).resolve().parent / "visual_cases"
    app.state.visual_case_registry = Registry(root)
    app.state.visual_case_adapter = adapter if adapter is not None else configured_adapter()
    app.title = "Local version-bound dataset trust, task value and visual evidence"

    def registry():
        return app.state.visual_case_registry

    def fail(exc: Exception, default: int = 422):
        code = getattr(exc, "code", None) or str(exc)
        if code in {"CASE_NOT_FOUND", "EVIDENCE_NOT_FOUND", "RUN_NOT_FOUND"}:
            status = 404
        elif any(token in code for token in ("MISMATCH", "CHANGED", "TAMPER", "CONFLICT", "EXISTS",
                "INVALID_SAVED", "INTEGRITY", "CORRUPT", "BINDING", "AUTHENTICATION", "ALREADY_BOUND")):
            status = 409
        else:
            status = default
        raise HTTPException(status, "VISUAL_EVIDENCE_INVALID:" + code) from exc

    def call(method, *args):
        try:
            return method(*args)
        except RegistryError as exc:
            fail(exc)
        except (FileNotFoundError, OSError, KeyError, TypeError) as exc:
            # Do not disclose a local file path or raw adapter exception body.
            raise HTTPException(409, "VISUAL_SAVED_EVIDENCE_UNAVAILABLE_OR_INVALID") from exc

    def bound_case(case_id, asset_sha256=None, asset_version=None):
        case = call(registry().get_case, case_id)
        asset = case.get("asset", case)
        if asset_sha256 is not None and asset.get("asset_sha256") != asset_sha256:
            raise HTTPException(409, "VISUAL_ASSET_SHA256_MISMATCH")
        if asset_version is not None and asset.get("asset_version") != asset_version:
            raise HTTPException(409, "VISUAL_ASSET_VERSION_MISMATCH")
        return case

    @app.get("/api/visual-cases/capabilities")
    def capabilities():
        selected = app.state.visual_case_adapter
        try:
            availability = selected.availability() if selected is not None else {
                "available": False, "reason": "LIVE_UNAVAILABLE", "provider": None, "model": None}
        except Exception:
            availability = {"available": False, "reason": "ADAPTER_CONFIGURATION_INVALID",
                "provider": None, "model": None}
        if not isinstance(availability, dict):
            availability = {"available": False, "reason": "ADAPTER_CONFIGURATION_INVALID",
                "provider": None, "model": None}
        available = availability.get("available") is True
        return {"live_available": available, "status": "AVAILABLE" if available else "UNAVAILABLE",
            "reason": availability.get("reason") if available else "LIVE_UNAVAILABLE",
            "adapter": availability, "limits": {
                "request_bytes": MAX_REGISTRATION_BYTES, "csv_bytes": 2 * 1024 * 1024,
                "csv_columns": 200, "image_bytes": 5 * 1024 * 1024,
                "image_pixels": 20000000, "evidence_per_case": 4},
            "message": ("已配置模型适配器；可用配置不代表本次已执行审阅。" if available else
                "LIVE_UNAVAILABLE：当前没有可调用模型；未执行新审阅，不会自动重放旧结果。"),
            "execution_modes": ["LIVE", "REPLAY"], "fact_status_without_independent_confirmation": "UNKNOWN"}

    @app.get("/api/visual-cases")
    def list_cases():
        return {"cases": call(registry().list_cases)}

    @app.post("/api/visual-cases")
    async def register_case(request: Request):
        payload = await bounded_json(request, MAX_REGISTRATION_BYTES)
        _registration_fields(payload)
        return await run_in_threadpool(call, registry().register_case, payload)

    @app.get("/api/visual-cases/{case_id}")
    def get_case(case_id: str, asset_sha256: str | None = None, asset_version: str | None = None):
        return bound_case(case_id, asset_sha256, asset_version)

    @app.get("/api/visual-cases/{case_id}/evidence/{evidence_id}")
    def evidence(case_id: str, evidence_id: str,
            asset_sha256: str | None = None, asset_version: str | None = None):
        bound_case(case_id, asset_sha256, asset_version)
        raw, media_type = call(registry().evidence_bytes, case_id, evidence_id)
        import hashlib
        actual = hashlib.sha256(raw).hexdigest()
        return Response(raw, media_type=media_type, headers={
            "ETag": '"' + actual + '"', "X-Evidence-SHA256": actual,
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/visual-cases/{case_id}/validate")
    async def validate(case_id: str, request: Request):
        payload = await bounded_json(request, MAX_ACTION_BYTES)
        exact_fields(payload, {"evidence_id"}, "VISUAL_VALIDATION_FIELDS_INVALID")
        if not isinstance(payload["evidence_id"], str) or not payload["evidence_id"]:
            raise HTTPException(422, "VISUAL_EVIDENCE_ID_REQUIRED")
        return await run_in_threadpool(call, registry().validate_evidence, case_id, payload["evidence_id"])

    @app.post("/api/visual-cases/{case_id}/execute")
    async def execute(case_id: str, request: Request):
        payload = await bounded_json(request, MAX_ACTION_BYTES)
        _execution_fields(payload)
        bound_case(case_id, payload["asset_sha256"], payload["asset_version"])
        # The full request is checked by Registry even for REPLAY, including the
        # registered evidence and asset binding. An uploaded run is never used.
        result = await run_in_threadpool(call, registry().execute, case_id,
            payload, app.state.visual_case_adapter)
        status = result.get("status")
        if status == "UNAVAILABLE":
            http_status, message = 503, "当前没有可用模型；未执行新审阅，也未回退为历史重放。"
        elif status == "FAILED":
            timeout = result.get("error_type") in {"TIMEOUT", "MODEL_TIMEOUT", "AdapterTimeout"}
            http_status = 504 if timeout else 502
            message = "实际审阅超时；失败已留档，未回退为历史重放。" if timeout else "实际审阅失败；失败已留档，未回退为历史重放。"
        else:
            return result
        detail = {key: result.get(key) for key in ("status", "execution_mode", "model_invoked", "fallback_performed", "reason", "error_type")}
        detail.update({"execution": result, "message": message})
        raise HTTPException(http_status, detail)

    @app.get("/api/visual-cases/{case_id}/report")
    def report(case_id: str, format: str = "md", asset_sha256: str | None = None,
            asset_version: str | None = None):
        if format not in {"md", "json"}:
            raise HTTPException(422, "VISUAL_REPORT_FORMAT_MUST_BE_MD_OR_JSON")
        case = bound_case(case_id, asset_sha256, asset_version)
        document = call(registry().report, case_id)
        asset = case.get("asset", case)
        headers = {"Content-Disposition": 'attachment; filename="dataset_visual_evidence_report.' + format + '"',
            "Cache-Control": "no-store", "X-Asset-SHA256": asset.get("asset_sha256", "")}
        if format == "json":
            return JSONResponse(document, headers=headers)
        return Response(chinese_report(document).encode("utf-8"), media_type="text/markdown", headers=headers)

    return app
