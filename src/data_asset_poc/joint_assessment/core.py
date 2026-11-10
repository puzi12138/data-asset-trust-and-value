"""Coordinate verified local artifacts without merging by a display name.

Resolvers are a server trust boundary, not authentication tokens supplied by a
browser. The gateway must resolve registry IDs, verify its local authentication
receipts, and read confined paths before constructing these materials. This
module then verifies original bytes and the measurement's artifact/freeze
chain. It cannot authenticate an arbitrary caller, a compromised local server,
or a reference's factual accuracy. All inputs can be synthetic in unit tests;
such tests do not become research measurements.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Callable, Mapping

METHOD_VERSION = "joint_assessment-exact-joint-binding-v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_REQUIRED_ARTIFACTS = {
    "protocol.json", "fit_records.json", "validation_freeze.json",
    "test_started.json", "split_membership.csv", "predictions.csv",
    "metrics.csv", "bootstrap_validation.json", "bootstrap_test.json",
}
_ASSET_KEYS = {"asset_id", "asset_version", "asset_sha256"}


def canonical(value) -> bytes:
    """Canonical JSON for semantic bindings; original file hashes stay separate."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class TrustMaterial:
    """Bytes resolved by a trusted server registry, never an HTTP body.

    VISUAL_CASE uses the actual registry case/protocol/evidence. Other kinds use
    a server-produced envelope containing asset, assessment,
    audit_protocol_sha256 and evidence[{evidence_id,sha256}]. Evidence can be
    empty for structural-only assessment. The envelope is not a claim of source
    identity, evidence independence or factual truth.
    """
    kind: str
    asset: Mapping
    asset_bytes: bytes
    report_bytes: bytes
    protocol_bytes: bytes
    evidence_bytes: Mapping[str, bytes]


@dataclass(frozen=True)
class MeasurementMaterial:
    """Completed Self_service_value run plus actual registered CSV and artifact bytes.

    artifacts contains every report.artifacts item and the engine's separate
    report.json. report_bytes is the worker's top-level report.json. Status and
    request are the server's run files, not client declarations.
    """
    report_bytes: bytes
    status_bytes: bytes
    request_bytes: bytes
    artifacts: Mapping[str, bytes]
    input_bytes: Mapping[str, bytes]


class _Issue(ValueError):
    def __init__(self, code: str, path: str, *, not_evaluated=False):
        self.code, self.path, self.not_evaluated = code, path, not_evaluated
        super().__init__(code)


def _require(condition, code, path):
    if not condition:
        raise _Issue(code, path)


def _object(value, path):
    _require(isinstance(value, dict), "OBJECT_REQUIRED", path)
    return value


def _text(value, path):
    _require(isinstance(value, str) and bool(value.strip()), "NONEMPTY_TEXT_REQUIRED", path)
    return value


def _sha(value, path):
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None,
             "SHA256_REQUIRED", path)
    return value


def _raw(value, path):
    _require(isinstance(value, bytes), "ORIGINAL_BYTES_REQUIRED", path)
    return value


def _json(raw, path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _Issue("DUPLICATE_JSON_KEY", path)
            result[key] = value
        return result
    try:
        value = json.loads(_raw(raw, path).decode("utf-8-sig"),
                           object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical(value)
    except _Issue:
        raise
    except (UnicodeError, ValueError, TypeError, OverflowError) as exc:
        raise _Issue("STRICT_JSON_REQUIRED", path) from exc
    return _object(value, path)


def _time(value, path):
    try:
        parsed = datetime.fromisoformat(_text(value, path).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (ValueError, OverflowError) as exc:
        raise _Issue("TIMEZONE_TIMESTAMP_REQUIRED", path) from exc


def _equal(left, right, code, path):
    _require(canonical(left) == canonical(right), code, path)


def _asset(value, path):
    value = dict(value) if isinstance(value, Mapping) else value
    _object(value, path)
    _require(set(value) == _ASSET_KEYS, "EXACT_ASSET_FIELDS_REQUIRED", path)
    _text(value["asset_id"], path + ".asset_id")
    _text(value["asset_version"], path + ".asset_version")
    _sha(value["asset_sha256"], path + ".asset_sha256")
    return copy.deepcopy(value)


def _trust(material, evaluated_at):
    _require(isinstance(material, TrustMaterial), "SERVER_TRUST_MATERIAL_REQUIRED", "trust")
    _require(material.kind in {"VISUAL_CASE", "REFERENCE_AUDIT", "STRUCTURAL_ASSESSMENT"},
             "TRUST_KIND_INVALID", "trust.kind")
    asset = _asset(material.asset, "trust.asset")
    _require(digest(_raw(material.asset_bytes, "trust.asset_bytes")) == asset["asset_sha256"],
             "ASSET_BYTES_HASH_MISMATCH", "trust.asset_sha256")
    report = _json(material.report_bytes, "trust.report")
    protocol = _json(material.protocol_bytes, "trust.protocol")
    _equal(report.get("asset"), asset, "TRUST_REPORT_ASSET_MISMATCH", "trust.report.asset")
    protocol_hash = digest(canonical(protocol))
    if material.kind == "VISUAL_CASE":
        _equal(report.get("protocol"), protocol, "AUDIT_PROTOCOL_MISMATCH", "trust.protocol")
        # Visual_evidence_live freezes the whole asset/protocol/evidence declaration, not
        # just the text of the observation rule.
        protocol_hash = digest(canonical({"asset": asset, "protocol": protocol,
                                          "evidence": report.get("evidence")}))
        _require(report.get("protocol_sha256") == protocol_hash,
                 "AUDIT_PROTOCOL_HASH_MISMATCH", "trust.protocol_sha256")
    else:
        _require(report.get("audit_protocol_sha256") == protocol_hash,
                 "AUDIT_PROTOCOL_HASH_MISMATCH", "trust.audit_protocol_sha256")
    if "created_at" in report:
        _require(_time(report["created_at"], "trust.created_at") <= evaluated_at,
                 "TRUST_TIMESTAMP_AFTER_EVALUATION", "trust.created_at")
    evidence = report.get("evidence", [])
    _require(isinstance(evidence, list), "EVIDENCE_MANIFEST_REQUIRED", "trust.evidence")
    if material.kind == "VISUAL_CASE":
        _require(1 <= len(evidence) <= 4, "VISUAL_EVIDENCE_REQUIRED", "trust.evidence")
    _require(isinstance(material.evidence_bytes, Mapping), "EVIDENCE_BYTES_REQUIRED", "trust.evidence_bytes")
    manifest, seen = [], set()
    for index, item in enumerate(evidence):
        item = _object(item, f"trust.evidence[{index}]")
        identity = _text(item.get("evidence_id"), f"trust.evidence[{index}].evidence_id")
        _require(identity not in seen, "DUPLICATE_EVIDENCE_ID", "trust.evidence")
        seen.add(identity)
        expected = _sha(item.get("sha256"), f"trust.evidence[{index}].sha256")
        _require(identity in material.evidence_bytes, "EVIDENCE_BYTES_MISSING", identity)
        raw = _raw(material.evidence_bytes[identity], "trust.evidence_bytes." + identity)
        _require(digest(raw) == expected, "EVIDENCE_HASH_MISMATCH", identity)
        manifest.append({"evidence_id": identity, "sha256": expected, "bytes": len(raw)})
    _require(set(material.evidence_bytes) == seen, "EVIDENCE_SET_MISMATCH", "trust.evidence_bytes")
    if material.kind == "VISUAL_CASE":
        for index, review in enumerate(report.get("reviews", [])):
            _object(review, "trust.reviews")
            _equal(review.get("asset"), asset, "REVIEW_ASSET_MISMATCH", f"trust.reviews[{index}]")
            _require(review.get("protocol_sha256") == protocol_hash,
                     "REVIEW_PROTOCOL_MISMATCH", f"trust.reviews[{index}]")
            eid = review.get("evidence_id")
            _require(eid in seen and review.get("evidence_sha256") == digest(material.evidence_bytes[eid]),
                     "REVIEW_EVIDENCE_MISMATCH", f"trust.reviews[{index}]")
            _require(review.get("outcome") in {"MATCH", "MISMATCH", "INCOMPARABLE", "DISPUTED"},
                     "FOUR_STATE_REVIEW_REQUIRED", f"trust.reviews[{index}]")
            _require(review.get("result_sha256") == digest(canonical({k: v for k, v in review.items() if k != "result_sha256"})),
                     "REVIEW_RESULT_HASH_MISMATCH", f"trust.reviews[{index}]")
        executions = report.get("executions", [])
        _require(isinstance(executions, list), "EXECUTION_LIST_REQUIRED", "trust.executions")
        for index, execution in enumerate(executions):
            _object(execution, "trust.executions")
            _equal(execution.get("asset"), asset, "EXECUTION_ASSET_MISMATCH", f"trust.executions[{index}]")
            _require(execution.get("protocol_sha256") == protocol_hash,
                     "EXECUTION_PROTOCOL_MISMATCH", f"trust.executions[{index}]")
            _require(execution.get("result_sha256") == digest(canonical({k: v for k, v in execution.items() if k != "result_sha256"})),
                     "EXECUTION_RESULT_HASH_MISMATCH", f"trust.executions[{index}]")
        _equal([e["review"] for e in executions if e.get("review")], report.get("reviews", []),
               "EXECUTION_REVIEW_SET_MISMATCH", "trust.reviews")
    manifest.sort(key=lambda item: item["evidence_id"])
    binding = {"asset": asset, "trust_kind": material.kind,
        "audit_task_id": protocol.get("task_id"),
        "audit_protocol_sha256": protocol_hash,
        "audit_protocol_bytes_sha256": digest(material.protocol_bytes),
        "trust_report_sha256": digest(material.report_bytes),
        "evidence_version": "sha256:" + digest(canonical(manifest)),
        "evidence_manifest": manifest}
    assessment = copy.deepcopy(report.get("assessment", {}))
    if isinstance(assessment, dict):
        assessment.pop("task_value", None)  # The verified joint result supplies this independently.
        # A nested self declaration cannot contradict the joint module's
        # explicit scope. The exact source report remains hash-addressable.
        for name in ("fact_status", "content_authenticity_status"):
            if name in assessment:
                assessment[name] = "UNKNOWN"
        if "factual_truth_verified" in assessment:
            assessment["factual_truth_verified"] = False
    # Binding and local integrity do not authenticate sources or factual truth.
    summary = {"kind": material.kind, "assessment": assessment,
        "fact_status": "UNKNOWN", "source_authentication": "NOT_ESTABLISHED_BY_JOINT_BINDING",
        "coordination_state": report.get("coordination_state"),
        "evidence_count": len(manifest), "review_count": len(report.get("reviews", [])),
        "additional_physical_observations_from_joint_binding": 0}
    return binding, summary


def _measurement(material, asset, evaluated_at):
    _require(isinstance(material, MeasurementMaterial), "SERVER_MEASUREMENT_MATERIAL_REQUIRED", "value")
    status = _json(material.status_bytes, "value.status")
    if status.get("status") != "COMPLETED":
        raise _Issue("MEASUREMENT_NOT_COMPLETED", "value.status", not_evaluated=True)
    report = _json(material.report_bytes, "value.report")
    request = _json(material.request_bytes, "value.request")
    _require(status.get("report_sha256") == digest(material.report_bytes),
             "VALUE_REPORT_HASH_MISMATCH", "value.status.report_sha256")
    if report.get("status") == "NOT_EVALUATED":
        raise _Issue("VALUE_REPORT_NOT_EVALUATED", "value.report.status", not_evaluated=True)
    _require(report.get("status") == "EVALUATED", "VALUE_REPORT_NOT_EVALUATED", "value.report.status")
    run_id = _text(report.get("run_id"), "value.run_id")
    _require(status.get("run_id") == request.get("run_id") == run_id,
             "RUN_ID_MISMATCH", "value.run_id")
    started = _time(request.get("created_at"), "value.request.created_at")
    finished = _time(status.get("finished_at"), "value.status.finished_at")
    _require(started <= finished <= evaluated_at, "EVALUATION_TIME_ORDER_INVALID", "value.evaluation_time")
    job_protocol = _object(request.get("protocol"), "value.request.protocol")
    request_hash = digest(canonical(job_protocol))
    _require(request.get("request_hash") == report.get("request_hash") == status.get("request_hash") == request_hash,
             "REQUEST_PROTOCOL_HASH_MISMATCH", "value.request_hash")
    _equal(report.get("request_protocol"), job_protocol, "REQUEST_PROTOCOL_MISMATCH", "value.request_protocol")
    _require(status.get("input_hashes_verified_before_and_after") is True,
             "WORKER_INPUT_VERIFICATION_REQUIRED", "value.status")
    records = _object(job_protocol.get("inputs"), "value.inputs")
    _require("candidate" in records, "CANDIDATE_INPUT_REQUIRED", "value.inputs")
    _equal(report.get("registered_inputs"), records, "REGISTERED_INPUTS_MISMATCH", "value.registered_inputs")
    _require(isinstance(material.input_bytes, Mapping) and set(material.input_bytes) == set(records),
             "INPUT_BYTES_SET_MISMATCH", "value.input_bytes")
    for role, record in records.items():
        record = _object(record, "value.inputs." + role)
        content = _sha(record.get("content_hash"), "value.inputs." + role + ".content_hash")
        _require(digest(_raw(material.input_bytes[role], "value.input_bytes." + role)) == content,
                 "INPUT_BYTES_HASH_MISMATCH", "value.inputs." + role)
        _require(record.get("dataset_id") == "ds_" + content and record.get("version") == "sha256:" + content,
                 "REGISTERED_INPUT_VERSION_MISMATCH", "value.inputs." + role)
    candidate = records["candidate"]
    measured_asset = {"asset_id": candidate["dataset_id"], "asset_version": candidate["version"],
                      "asset_sha256": candidate["content_hash"]}
    _equal(measured_asset, asset, "EXACT_ASSET_BINDING_MISMATCH", "value.candidate")
    _require(isinstance(material.artifacts, Mapping), "ARTIFACT_BYTES_REQUIRED", "value.artifacts")
    manifest = _object(report.get("artifacts"), "value.artifacts")
    _require(_REQUIRED_ARTIFACTS <= set(manifest), "REQUIRED_ARTIFACT_MISSING", "value.artifacts")
    _require(set(material.artifacts) == set(manifest) | {"report.json"},
             "ARTIFACT_BYTES_SET_MISMATCH", "value.artifacts")
    for name, expected in manifest.items():
        _require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", name) is not None,
                 "ARTIFACT_NAME_INVALID", "value.artifacts")
        expected = _object(expected, "value.artifacts." + name)
        raw = _raw(material.artifacts[name], "value.artifacts." + name)
        _require(expected.get("sha256") == digest(raw) and type(expected.get("bytes")) is int
                 and expected["bytes"] == len(raw), "ARTIFACT_HASH_OR_SIZE_MISMATCH", "value.artifacts." + name)
    engine = _json(material.artifacts["report.json"], "value.engine_report")
    for field in ("status", "engine_version", "task", "input_binding", "protocol_hash",
                  "validation_freeze_sha256", "splits", "fit_count", "validation", "test",
                  "isolation_assessment", "marginal_utility", "recommendation", "artifacts"):
        _require(field in report and field in engine, "ENGINE_REPORT_FIELD_MISSING", "value." + field)
        _equal(report[field], engine[field], "ENGINE_WORKER_REPORT_MISMATCH", "value." + field)
    protocol = _json(material.artifacts["protocol.json"], "value.protocol")
    protocol_hash = digest(material.artifacts["protocol.json"])
    _require(report["protocol_hash"] == protocol_hash, "VALUE_PROTOCOL_HASH_MISMATCH", "value.protocol_hash")
    for field in ("engine_version", "task", "input_binding", "splits", "isolation_assessment"):
        _equal(protocol.get(field), report[field], "VALUE_PROTOCOL_FIELD_MISMATCH", "value.protocol." + field)
    task = _object(report["task"], "value.task")
    _equal(job_protocol.get("task"), task, "REQUEST_TASK_MISMATCH", "value.task")
    target = _text(task.get("target"), "value.task.target")
    kind = task.get("task_type")
    _require(kind in {"classification", "regression"}, "SUPPORTED_TASK_TYPE_REQUIRED", "value.task.task_type")
    _equal(report["input_binding"].get("registered_inputs"), records,
           "PROTOCOL_REGISTERED_INPUTS_MISMATCH", "value.input_binding.registered_inputs")
    _require(target in [item.get("name") for item in candidate.get("columns", [])],
             "TARGET_NOT_IN_CANDIDATE_SCHEMA", "value.task.target")
    splits = _object(report["splits"], "value.splits")
    _require(set(splits) == {"D0", "Di", "validation", "test"}, "FOUR_SPLITS_REQUIRED", "value.splits")
    for role, split in splits.items():
        _object(split, "value.splits." + role)
        _sha(split.get("content_hash"), "value.splits." + role + ".content_hash")
        _text(split.get("version"), "value.splits." + role + ".version")
        _require(type(split.get("row_count")) is int and split["row_count"] > 0,
                 "NONEMPTY_SPLIT_REQUIRED", "value.splits." + role)
        _require(target in split.get("columns", []), "TARGET_NOT_IN_SPLIT_SCHEMA", "value.splits." + role)
    _require(len({s["content_hash"] for s in splits.values()}) == 4,
             "SPLIT_HASH_COLLISION_OR_REUSE", "value.splits")
    model = _object(protocol.get("models"), "value.models").get(kind)
    _object(model, "value.model")
    _text(model.get("name"), "value.model.name")
    preprocessing = _text(protocol.get("preprocessing"), "value.preprocessing")
    freeze = _json(material.artifacts["validation_freeze.json"], "value.validation_freeze")
    freeze_hash = digest(material.artifacts["validation_freeze.json"])
    _require(report["validation_freeze_sha256"] == freeze_hash,
             "VALIDATION_FREEZE_HASH_MISMATCH", "value.validation_freeze")
    _require(freeze.get("protocol_hash") == protocol_hash and
             freeze.get("fit_records_sha256") == digest(material.artifacts["fit_records.json"]),
             "FREEZE_CHAIN_MISMATCH", "value.validation_freeze")
    for field in ("validation", "recommendation"):
        _equal(freeze.get(field), report[field], "FROZEN_DECISION_MISMATCH", "value." + field)
    _require(freeze.get("test_predictions_executed") is False and
             freeze.get("validation_decision_uses_test") is False and
             report["recommendation"].get("uses_test") is False,
             "TEST_FREEZE_POLICY_VIOLATION", "value.validation_freeze")
    test_started = _json(material.artifacts["test_started.json"], "value.test_started")
    _require(test_started.get("protocol_hash") == protocol_hash and
             test_started.get("validation_freeze_sha256") == freeze_hash,
             "TEST_STARTED_FREEZE_MISMATCH", "value.test_started")
    fits = _json(b'{"records":' + material.artifacts["fit_records.json"] + b'}', "value.fit_records")["records"]
    _require(isinstance(fits, list) and bool(fits) and len(fits) == report["fit_count"]
             and all(isinstance(f, dict) and f.get("actual_fit_executed") is True for f in fits),
             "ACTUAL_FIT_RECORDS_REQUIRED", "value.fit_records")
    seeds = task.get("model_seeds")
    _require(isinstance(seeds, list) and bool(seeds) and
             all(type(seed) is int for seed in seeds) and len(set(seeds)) == len(seeds),
             "MODEL_SEEDS_REQUIRED", "value.task.model_seeds")
    _require(len(fits) == 2 * len(seeds) and
             {(f.get("model_seed"), f.get("role")) for f in fits} ==
             {(seed, role) for seed in seeds for role in ("base", "augmented")},
             "PAIRED_FIT_ROLES_REQUIRED", "value.fit_records")
    for fit in fits:
        expected_rows = splits["D0"]["row_count"] + (splits["Di"]["row_count"] if fit["role"] == "augmented" else 0)
        _require(fit.get("training_rows") == expected_rows,
                 "FIT_TRAINING_ROWS_MISMATCH", "value.fit_records")
        if fit["role"] == "base":
            _require(fit.get("training_hash") == splits["D0"]["content_hash"],
                     "FIT_D0_VERSION_MISMATCH", "value.fit_records")
    model_states = [f.get("model_state_sha256") for f in fits]
    for value in model_states:
        _sha(value, "value.fit_records.model_state_sha256")
    _equal(freeze.get("model_state_sha256"), model_states, "FIT_STATE_FREEZE_MISMATCH", "value.fit_records")
    expected_loss = "log_loss" if kind == "classification" else "mae"
    expected_unit = "natural_log_units" if kind == "classification" else task.get("target_unit")
    _text(expected_unit, "value.loss_unit")
    for split in ("validation", "test"):
        result = _object(report[split], "value." + split)
        _require(result.get("loss_name") == expected_loss and result.get("loss_unit") == expected_unit,
                 "LOSS_OR_UNIT_MISMATCH", "value." + split)
        utility = result.get("delta_u")
        _require(type(utility) in {int, float} and math.isfinite(utility),
                 "FINITE_UTILITY_REQUIRED", "value." + split + ".delta_u")
        _equal(report["marginal_utility"].get(split), utility,
               "MARGINAL_UTILITY_MISMATCH", "value.marginal_utility." + split)
    _require(report["test"].get("evaluation_use") == "POSTHOC_ONLY_NOT_AN_INPUT_TO_CURRENT_RECOMMENDATION",
             "POSTHOC_TEST_POLICY_REQUIRED", "value.test.evaluation_use")
    binding = {"task_id": "task:sha256:" + digest(canonical(task)), "target": target,
        "task_type": kind, "task_configuration_sha256": digest(canonical(task)),
        "buyer_mode": task.get("buyer_mode"), "D0": copy.deepcopy(splits["D0"]),
        "learner": copy.deepcopy(model), "learner_configuration_sha256": digest(canonical(model)),
        "preprocessing": preprocessing,
        "preprocessing_configuration_sha256": digest(canonical({name: protocol.get(name) for name in
            ("preprocessing", "schema_inference", "feature_columns", "numeric_columns", "categorical_columns", "class_labels")})),
        "evaluation": {"validation": copy.deepcopy(splits["validation"]), "test": copy.deepcopy(splits["test"]),
            "split_membership_sha256": digest(material.artifacts["split_membership.csv"]),
            "loss_name": expected_loss, "loss_unit": expected_unit, "test_use": "POSTHOC_ONLY"},
        "value_run_id": run_id, "value_protocol_sha256": protocol_hash,
        "value_request_sha256": request_hash, "value_result_sha256": digest(material.report_bytes),
        "engine_report_sha256": digest(material.artifacts["report.json"]),
        "validation_freeze_sha256": freeze_hash,
        "evaluation_started_at": request["created_at"], "evaluation_finished_at": status["finished_at"]}
    summary = {"status": "EVALUATED", "value_status": report["validation"].get("value_status"),
        "validation": copy.deepcopy(report["validation"]), "test": copy.deepcopy(report["test"]),
        "recommendation": copy.deepcopy(report["recommendation"]), "buyer_mode": task.get("buyer_mode"),
        "market_price": "NOT_EVALUATED", "trust_multiplier_applied": False}
    return binding, summary


def _markdown(result):
    lines = ["# 同版本数据集可信—价值联合评估", "", f"- 联合状态：{result['status']}",
             f"- 方法版本：{METHOD_VERSION}", f"- 联合生成时间：{result['evaluated_at']}"]
    binding = result.get("binding") or {}
    asset = binding.get("asset") or {}
    if asset:
        lines += [f"- 资产：{asset['asset_id']} / {asset['asset_version']}", f"- 数据集SHA-256：{asset['asset_sha256']}"]
    lines += ["", "## 可信证据", "", "文件完整性与同版本绑定不等于事实真实性；内容事实状态保持UNKNOWN。",
              "AI视觉结果只支持固定观察规则下的窄范围判断；重复审阅不增加独立物理样本。",
              "", "## 任务价值", "", f"- 测量状态：{result['task_value']['status']}"]
    if result["task_value"].get("status") == "EVALUATED":
        value = result["task_value"]
        lines += [f"- 价值证据：{value.get('value_status')}", f"- 买方模式：{value.get('buyer_mode')}",
                  f"- 验证集边际效用：{value['validation']['delta_u']} {value['validation']['loss_unit']}",
                  f"- 测试集事后边际效用：{value['test']['delta_u']} {value['test']['loss_unit']}",
                  "- 测试集不参与当前建议；任务效用不是市场价格，可信度不直接乘入价值。"]
    for issue in result["issues"]:
        lines.append(f"- 未通过：{issue['code']}（{issue['path']}）")
    lines += ["", "## 使用建议", "", result["recommendation"], "",
              "绑定结果可复算，但不能证明本地服务器未被控制、来源独立或现实标签真实。"]
    return "\n".join(lines) + "\n"


def coordinate_joint_assessment(*, trust_resolver: Callable[[], TrustMaterial],
    measurement_resolver: Callable[[], MeasurementMaterial] | None,
    evaluated_at: str, expected_binding: dict | None = None) -> dict:
    """Verify server-resolved bytes, derive a complete binding, and join outputs.

    expected_binding, if present, is an exact full pinned binding; partial
    matching is deliberately unavailable. Integrity is not source/fact
    authentication. Resolver failures are recorded without their potentially
    sensitive exception messages. No resolver accepts a client receipt string.
    """
    result = {"method_version": METHOD_VERSION, "status": "NOT_EVALUATED",
        "evaluated_at": evaluated_at, "binding": None, "trust": None,
        "task_value": {"status": "NOT_EVALUATED", "reason": "NO_COMPLETED_EXACT_VERSION_MEASUREMENT"},
        "issues": [], "recommendation": "任务条件不足；保留可信证据分析，补充精确版本的合法任务评价资料。",
        "trust_and_value_independent": True, "synthetic_test_is_research_evidence": False}
    try:
        now = _time(evaluated_at, "evaluated_at")
        _require(callable(trust_resolver), "SERVER_TRUST_RESOLVER_REQUIRED", "trust_resolver")
        trust_binding, trust_summary = _trust(trust_resolver(), now)
        result.update(binding=trust_binding, trust=trust_summary)
        if measurement_resolver is not None:
            _require(callable(measurement_resolver), "SERVER_VALUE_RESOLVER_REQUIRED", "measurement_resolver")
            value_binding, value_summary = _measurement(measurement_resolver(), trust_binding["asset"], now)
            binding = {**trust_binding, **value_binding}
            if expected_binding is not None:
                _object(expected_binding, "expected_binding")
                _equal(binding, expected_binding, "PINNED_BINDING_MISMATCH", "expected_binding")
            decision = value_summary.get("value_status")
            if decision == "POSITIVE_VALUE_EVIDENCE":
                advice = "可信证据与任务效用针对同一精确版本；可在已声明任务条件下考虑研究使用。正式事实用途仍需相应独立证据。"
            elif decision == "NEGATIVE_VALUE_EVIDENCE":
                advice = "可信证据与任务效用针对同一精确版本；当前任务已有负价值证据，保留不使用选项。事实真实性仍需独立证据。"
            else:
                advice = "可信证据与任务效用针对同一精确版本；当前价值或模型证据不足，暂缓使用或补充评价，不解释为已经证明无价值。"
            result.update(status="SAME_ASSET_BOUND", binding=binding, task_value=value_summary,
                          recommendation=advice)
        elif expected_binding is not None:
            _equal(trust_binding, expected_binding, "PINNED_BINDING_MISMATCH", "expected_binding")
    except _Issue as exc:
        result["status"] = "NOT_EVALUATED" if exc.not_evaluated else "BINDING_MISMATCH"
        result["issues"].append({"code": exc.code, "path": exc.path})
        result["task_value"] = {"status": "NOT_EVALUATED", "reason": exc.code}
        result["recommendation"] = "当前绑定或证据链未通过；不得合并可信与价值结论，请检查精确版本及原始文件。"
    except Exception as exc:
        result["status"] = "BINDING_MISMATCH"
        result["issues"].append({"code": "SERVER_RESOLVER_OR_MATERIAL_INVALID", "path": "server_resolver",
                                 "error_class": type(exc).__name__})
        result["task_value"] = {"status": "NOT_EVALUATED", "reason": "SERVER_RESOLVER_OR_MATERIAL_INVALID"}
        result["recommendation"] = "服务端未能验证原始资料；停止合并并检查本地证据。"
    result["report_markdown"] = _markdown(result)
    result["result_sha256"] = digest(canonical(result))
    return result
