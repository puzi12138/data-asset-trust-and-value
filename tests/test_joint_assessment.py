"""SYNTHETIC software fixtures: no training, image review, or fact audit.

The fixtures deliberately imitate completed local run schemas to exercise
binding rules; they are never research observations or model-call receipts.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import unittest

from data_asset_poc.joint_assessment import (MeasurementMaterial, TrustMaterial,
    canonical, coordinate_joint_assessment, digest)

NOW = "2026-10-25T12:00:00+00:00"


def fixture():
    """Return a fully synthetic server material pair and mutable run objects."""
    raw = b"row_id,x,target\n1,0,0\n2,1,1\n3,2,0\n4,3,1\n"
    h = digest(raw)
    asset = {"asset_id": "ds_" + h, "asset_version": "sha256:" + h, "asset_sha256": h}
    image = b"SYNTHETIC_IMAGE_BYTES_NOT_A_REAL_VISUAL_OBSERVATION"
    evidence = [{"evidence_id": "image-1", "sha256": digest(image), "pixel_sha256": digest(b"SYNTHETIC_PIXELS")}]
    audit_protocol = {"task_id": "SYNTHETIC-observation", "field": "x", "observation_rule": "SYNTHETIC fixture only"}
    trust_report = {"asset": asset, "protocol": audit_protocol, "evidence": evidence,
        "protocol_sha256": digest(canonical({"asset": asset, "protocol": audit_protocol, "evidence": evidence})),
        "assessment": {"fact_status": "UNKNOWN", "decision": "NEED_MORE_EVIDENCE"},
        "reviews": [], "coordination_state": "ADDITIONAL_EVIDENCE_REQUIRED",
        "created_at": "2026-10-25T09:00:00+00:00"}
    trust = TrustMaterial("VISUAL_CASE", asset, raw, canonical(trust_report), canonical(audit_protocol), {"image-1": image})
    record = {"dataset_id": "ds_" + h, "version": "sha256:" + h, "content_hash": h,
        "separator": ",", "row_count": 4, "column_count": 3,
        "columns": [{"name": name, "dtype": "int64"} for name in ("row_id", "x", "target")]}
    task = {"target": "target", "task_type": "classification", "positive_label": "1",
        "buyer_mode": "SIMULATED_BUYER_SCENARIO", "model_seeds": [101], "target_unit": "target_units",
        "split_mode": "record", "seed": 101, "n_bootstrap": 200, "alpha": 0.05,
        "id_columns": ["row_id"], "entity_column": None, "time_column": None}
    splits = {name: {"content_hash": digest(name.encode()), "version": "canonical-v1-" + name,
        "row_count": 1, "columns": ["row_id", "x", "target"], "group_count": 1,
        "hash_basis": "SYNTHETIC canonical table fixture"} for name in ("D0", "Di", "validation", "test")}
    inputs = {"candidate": record}
    input_binding = {"registered_inputs": inputs, "tables": {"candidate": {"content_hash": digest(b"canonical table")}},
                     "registered_bytes_and_canonical_tables_are_distinct": True}
    isolation = {"record_feature_groups": "SEPARATED", "entity": "UNKNOWN_NO_ENTITY_COLUMN"}
    protocol = {"engine_version": "SYNTHETIC-v1", "task": task, "input_binding": input_binding,
        "splits": splits, "isolation_assessment": isolation,
        "models": {"classification": {"name": "LogisticRegression", "C": 1.0}},
        "preprocessing": "train-only SYNTHETIC test configuration", "feature_columns": ["x"],
        "numeric_columns": ["x"], "categorical_columns": [], "class_labels": {"positive": "1", "negative": "0"},
        "schema_inference": "D0 only"}
    result = {"delta_u": 0.02, "ci_lower": -0.01, "ci_upper": 0.05,
        "loss_name": "log_loss", "loss_unit": "natural_log_units", "value_status": "VALUE_UNCERTAIN"}
    recommendation = {"action": "do_not_use", "uses_test": False,
        "value_status": "VALUE_UNCERTAIN", "no_purchase_delta_u": 0.0}
    fits = [{"actual_fit_executed": True, "model_seed": 101, "role": role,
        "training_rows": 1 if role == "base" else 2,
        "training_hash": splits["D0"]["content_hash"] if role == "base" else digest(b"augmented"),
        "model_state_sha256": digest(role.encode())} for role in ("base", "augmented")]
    objects = {"protocol.json": protocol, "fit_records.json": fits,
        "bootstrap_validation.json": {"SYNTHETIC": True}, "bootstrap_test.json": {"SYNTHETIC": True}}
    artifacts = {name: canonical(value) for name, value in objects.items()}
    artifacts.update({"split_membership.csv": b"source,source_row,role,group_id\ncandidate,0,D0,a\n",
        "predictions.csv": b"SYNTHETIC predictions fixture\n", "metrics.csv": b"SYNTHETIC metrics fixture\n"})
    p_hash = digest(artifacts["protocol.json"])
    freeze = {"protocol_hash": p_hash, "validation": copy.deepcopy(result), "recommendation": recommendation,
        "fit_records_sha256": digest(artifacts["fit_records.json"]),
        "model_state_sha256": [f["model_state_sha256"] for f in fits],
        "test_predictions_executed": False, "validation_decision_uses_test": False}
    artifacts["validation_freeze.json"] = canonical(freeze)
    f_hash = digest(artifacts["validation_freeze.json"])
    artifacts["test_started.json"] = canonical({"protocol_hash": p_hash, "validation_freeze_sha256": f_hash})
    test = {**result, "evaluation_use": "POSTHOC_ONLY_NOT_AN_INPUT_TO_CURRENT_RECOMMENDATION"}
    engine = {"status": "EVALUATED", "engine_version": "SYNTHETIC-v1", "task": task,
        "input_binding": input_binding, "protocol_hash": p_hash, "validation_freeze_sha256": f_hash,
        "splits": splits, "fit_count": 2, "validation": copy.deepcopy(result), "test": test,
        "isolation_assessment": isolation, "marginal_utility": {"validation": .02, "test": .02},
        "recommendation": recommendation,
        "artifacts": {name: {"sha256": digest(value), "bytes": len(value)} for name, value in artifacts.items()}}
    job_protocol = {"inputs": inputs, "task": task, "protocol_version": "SYNTHETIC-v1"}
    request_hash = digest(canonical(job_protocol))
    request = {"run_id": "SYNTHETIC-run", "created_at": "2026-10-25T10:00:00+00:00",
        "request_hash": request_hash, "protocol": job_protocol}
    report = {**engine, "run_id": "SYNTHETIC-run", "request_hash": request_hash,
        "request_protocol": job_protocol, "registered_inputs": inputs}
    artifacts["report.json"] = canonical(engine)
    status = {"status": "COMPLETED", "run_id": "SYNTHETIC-run", "request_hash": request_hash,
        "finished_at": "2026-10-25T10:01:00+00:00", "input_hashes_verified_before_and_after": True,
        "report_sha256": digest(canonical(report))}
    measurement = MeasurementMaterial(canonical(report), canonical(status), canonical(request), artifacts, {"candidate": raw})
    return trust, measurement


def coordinate(trust=None, value=None, expected=None):
    if trust is None and value is None:
        trust, value = fixture()
    return coordinate_joint_assessment(trust_resolver=lambda: trust,
        measurement_resolver=(lambda: value) if value is not None else None,
        evaluated_at=NOW, expected_binding=expected)


def reseal(value, *, report=None, status=None, request=None, artifacts=None):
    report = copy.deepcopy(json.loads(value.report_bytes) if report is None else report)
    status = copy.deepcopy(json.loads(value.status_bytes) if status is None else status)
    status["report_sha256"] = digest(canonical(report))
    return replace(value, report_bytes=canonical(report), status_bytes=canonical(status),
        request_bytes=value.request_bytes if request is None else canonical(request),
        artifacts=value.artifacts if artifacts is None else artifacts)


class JointBindingTests(unittest.TestCase):
    def assertRejected(self, result, code=None):
        self.assertEqual(result["status"], "BINDING_MISMATCH", result)
        self.assertEqual(result["task_value"]["status"], "NOT_EVALUATED")
        if code:
            self.assertEqual(result["issues"][0]["code"], code)

    def test01_synthetic_same_asset_complete(self):
        result = coordinate()
        self.assertEqual(result["status"], "SAME_ASSET_BOUND", result)
        self.assertEqual(result["trust"]["fact_status"], "UNKNOWN")
        self.assertEqual(result["task_value"]["value_status"], "VALUE_UNCERTAIN")
        self.assertIn("不解释为已经证明无价值", result["recommendation"])
        self.assertNotEqual(result["binding"]["audit_task_id"], result["binding"]["task_id"])

    def test02_deterministic_recompute(self):
        self.assertEqual(coordinate(), coordinate())

    def test03_result_hash(self):
        result = coordinate()
        expected = result.pop("result_sha256")
        self.assertEqual(digest(canonical(result)), expected)

    def test04_full_pin_accepts(self):
        t, v = fixture()
        pin = coordinate(t, v)["binding"]
        self.assertEqual(coordinate(t, v, pin)["status"], "SAME_ASSET_BOUND")

    def test05_pinned_field_mutations(self):
        t, v = fixture()
        binding = coordinate(t, v)["binding"]
        for field in binding:
            with self.subTest(field=field):
                changed = copy.deepcopy(binding)
                changed[field] = "WRONG_OR_TAMPERED"
                self.assertRejected(coordinate(t, v, changed), "PINNED_BINDING_MISMATCH")

    def test06_partial_pin_rejected(self):
        t, v = fixture()
        self.assertRejected(coordinate(t, v, {"asset": t.asset}), "PINNED_BINDING_MISMATCH")

    def test07_missing_pinned_fields(self):
        t, v = fixture()
        binding = coordinate(t, v)["binding"]
        for field in binding:
            with self.subTest(field=field):
                changed = copy.deepcopy(binding)
                del changed[field]
                self.assertRejected(coordinate(t, v, changed), "PINNED_BINDING_MISMATCH")

    def test08_no_measurement_unknown(self):
        t, _ = fixture()
        result = coordinate(t)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertEqual(result["trust"]["fact_status"], "UNKNOWN")

    def test09_status_not_completed(self):
        t, v = fixture()
        v = reseal(v, status={"status": "RUNNING"})
        self.assertEqual(coordinate(t, v)["status"], "NOT_EVALUATED")

    def test10_no_target_not_evaluated(self):
        t, v = fixture()
        report = json.loads(v.report_bytes)
        report["status"] = "NOT_EVALUATED"
        self.assertEqual(coordinate(t, reseal(v, report=report))["status"], "NOT_EVALUATED")

    def test11_asset_bytes_tampered(self):
        t, v = fixture()
        self.assertRejected(coordinate(replace(t, asset_bytes=t.asset_bytes + b"x"), v), "ASSET_BYTES_HASH_MISMATCH")

    def test12_asset_id_rejected_even_same_bytes(self):
        t, v = fixture()
        asset = {**t.asset, "asset_id": "same-friendly-name"}
        report = json.loads(t.report_bytes)
        report["asset"] = asset
        report["protocol_sha256"] = digest(canonical({"asset": asset, "protocol": report["protocol"], "evidence": report["evidence"]}))
        self.assertRejected(coordinate(replace(t, asset=asset, report_bytes=canonical(report)), v), "EXACT_ASSET_BINDING_MISMATCH")

    def test13_asset_version_rejected_even_same_bytes(self):
        t, v = fixture()
        asset = {**t.asset, "asset_version": "v2"}
        report = json.loads(t.report_bytes)
        report["asset"] = asset
        report["protocol_sha256"] = digest(canonical({"asset": asset, "protocol": report["protocol"], "evidence": report["evidence"]}))
        self.assertRejected(coordinate(replace(t, asset=asset, report_bytes=canonical(report)), v), "EXACT_ASSET_BINDING_MISMATCH")

    def test14_nasa_ionosphere_mix_rejected(self):
        t, v = fixture()
        raw = b"nasa_id,description\nas17-148-22727,cloud disc\n"
        asset = {"asset_id": "nasa-apollo-public-image-catalog", "asset_version": "acquired-v1", "asset_sha256": digest(raw)}
        report = json.loads(t.report_bytes)
        report["asset"] = asset
        report["protocol_sha256"] = digest(canonical({"asset": asset, "protocol": report["protocol"], "evidence": report["evidence"]}))
        self.assertRejected(coordinate(replace(t, asset=asset, asset_bytes=raw, report_bytes=canonical(report)), v), "EXACT_ASSET_BINDING_MISMATCH")

    def test15_evidence_bytes_tampered(self):
        t, v = fixture()
        self.assertRejected(coordinate(replace(t, evidence_bytes={"image-1": b"wrong"}), v), "EVIDENCE_HASH_MISMATCH")

    def test16_evidence_missing(self):
        t, v = fixture()
        self.assertRejected(coordinate(replace(t, evidence_bytes={}), v), "EVIDENCE_BYTES_MISSING")

    def test17_extra_evidence(self):
        t, v = fixture()
        self.assertRejected(coordinate(replace(t, evidence_bytes={**t.evidence_bytes, "other": b"other"}), v), "EVIDENCE_SET_MISMATCH")

    def test18_protocol_bytes_changed(self):
        t, v = fixture()
        p = json.loads(t.protocol_bytes)
        p["observation_rule"] = "changed"
        self.assertRejected(coordinate(replace(t, protocol_bytes=canonical(p)), v), "AUDIT_PROTOCOL_MISMATCH")

    def test19_protocol_hash_changed(self):
        t, v = fixture()
        p = json.loads(t.report_bytes)
        p["protocol_sha256"] = "0" * 64
        self.assertRejected(coordinate(replace(t, report_bytes=canonical(p)), v), "AUDIT_PROTOCOL_HASH_MISMATCH")

    def test20_self_reported_receipt_does_not_authenticate(self):
        t, v = fixture()
        p = json.loads(t.report_bytes)
        p.update(validation_receipt="TRUST_ME", source_status="VERIFIED", fact_status="VERIFIED")
        result = coordinate(replace(t, report_bytes=canonical(p)), v)
        self.assertEqual(result["status"], "SAME_ASSET_BOUND")
        self.assertEqual(result["trust"]["fact_status"], "UNKNOWN")
        self.assertEqual(result["trust"]["source_authentication"], "NOT_ESTABLISHED_BY_JOINT_BINDING")

    def test21_wrong_value_report_bytes(self):
        t, v = fixture()
        self.assertRejected(coordinate(t, replace(v, report_bytes=v.report_bytes + b" ")), "VALUE_REPORT_HASH_MISMATCH")

    def test22_wrong_input_bytes(self):
        t, v = fixture()
        self.assertRejected(coordinate(t, replace(v, input_bytes={"candidate": b"wrong"})), "INPUT_BYTES_HASH_MISMATCH")

    def test23_missing_input_bytes(self):
        t, v = fixture()
        self.assertRejected(coordinate(t, replace(v, input_bytes={})), "INPUT_BYTES_SET_MISMATCH")

    def test24_all_required_artifact_tampering(self):
        t, v = fixture()
        for name in v.artifacts:
            with self.subTest(artifact=name):
                artifacts = dict(v.artifacts)
                artifacts[name] += b"TAMPERED"
                self.assertRejected(coordinate(t, replace(v, artifacts=artifacts)))

    def test25_all_required_artifact_missing(self):
        t, v = fixture()
        for name in v.artifacts:
            with self.subTest(artifact=name):
                artifacts = dict(v.artifacts)
                del artifacts[name]
                self.assertRejected(coordinate(t, replace(v, artifacts=artifacts)))

    def test26_wrong_run_id(self):
        t, v = fixture()
        status = json.loads(v.status_bytes)
        status["run_id"] = "wrong"
        self.assertRejected(coordinate(t, reseal(v, status=status)), "RUN_ID_MISMATCH")

    def test27_wrong_request_hash(self):
        t, v = fixture()
        status = json.loads(v.status_bytes)
        status["request_hash"] = "0" * 64
        self.assertRejected(coordinate(t, reseal(v, status=status)), "REQUEST_PROTOCOL_HASH_MISMATCH")

    def test28_missing_input_verification(self):
        t, v = fixture()
        status = json.loads(v.status_bytes)
        del status["input_hashes_verified_before_and_after"]
        self.assertRejected(coordinate(t, reseal(v, status=status)), "WORKER_INPUT_VERIFICATION_REQUIRED")

    def test29_future_measurement(self):
        t, v = fixture()
        status = json.loads(v.status_bytes)
        status["finished_at"] = "2026-10-26T10:00:00+00:00"
        self.assertRejected(coordinate(t, reseal(v, status=status)), "EVALUATION_TIME_ORDER_INVALID")

    def test30_naive_measurement_time(self):
        t, v = fixture()
        status = json.loads(v.status_bytes)
        status["finished_at"] = "2026-10-25T10:01:00"
        self.assertRejected(coordinate(t, reseal(v, status=status)), "TIMEZONE_TIMESTAMP_REQUIRED")

    def test31_time_reversed(self):
        t, v = fixture()
        status = json.loads(v.status_bytes)
        status["finished_at"] = "2026-10-25T09:00:00+00:00"
        self.assertRejected(coordinate(t, reseal(v, status=status)), "EVALUATION_TIME_ORDER_INVALID")

    def test32_engine_worker_task_mismatch(self):
        t, v = fixture()
        report = json.loads(v.report_bytes)
        report["task"]["target"] = "wrong"
        self.assertRejected(coordinate(t, reseal(v, report=report)), "ENGINE_WORKER_REPORT_MISMATCH")

    def test33_engine_worker_D0_mismatch(self):
        t, v = fixture()
        report = json.loads(v.report_bytes)
        report["splits"]["D0"]["version"] = "wrong"
        self.assertRejected(coordinate(t, reseal(v, report=report)), "ENGINE_WORKER_REPORT_MISMATCH")

    def test34_engine_worker_loss_mismatch(self):
        t, v = fixture()
        report = json.loads(v.report_bytes)
        report["validation"]["loss_name"] = "mae"
        self.assertRejected(coordinate(t, reseal(v, report=report)), "ENGINE_WORKER_REPORT_MISMATCH")

    def test35_engine_worker_utility_mismatch(self):
        t, v = fixture()
        report = json.loads(v.report_bytes)
        report["validation"]["delta_u"] = 99
        self.assertRejected(coordinate(t, reseal(v, report=report)), "ENGINE_WORKER_REPORT_MISMATCH")

    def test36_duplicate_json_key_rejected(self):
        t, v = fixture()
        report = b'{"asset":{},"asset":{}}'
        self.assertRejected(coordinate(replace(t, report_bytes=report), v), "DUPLICATE_JSON_KEY")

    def test37_nonfinite_json_rejected(self):
        t, v = fixture()
        self.assertRejected(coordinate(replace(t, report_bytes=b'{"x":NaN}'), v), "STRICT_JSON_REQUIRED")

    def test38_raw_dict_not_trust_material(self):
        _, v = fixture()
        self.assertRejected(coordinate({"validation_receipt": "signed"}, v), "SERVER_TRUST_MATERIAL_REQUIRED")

    def test39_resolver_exception_not_exposed(self):
        def fail():
            raise ValueError("sensitive-token-must-not-appear")
        result = coordinate_joint_assessment(trust_resolver=fail, measurement_resolver=None, evaluated_at=NOW)
        self.assertRejected(result, "SERVER_RESOLVER_OR_MATERIAL_INVALID")
        self.assertNotIn("sensitive-token", json.dumps(result))

    def test40_structural_only_is_supported(self):
        t, v = fixture()
        p = {"method": "SYNTHETIC structure-only", "task_id": None}
        report = {"asset": t.asset, "assessment": {"structure": {"completeness": 1.0}},
            "audit_protocol_sha256": digest(canonical(p)), "evidence": []}
        t = replace(t, kind="STRUCTURAL_ASSESSMENT", protocol_bytes=canonical(p), report_bytes=canonical(report), evidence_bytes={})
        result = coordinate(t, v)
        self.assertEqual(result["status"], "SAME_ASSET_BOUND")
        self.assertEqual(result["trust"]["fact_status"], "UNKNOWN")

    def test41_visual_review_wrong_asset_rejected(self):
        t, v = fixture()
        report = json.loads(t.report_bytes)
        report["reviews"] = [{"asset": {**t.asset, "asset_version": "wrong"}}]
        self.assertRejected(coordinate(replace(t, report_bytes=canonical(report)), v), "REVIEW_ASSET_MISMATCH")

    def test42_future_trust_rejected(self):
        t, v = fixture()
        report = json.loads(t.report_bytes)
        report["created_at"] = "2026-11-01T00:00:00Z"
        self.assertRejected(coordinate(replace(t, report_bytes=canonical(report)), v), "TRUST_TIMESTAMP_AFTER_EVALUATION")

    def test43_unknown_fields_in_asset_rejected(self):
        t, v = fixture()
        self.assertRejected(coordinate(replace(t, asset={**t.asset, "alias": "friendly"}), v), "EXACT_ASSET_FIELDS_REQUIRED")

    def test44_repeated_join_does_not_add_observation(self):
        t, v = fixture()
        results = [coordinate(t, v) for _ in range(3)]
        self.assertEqual(len({r["result_sha256"] for r in results}), 1)
        self.assertTrue(all(r["trust"]["additional_physical_observations_from_joint_binding"] == 0 for r in results))

    def test45_canonical_different_newlines_are_same_json_not_raw_hash(self):
        value = {"a": 1, "b": "中文"}
        lf = json.dumps(value, ensure_ascii=False, indent=2).encode()
        crlf = lf.replace(b"\n", b"\r\n")
        self.assertNotEqual(digest(lf), digest(crlf))
        self.assertEqual(canonical(json.loads(lf)), canonical(json.loads(crlf)))

    def test46_live_review_chain_valid_and_unknown(self):
        t, v = fixture()
        report = json.loads(t.report_bytes)
        review = {"asset": report["asset"], "protocol_sha256": report["protocol_sha256"],
            "evidence_id": "image-1", "evidence_sha256": report["evidence"][0]["sha256"],
            "outcome": "MATCH", "reviewer_type": "AI", "execution_mode": "LIVE",
            "observation_basis": "SYNTHETIC software fixture; not an actual image review"}
        review["result_sha256"] = digest(canonical(review))
        execution = {"asset": report["asset"], "protocol_sha256": report["protocol_sha256"],
            "status": "LIVE", "review": review, "synthetic": True}
        execution["result_sha256"] = digest(canonical(execution))
        report["reviews"] = [review]
        report["executions"] = [execution]
        result = coordinate(replace(t, report_bytes=canonical(report)), v)
        self.assertEqual(result["status"], "SAME_ASSET_BOUND", result)
        self.assertEqual(result["trust"]["fact_status"], "UNKNOWN")

    def test47_tampered_review_hash_rejected(self):
        t, v = fixture()
        report = json.loads(t.report_bytes)
        report["reviews"] = [{"asset": report["asset"], "protocol_sha256": report["protocol_sha256"],
            "evidence_id": "image-1", "evidence_sha256": report["evidence"][0]["sha256"],
            "outcome": "MATCH", "result_sha256": "0" * 64}]
        self.assertRejected(coordinate(replace(t, report_bytes=canonical(report)), v), "REVIEW_RESULT_HASH_MISMATCH")

    def test48_tampered_execution_hash_rejected(self):
        t, v = fixture()
        report = json.loads(t.report_bytes)
        report["executions"] = [{"asset": report["asset"], "protocol_sha256": report["protocol_sha256"],
            "status": "FAILED", "result_sha256": "0" * 64}]
        self.assertRejected(coordinate(replace(t, report_bytes=canonical(report)), v), "EXECUTION_RESULT_HASH_MISMATCH")

    def test49_orphaned_review_rejected(self):
        t, v = fixture()
        report = json.loads(t.report_bytes)
        review = {"asset": report["asset"], "protocol_sha256": report["protocol_sha256"],
            "evidence_id": "image-1", "evidence_sha256": report["evidence"][0]["sha256"], "outcome": "MATCH"}
        review["result_sha256"] = digest(canonical(review))
        report["reviews"] = [review]
        report["executions"] = []
        self.assertRejected(coordinate(replace(t, report_bytes=canonical(report)), v), "EXECUTION_REVIEW_SET_MISMATCH")

    def test50_asset_or_task_schema_missing(self):
        t, v = fixture()
        for name in t.asset:
            with self.subTest(name=name):
                asset = dict(t.asset)
                del asset[name]
                self.assertRejected(coordinate(replace(t, asset=asset), v), "EXACT_ASSET_FIELDS_REQUIRED")

    def test51_missing_worker_or_engine_field(self):
        t, v = fixture()
        for name in ("task", "splits", "validation", "recommendation", "input_binding", "fit_count"):
            with self.subTest(name=name):
                report = json.loads(v.report_bytes)
                del report[name]
                self.assertRejected(coordinate(t, reseal(v, report=report)), "ENGINE_REPORT_FIELD_MISSING")

    def test52_reference_evidence_keeps_unknown(self):
        t, v = fixture()
        p = {"method": "SYNTHETIC reference comparison", "task_id": "audit-reference"}
        report = {"asset": t.asset, "assessment": {"statistical_decision": "ACCEPT_CONDITIONALLY", "fact_status": "UNKNOWN"},
            "audit_protocol_sha256": digest(canonical(p)),
            "evidence": [{"evidence_id": "image-1", "sha256": digest(t.evidence_bytes["image-1"])}]}
        result = coordinate(replace(t, kind="REFERENCE_AUDIT", protocol_bytes=canonical(p), report_bytes=canonical(report)), v)
        self.assertEqual(result["status"], "SAME_ASSET_BOUND", result)
        self.assertEqual(result["trust"]["fact_status"], "UNKNOWN")

    def test53_semantic_protocol_and_raw_protocol_are_distinct(self):
        t, v = fixture()
        pretty = json.dumps(json.loads(t.protocol_bytes), indent=2).encode()
        result = coordinate(replace(t, protocol_bytes=pretty), v)
        baseline = coordinate(t, v)
        self.assertEqual(result["status"], "SAME_ASSET_BOUND")
        self.assertEqual(result["binding"]["audit_protocol_sha256"], baseline["binding"]["audit_protocol_sha256"])
        self.assertNotEqual(result["binding"]["audit_protocol_bytes_sha256"], baseline["binding"]["audit_protocol_bytes_sha256"])


if __name__ == "__main__":
    unittest.main()
