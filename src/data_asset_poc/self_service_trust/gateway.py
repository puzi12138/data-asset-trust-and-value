"""Add bounded, immutable reference audits to the unchanged Self_service_value gateway.

This module does not import the historical web stack at import time. Local
hashes detect changed evidence; they do not authenticate uploaded declarations.
"""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import duplicate_metrics, evaluate_plan, make_plan, value_semantics
from .core import frame_payload

PLAN_ID = re.compile(r"^plan_[0-9a-f]{64}$")
JOINT_ID = re.compile(r"^joint_[0-9a-f]{64}$")
MAX_JSON_BYTES = 65536
MAX_SAVED_BYTES = 64 * 1024 * 1024
JOINT_METHOD_VERSION = "self_service_trust-joint-structure-source-v2"
JOINT_V2_FIELDS = frozenset({"method_version", "structural_quality", "source_integrity"})


def require_web_environment():
    """Fail before importing a historical constr(regex=...) web dependency."""
    version = importlib.metadata.version("pydantic")
    if version.split(".")[0] != "1":
        raise ValueError("SELF_SERVICE_TRUST_WEB_REQUIRES_PINNED_PYDANTIC_1_ENVIRONMENT:" + version)


def markdown_report(title: str, document: dict) -> str:
    report = document.get("report", {})
    summary = ""
    if "structural_quality" in report and "source_integrity" in report:
        metrics = report["structural_quality"]["metrics"]
        source = report["source_integrity"]
        summary = ("## 结构质量与来源完整性\n\n"
            f"- 完整性：{metrics['completeness']}（{metrics['completeness_status']}）；"
            f"缺失单元格 {metrics['missing_cells']}/{metrics['cell_count']}。\n"
            f"- 整行唯一性：{metrics['uniqueness']}（{metrics['uniqueness_status']}）；"
            f"额外重复行 {metrics['duplicate_excess']}/{metrics['row_count']}。\n"
            f"- 格式规则：{metrics['format_validity_status']}；"
            f"一致性规则：{metrics['consistency_status']}；未声明领域规则。\n"
            f"- 本地内容哈希：{source['content_hash']['status']}；"
            f"版本绑定：{source['version']['status']}。\n"
            f"- 来源真实性：{source['origin_authenticity']['status']}；"
            f"签名：{source['signature']['status']}；"
            f"来源声明：{source['source_declaration']['status']}。\n"
            "- 结构质量、各口径重复、参考符合性与任务价值分别解释；本地匹配不证明现实真实性。\n\n")
    return (f"# {title}\n\n"
        "来源声明不等于来源核验；参考符合性不等于现实事实真实性。\n"
        "事实状态保持UNKNOWN；正式用途仍需补证。任务价值与结构重复分别报告。\n"
        "验证结果用于价值解释，测试结果仅为事后描述；不将可信分乘入效用。\n"
        "重复创建或筛选计划的多重性不包含在单次固定计划区间内。\n\n" + summary +
        "```json\n" + json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n```\n")


def create_evidence_gateway(config_file: Path | str):
    require_web_environment()
    # Deferred until the environment guard above has passed.
    from ..self_service_value import gateway as legacy

    app = legacy.create_self_service_gateway(config_file)
    app.title = "Self_service_trust local self-service reference conformance and value"
    registered_root = legacy.checked_path(app.state.self_service_root)
    workspace = registered_root.parent
    root = legacy.checked_path(workspace / "self_service_trust", workspace)
    root.mkdir(exist_ok=True)
    for name in ("plans", "joints"):
        legacy.checked_path(root / name, root).mkdir(exist_ok=True)
    app.state.self_service_trust_root = root

    def path(folder: Path, name: str) -> Path:
        return legacy.checked_path(folder / name, root)

    def immutable_write(target: Path, raw: bytes):
        legacy.checked_path(target, root)
        with target.open("xb") as stream:
            stream.write(raw)

    def write_json(target: Path, value):
        immutable_write(target, legacy.canonical(value))

    def read_bytes(target: Path) -> bytes:
        legacy.checked_path(target, root)
        if target.stat().st_size > MAX_SAVED_BYTES:
            raise HTTPException(409, "EVIDENCE_SAVED_FILE_SIZE_LIMIT")
        return legacy.shared_read_bytes(target)

    def read_json(target: Path):
        document = legacy.parse_json(read_bytes(target))
        if not isinstance(document, dict):
            raise ValueError("EVIDENCE_SAVED_JSON_OBJECT_REQUIRED")
        return document

    def dataset(identity, expected=None):
        if not isinstance(identity, str) or not legacy.DATASET_ID.fullmatch(identity):
            raise HTTPException(404, "REGISTERED_DATASET_NOT_FOUND")
        try:
            record, raw, frame = legacy.registered_input(registered_root, identity)
        except FileNotFoundError as exc:
            raise HTTPException(404, "REGISTERED_DATASET_NOT_FOUND") from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(409, "REGISTERED_DATASET_CHANGED:" + str(exc)) from exc
        if expected is not None and record["content_hash"] != expected:
            raise HTTPException(409, "REGISTERED_DATASET_EXPECTED_HASH_MISMATCH")
        frame.attrs["raw_bytes"] = raw
        return legacy.input_binding({"data": record})["data"], raw, frame

    async def body(request: Request, allowed: set[str], required: set[str]):
        raw = bytearray()
        async for block in request.stream():
            if len(raw)+len(block) > MAX_JSON_BYTES:
                raise HTTPException(413, "EVIDENCE_JSON_SIZE_LIMIT_65536")
            raw.extend(block)
        try:
            value = legacy.parse_json(bytes(raw))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise HTTPException(422, "INVALID_EVIDENCE_JSON:" + str(exc)) from exc
        if not isinstance(value, dict) or set(value)-allowed or not required <= set(value):
            raise HTTPException(422, "EVIDENCE_REQUEST_FIELDS_INVALID")
        for name in ("expected_candidate_hash", "expected_reference_hash"):
            if name in value and (not isinstance(value[name], str) or not legacy.HASH.fullmatch(value[name])):
                raise HTTPException(422, "SHA256_REQUIRED:" + name)
        return value

    def core_call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def plan_folder(identity: str):
        if not isinstance(identity, str) or not PLAN_ID.fullmatch(identity):
            raise HTTPException(404, "EVIDENCE_PLAN_NOT_FOUND")
        try:
            folder = legacy.checked_path(root / "plans" / identity, root)
        except ValueError as exc:
            raise HTTPException(409, "EVIDENCE_PLAN_PATH_CHANGED:"+str(exc)) from exc
        if not folder.is_dir():
            raise HTTPException(404, "EVIDENCE_PLAN_NOT_FOUND")
        return folder

    def load_plan(identity: str):
        folder = plan_folder(identity)
        try:
            raw = read_bytes(path(folder, "plan.json"))
            plan = legacy.parse_json(raw)
            if not isinstance(plan, dict):
                raise ValueError("PLAN_OBJECT_REQUIRED")
            receipt = read_json(path(folder, "receipt.json"))
            claimed = plan["plan_hash"]
            computed = legacy.digest(legacy.canonical({k: v for k, v in plan.items() if k != "plan_hash"}))
            if (identity != "plan_"+computed or claimed != computed or receipt.get("plan_id") != identity
                or receipt.get("plan_hash") != computed or receipt.get("plan_sha256") != legacy.digest(raw)):
                raise ValueError("PLAN_HASH_CHAIN_CHANGED")
            tables = {}
            for role in ("candidate", "reference"):
                pinned = plan["bindings"][role]
                record, registered_raw, frame = dataset(pinned["dataset_id"], pinned["content_hash"])
                snapshot = read_bytes(path(folder, role+".csv"))
                if (record != pinned or snapshot != registered_raw
                    or receipt.get(role+"_snapshot_sha256") != legacy.digest(snapshot)):
                    raise ValueError("PLAN_REGISTERED_INPUT_OR_SNAPSHOT_CHANGED:"+role)
                metadata_raw = read_bytes(path(folder, role+"_metadata.json"))
                frame_raw = read_bytes(path(folder, role+"_frame.json"))
                if (legacy.parse_json(metadata_raw) != pinned
                    or legacy.parse_json(frame_raw) != frame_payload(frame)
                    or receipt.get(role+"_metadata_sha256") != legacy.digest(metadata_raw)
                    or receipt.get(role+"_frame_sha256") != legacy.digest(frame_raw)):
                    raise ValueError("PLAN_METADATA_OR_TYPED_FRAME_CHANGED:"+role)
                tables[role] = frame
            return folder, plan, tables
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, FileNotFoundError, UnicodeError) as exc:
            raise HTTPException(409, "EVIDENCE_PLAN_CHAIN_INVALID:"+str(exc)) from exc

    def load_result(identity: str):
        folder, plan, tables = load_plan(identity)
        try:
            raw = read_bytes(path(folder, "result.json"))
            report = legacy.parse_json(raw)
            if not isinstance(report, dict):
                raise ValueError("RESULT_OBJECT_REQUIRED")
            receipt = read_json(path(folder, "result_receipt.json"))
            report_hash = legacy.digest(raw)
            if (receipt.get("plan_id") != identity or receipt.get("plan_hash") != plan["plan_hash"]
                or receipt.get("report_hash") != report_hash or report.get("plan_hash") != plan["plan_hash"]):
                raise ValueError("RESULT_HASH_CHAIN_CHANGED")
            return {"plan_id": identity, "report_hash": report_hash, "report": report}
        except FileNotFoundError as exc:
            raise HTTPException(409, "EVIDENCE_PLAN_NOT_EVALUATED_OR_INCOMPLETE") from exc
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise HTTPException(409, "EVIDENCE_RESULT_CHAIN_INVALID:"+str(exc)) from exc

    def load_run(identity: str, candidate_binding: dict):
        if not isinstance(identity, str) or not legacy.RUN_ID.fullmatch(identity):
            raise HTTPException(404, "SELF_SERVICE_RUN_NOT_FOUND")
        folder = legacy.checked_path(registered_root / "runs" / identity, registered_root)
        if not folder.is_dir():
            raise HTTPException(404, "SELF_SERVICE_RUN_NOT_FOUND")
        try:
            state = legacy.load_json(legacy.checked_path(folder/"status.json", registered_root))
            if state.get("status") != "COMPLETED":
                raise HTTPException(409, "SELF_SERVICE_RUN_NOT_COMPLETED")
            job = legacy.load_json(legacy.checked_path(folder/"request.json", registered_root))
            protocol = job["protocol"]
            request_hash = legacy.digest(legacy.canonical(protocol))
            report_raw = legacy.shared_read_bytes(legacy.checked_path(folder/"report.json", registered_root))
            report_hash = legacy.digest(report_raw)
            report = legacy.parse_json(report_raw)
            if not isinstance(job, dict) or not isinstance(state, dict) or not isinstance(report, dict):
                raise ValueError("RUN_STATE_REQUEST_REPORT_OBJECTS_REQUIRED")
            if (job.get("run_id") != identity or state.get("run_id") != identity or job.get("request_hash") != request_hash
                or state.get("request_hash") != request_hash or state.get("report_sha256") != report_hash
                or report.get("run_id") != identity or report.get("request_hash") != request_hash
                or report.get("request_protocol") != protocol or report.get("registered_inputs") != protocol["inputs"]
                or protocol["inputs"].get("candidate") != candidate_binding):
                raise ValueError("RUN_REQUEST_REPORT_CANDIDATE_BINDING_CHANGED")
            for role, pinned in protocol["inputs"].items():
                current, _, _ = dataset(pinned["dataset_id"], pinned["content_hash"])
                if current != pinned:
                    raise ValueError("RUN_REGISTERED_INPUT_METADATA_CHANGED:"+role)
            if report.get("input_binding", {}).get("registered_inputs") != protocol["inputs"]:
                raise ValueError("RUN_ENGINE_INPUT_BINDING_CHANGED")
            if report.get("task") != protocol["task"]:
                raise ValueError("RUN_ENGINE_TASK_CHANGED")
            frozen_features = None
            if report.get("status") == "EVALUATED":
                measurement = legacy.checked_path(folder/"measurement", registered_root)
                for name, hashfield in (("protocol.json", "protocol_hash"), ("validation_freeze.json", "validation_freeze_sha256")):
                    evidence_raw = legacy.shared_read_bytes(legacy.checked_path(measurement/name, registered_root))
                    if legacy.digest(evidence_raw) != report.get(hashfield):
                        raise ValueError("RUN_MEASUREMENT_FREEZE_CHANGED:"+name)
                freeze = legacy.load_json(measurement/"validation_freeze.json")
                measured_protocol = legacy.load_json(measurement/"protocol.json")
                frozen_features = measured_protocol.get("feature_columns")
                if (not isinstance(frozen_features, list) or not frozen_features
                    or any(not isinstance(c, str) for c in frozen_features) or len(set(frozen_features)) != len(frozen_features)):
                    raise ValueError("RUN_FROZEN_FEATURE_SCHEMA_INVALID")
                if (freeze.get("protocol_hash") != report.get("protocol_hash")
                    or freeze.get("validation") != report.get("validation") or freeze.get("recommendation") != report.get("recommendation")
                    or measured_protocol.get("task") != protocol["task"]
                    or measured_protocol.get("input_binding", {}).get("registered_inputs") != protocol["inputs"]):
                    raise ValueError("RUN_FROZEN_VALIDATION_OR_RECOMMENDATION_CHANGED")
                for name, descriptor in report.get("artifacts", {}).items():
                    # Artifact names are created by the engine, but saved JSON is untrusted.
                    if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
                        raise ValueError("RUN_ARTIFACT_PATH_INVALID")
                    artifact_raw = legacy.shared_read_bytes(legacy.checked_path(measurement/name, registered_root))
                    if (descriptor.get("sha256") != legacy.digest(artifact_raw)
                        or descriptor.get("bytes") != len(artifact_raw)):
                        raise ValueError("RUN_MEASUREMENT_ARTIFACT_CHANGED:"+name)
            return report, report_hash, frozen_features
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, FileNotFoundError, UnicodeError) as exc:
            raise HTTPException(409, "SELF_SERVICE_RUN_CHAIN_INVALID:"+str(exc)) from exc

    def joint_components(payload: dict):
        candidate, candidate_raw, frame = dataset(payload["candidate_id"], payload["expected_candidate_hash"])
        # Recompute from this exact verified parse/byte pair, never registration's
        # old unbound trust object. No domain structural rules are declared here.
        assessed = core_call(legacy.evaluate_dataset, frame, dataset_id=candidate["dataset_id"],
            version=candidate["version"], context={"schema_name": "generic"}, raw_bytes=candidate_raw)
        actual_hash = legacy.digest(candidate_raw)
        if (actual_hash != candidate["content_hash"] or candidate["version"] != "sha256:"+actual_hash
            or assessed["dataset_id"] != candidate["dataset_id"] or assessed["version"] != candidate["version"]):
            raise HTTPException(409, "JOINT_STRUCTURE_INPUT_BINDING_CHANGED")
        structure = {**assessed["dimensions"]["structure"], "method_version": assessed["method_version"],
            "schema_name": assessed["schema_name"], "dataset_binding": candidate, "duplicate_scope": "FULL_ROW",
            "rules_policy": "UNDECLARED_DOMAIN_RULES_NOT_VERIFIED"}
        declaration = None
        evidence = {"status": "NOT_EVALUATED", "fact_status": "UNKNOWN", "reason": "NO_REFERENCE_PLAN_SUPPLIED"}
        if payload.get("plan_id") is not None:
            _, plan, _ = load_plan(payload["plan_id"])
            if plan["bindings"]["candidate"] != candidate:
                raise HTTPException(409, "JOINT_PLAN_CANDIDATE_VERSION_MISMATCH")
            declaration = plan["bindings"]["source_declaration"]
            evidence = {"status": "EVALUATED", "plan_hash": plan["plan_hash"], **load_result(payload["plan_id"])}
        source_integrity = {"status": "LOCAL_BINDING_MATCHED_ORIGIN_UNKNOWN", "dataset_binding": candidate,
            "content_hash": {"status": "MATCHED", "algorithm": "SHA-256", "expected": payload["expected_candidate_hash"],
                "registered": candidate["content_hash"], "actual": actual_hash},
            "version": {"status": "MATCHED", "registered": candidate["version"], "assessed": assessed["version"]},
            "origin_authenticity": {"status": "UNKNOWN", "reason": "NO_INDEPENDENT_ORIGIN_AUTHENTICATION"},
            "signature": {"status": "NOT_PROVIDED"},
            "source_declaration": {"status": "UNAUTHENTICATED" if declaration is not None else "NOT_PROVIDED",
                "scope": "REFERENCE_PLAN_DECLARATION" if declaration is not None else None, "declaration": declaration},
            "meaning": "Local bytes and registered version match; origin, declarations and real-world facts are not authenticated."}
        saved, run_hash, measured_features = (None, None, None)
        if payload.get("run_id") is not None:
            saved, run_hash, measured_features = load_run(payload["run_id"], candidate)
        requested_features = payload.get("feature_columns")
        if measured_features is not None:
            if requested_features is not None and (not isinstance(requested_features, list)
                or any(not isinstance(c, str) for c in requested_features)
                or len(set(requested_features)) != len(requested_features)
                or set(requested_features) != set(measured_features)):
                raise HTTPException(422, "JOINT_FEATURE_COLUMNS_MUST_MATCH_FROZEN_MEASUREMENT_FEATURES")
            assessed_features = measured_features
            feature_scope = "FROZEN_MEASUREMENT_FEATURES"
        elif saved is not None:
            if requested_features is not None:
                raise HTTPException(409, "JOINT_RUN_HAS_NO_MEASURED_FEATURE_SCHEMA")
            assessed_features = None
            feature_scope = "NOT_EVALUATED"
        else:
            assessed_features = requested_features
            feature_scope = "DECLARED_TASK_FEATURES_NOT_MEASURED" if requested_features is not None else "NOT_EVALUATED"
        duplicates = core_call(duplicate_metrics, frame, auxiliary_id_columns=payload.get("auxiliary_id_columns", []),
            feature_columns=assessed_features)
        duplicates["task_feature_scope"] = feature_scope
        duplicates["task_features"]["scope"] = feature_scope
        semantics = core_call(value_semantics, saved)
        statistical = evidence.get("report", {}).get("statistical_decision")
        state = semantics["value_status"]
        action = ("DO_NOT_USE" if statistical == "REJECT_CONFIRMED" or state in {"NEGATIVE_VALUE_EVIDENCE", "TASK_MODEL_UNRELIABLE"}
            else "CONSIDER_RESEARCH_USE" if statistical == "ACCEPT_CONDITIONALLY" and state == "POSITIVE_VALUE_EVIDENCE"
            else "NEED_MORE_EVIDENCE")
        return {"method_version": JOINT_METHOD_VERSION, "candidate": candidate, "structural_quality": structure,
            "source_integrity": source_integrity, "duplicate_metrics": duplicates, "evidence": evidence, "value": semantics,
            "joint_decision": {"formal_decision": "NEED_MORE_EVIDENCE", "fact_status": "UNKNOWN",
                "exploration_action": action, "reason": "REFERENCE_CONFORMANCE_AND_VALIDATION_VALUE_SEPARATELY_REQUIRED",
                "changes_saved_value_recommendation": False},
            "binding": {"candidate": candidate, "plan_id": payload.get("plan_id"),
                "plan_hash": evidence.get("plan_hash"), "audit_report_hash": evidence.get("report_hash"),
                "run_id": payload.get("run_id"), "run_report_hash": run_hash},
            "trust_multiplier_applied": False, "market_price": "NOT_EVALUATED",
            "limitations": ["Uploaded source relationships and timestamps are declarations, not verification.",
                "Reference agreement cannot certify real-world factual truth.",
                "Repeated plans and outcome-based plan selection are not covered by one fixed-plan interval.",
                "Local hashes are traceability checks, not an operating-system adversary boundary."]}

    @app.post("/api/evidence/plans")
    async def create_plan(request: Request):
        payload = await body(request, {"candidate_id", "expected_candidate_hash", "reference_id", "expected_reference_hash", "config", "source_declaration"},
            {"candidate_id", "expected_candidate_hash", "reference_id", "expected_reference_hash", "config", "source_declaration"})
        candidate, candidate_raw, candidate_frame = dataset(payload["candidate_id"], payload["expected_candidate_hash"])
        reference, reference_raw, reference_frame = dataset(payload["reference_id"], payload["expected_reference_hash"])
        config = payload["config"]
        if not isinstance(config, dict) or not isinstance(config.get("rules"), list) or not 1 <= len(config["rules"]) <= 32:
            raise HTTPException(422, "AUDIT_REQUIRES_1_TO_32_RULES")
        # N<=10000 and R<=32 bound even a complete census at 320000 comparisons.
        plan = core_call(make_plan, candidate_frame, reference_frame, config,
            {"candidate": candidate, "reference": reference, "source_declaration": payload["source_declaration"]})
        if plan["sample_size"]*len(config["rules"]) > 320000:
            raise HTTPException(422, "AUDIT_COMPARISON_CELL_LIMIT_320000")
        identity = "plan_"+plan["plan_hash"]
        lock = legacy.acquire_lock(root/"plans.lock")
        if lock is None:
            raise HTTPException(409, "EVIDENCE_PLAN_STORE_BUSY")
        try:
            folder = legacy.checked_path(root/"plans"/identity, root)
            if folder.exists():
                _, existing, _ = load_plan(identity)
                return {"plan_id": identity, "plan_hash": existing["plan_hash"], "plan": existing}
            folder.mkdir()
            immutable_write(path(folder, "candidate.csv"), candidate_raw)
            immutable_write(path(folder, "reference.csv"), reference_raw)
            extras = {}
            for role, metadata, frame in (("candidate", candidate, candidate_frame), ("reference", reference, reference_frame)):
                for suffix, value in (("metadata", metadata), ("frame", frame_payload(frame))):
                    extra_raw = legacy.canonical(value)
                    immutable_write(path(folder, role+"_"+suffix+".json"), extra_raw)
                    extras[role+"_"+suffix+"_sha256"] = legacy.digest(extra_raw)
            plan_raw = legacy.canonical(plan)
            immutable_write(path(folder, "plan.json"), plan_raw)
            write_json(path(folder, "receipt.json"), {"plan_id": identity, "plan_hash": plan["plan_hash"],
                "plan_sha256": legacy.digest(plan_raw), "candidate_snapshot_sha256": legacy.digest(candidate_raw),
                "reference_snapshot_sha256": legacy.digest(reference_raw), **extras})
            load_plan(identity)
        finally:
            legacy.release_lock(lock)
        return {"plan_id": identity, "plan_hash": plan["plan_hash"], "plan": plan}

    @app.post("/api/evidence/plans/{plan_id}/evaluate")
    def evaluate(plan_id: str):
        folder, plan, tables = load_plan(plan_id)
        lock = legacy.acquire_lock(root/"evaluation.lock")
        if lock is None:
            raise HTTPException(409, "EVIDENCE_EVALUATOR_BUSY")
        try:
            if path(folder, "result.json").exists() or path(folder, "result_receipt.json").exists():
                return load_result(plan_id)
            report = core_call(evaluate_plan, tables["candidate"], tables["reference"], plan)
            load_plan(plan_id)  # Recheck registry and snapshots after comparison.
            raw = legacy.canonical(report)
            if len(raw) > MAX_SAVED_BYTES:
                raise HTTPException(413, "EVIDENCE_RESULT_SIZE_LIMIT_64_MIB_NO_RESULT_PUBLISHED")
            immutable_write(path(folder, "result.json"), raw)
            write_json(path(folder, "result_receipt.json"), {"plan_id": plan_id, "plan_hash": plan["plan_hash"],
                "report_hash": legacy.digest(raw)})
            return load_result(plan_id)
        finally:
            legacy.release_lock(lock)

    @app.get("/api/evidence/plans/{plan_id}/result")
    def result(plan_id: str):
        return load_result(plan_id)

    def download(identity: str, document: dict, title: str, format: str):
        if format == "json":
            return JSONResponse(document, headers={"Content-Disposition": f'attachment; filename="{identity}.json"'})
        if format == "md":
            return Response(markdown_report(title, document), media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{identity}.md"'})
        raise HTTPException(422, "REPORT_FORMAT_MUST_BE_MD_OR_JSON")

    @app.get("/api/evidence/plans/{plan_id}/report")
    def audit_download(plan_id: str, format: str = "json"):
        return download(plan_id, load_result(plan_id), "固定参考符合性审计报告", format)

    @app.post("/api/evidence/joint")
    async def create_joint(request: Request):
        payload = await body(request, {"candidate_id", "expected_candidate_hash", "plan_id", "run_id", "auxiliary_id_columns", "feature_columns"},
            {"candidate_id", "expected_candidate_hash"})
        report = joint_components(payload)
        identity = "joint_"+legacy.digest(legacy.canonical(report))
        lock = legacy.acquire_lock(root/"joint.lock")
        if lock is None:
            raise HTTPException(409, "EVIDENCE_JOINT_STORE_BUSY")
        try:
            folder = legacy.checked_path(root/"joints"/identity, root)
            if not folder.exists():
                folder.mkdir()
                write_json(path(folder, "request.json"), payload)
                write_json(path(folder, "report.json"), report)
            else:
                stored = read_json(path(folder, "report.json"))
                if stored != report:
                    raise HTTPException(409, "JOINT_REPORT_CHANGED")
        finally:
            legacy.release_lock(lock)
        return {"joint_id": identity, "report": report}

    @app.get("/api/evidence/joint/{joint_id}/report")
    def joint_download(joint_id: str, format: str = "json"):
        if not JOINT_ID.fullmatch(joint_id):
            raise HTTPException(404, "EVIDENCE_JOINT_NOT_FOUND")
        folder = legacy.checked_path(root/"joints"/joint_id, root)
        if not folder.is_dir():
            raise HTTPException(404, "EVIDENCE_JOINT_NOT_FOUND")
        try:
            report = read_json(path(folder, "report.json"))
            payload = read_json(path(folder, "request.json"))
            current = joint_components(payload)
            # Old reports retain their original bytes and content ID. Validate
            # their full original projection against today's verified inputs;
            # only new POSTs publish the additional deterministic v2 fields.
            if "method_version" not in report and set(report) == set(current)-JOINT_V2_FIELDS:
                current = {key: value for key, value in current.items() if key not in JOINT_V2_FIELDS}
            if report != current or joint_id != "joint_"+legacy.digest(legacy.canonical(report)):
                raise HTTPException(409, "JOINT_REPORT_OR_BINDING_CHANGED")
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            raise HTTPException(409, "JOINT_REPORT_CHAIN_INVALID:"+str(exc)) from exc
        return download(joint_id, {"joint_id": joint_id, "report": report}, "可信与任务价值联合报告", format)

    return app
