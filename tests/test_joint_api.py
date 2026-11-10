"""Actual API tests with SYNTHETIC software fixtures only.

No provider call, model fitting, factual audit or research observation occurs.
The saved-run schema fixture exercises the real server resolvers and core.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from data_asset_poc.joint_assessment.gateway import create_joint_gateway
from data_asset_poc.joint_assessment import canonical, digest
from data_asset_poc.self_service_value import gateway as legacy

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Research-Request": "1", "Origin": "http://localhost:12360"}
RUN = "run_" + "a" * 32
spec = importlib.util.spec_from_file_location("joint_assessment_synthetic_core_fixtures", Path(__file__).with_name("test_joint_assessment.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def old_app():
    app = FastAPI()
    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.headers.get("host", "").split(":")[0] not in {"testserver", "localhost", "127.0.0.1"}:
            return JSONResponse(status_code=403, content={"detail": "LOCAL_HOST_REQUIRED"})
        if request.method == "POST" and (request.headers.get("x-research-request") != "1"
                or request.headers.get("origin") not in {None, "http://localhost:12360"}):
            return JSONResponse(status_code=403, content={"detail": "LOCAL_ACTION_REQUIRED"})
        return await call_next(request)
    @app.get("/api/workbench/options")
    def old_options():
        return {"status": "HISTORICAL_ROUTE_RETAINED"}
    return app


class JointAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="joint_assessment_api_SYNTHETIC_")
        self.workspace = Path(self.tmp.name)
        config = self.workspace / "consumer.json"
        config.write_text(json.dumps({"workspace": str(self.workspace)}), encoding="utf-8")
        self.patch = patch.object(legacy, "create_workbench_gateway", return_value=old_app())
        self.patch.start()
        self.adapter_patch = patch("data_asset_poc.visual_evidence_live.adapters.configured_adapter", return_value=None)
        self.adapter_patch.start()
        self.app = create_joint_gateway(config, ROOT)
        self.client = TestClient(self.app)
        trust, self.measurement = fixtures.fixture()
        self.raw = trust.asset_bytes
        uploaded = self.client.post("/api/self-service/datasets", content=self.raw,
            headers={**HEADERS, "Content-Type": "text/csv"})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.dataset = uploaded.json()
        im = io.BytesIO()
        Image.new("RGB", (8, 7), (20, 30, 40)).save(im, format="PNG")
        self.png = im.getvalue()
        self.payload = {"asset_id": self.dataset["dataset_id"], "asset_version": self.dataset["version"],
            "asset_sha256": digest(self.raw), "dataset_csv_base64": base64.b64encode(self.raw).decode(),
            "task_id": "SYNTHETIC-observation", "entity_key": "row_id", "entity_value": "1", "field": "x",
            "observation_target": "SYNTHETIC software fixture only", "observation_rule": "No fact audit",
            "capture_time": None, "source": {"description": "SYNTHETIC", "url": "", "license": "software fixture",
                "independence_declaration": "NOT_INDEPENDENT_FACT_EVIDENCE"},
            "authorization": {"local_processing": True, "external_model_upload": False},
            "evidence": [{"evidence_id": "fixture-image", "filename": "fixture.png", "sha256": digest(self.png),
                "data_base64": base64.b64encode(self.png).decode()}]}
        response = self.client.post("/api/visual-cases", json=self.payload, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.case = response.json()
        self.install_run()

    def tearDown(self):
        self.client.close()
        self.adapter_patch.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def install_run(self):
        self.folder = self.workspace / "self_service" / "runs" / RUN
        self.folder.mkdir()
        report = json.loads(self.measurement.report_bytes)
        request = json.loads(self.measurement.request_bytes)
        state = json.loads(self.measurement.status_bytes)
        for doc in (report, request, state):
            doc["run_id"] = RUN
        request["created_at"] = "2026-10-24T10:00:00+00:00"
        state["finished_at"] = "2026-10-24T10:01:00+00:00"
        state["report_sha256"] = digest(canonical(report))
        for name, document in (("report.json", report), ("request.json", request), ("status.json", state)):
            (self.folder / name).write_bytes(canonical(document))
        (self.folder / "measurement").mkdir()
        for name, raw in self.measurement.artifacts.items():
            (self.folder / "measurement" / name).write_bytes(raw)

    def post(self, **changes):
        return self.client.post("/api/same-asset-joint", json={"case_id": self.case["case_id"],
            "value_run_id": RUN, **changes}, headers=HEADERS)

    def assert_mismatch(self, response):
        self.assertEqual(response.status_code, 409, response.text)
        document = response.json()["detail"]
        self.assertEqual(document["status"], "BINDING_MISMATCH")
        self.assertEqual(document["task_value"]["status"], "NOT_EVALUATED")
        return document

    def test01_complete_synthetic_binding_reads_real_registered_files(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        doc = response.json()
        self.assertEqual(doc["status"], "SAME_ASSET_BOUND", doc)
        self.assertEqual(doc["binding"]["asset"]["asset_sha256"], digest(self.raw))
        self.assertEqual(doc["value_run_id"], RUN)
        self.assertEqual(doc["case_id"], self.case["case_id"])
        self.assertEqual(doc["trust"]["fact_status"], "UNKNOWN")
        self.assertEqual(doc["task_value"]["value_status"], "VALUE_UNCERTAIN")

    def test02_no_value_keeps_trust_not_evaluated(self):
        doc = self.post(value_run_id=None).json()
        self.assertEqual(doc["status"], "NOT_EVALUATED")
        self.assertEqual(doc["task_value"]["status"], "NOT_EVALUATED")
        self.assertEqual(doc["trust"]["fact_status"], "UNKNOWN")

    def test03_unknown_valid_run_not_evaluated(self):
        self.assertEqual(self.post(value_run_id="run_" + "b" * 32).json()["status"], "NOT_EVALUATED")

    def test04_capabilities_saved_identifiers_and_old_routes(self):
        result = self.client.get("/api/same-asset-joint/capabilities").json()
        self.assertEqual(result["value_runs"][0]["run_id"], RUN)
        self.assertEqual(result["value_runs"][0]["candidate"]["content_hash"], digest(self.raw))
        for path in ("/api/workbench/options", "/api/self-service/options", "/api/visual-cases/capabilities"):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test05_export_json_and_chinese_markdown(self):
        doc = self.post().json()
        url = "/api/same-asset-joint/" + doc["joint_id"]
        self.assertEqual(self.client.get(url).json(), doc)
        exported = self.client.get(url + "?format=md")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("同版本数据集", exported.text)
        self.assertEqual(exported.headers["X-Joint-Result-SHA256"], doc["result_sha256"])
        listed = self.client.get("/api/same-asset-joint").json()["reports"]
        self.assertEqual(listed[0]["joint_id"], doc["joint_id"])

    def test06_partial_pin_rejected(self):
        self.assert_mismatch(self.post(expected_binding={"asset_sha256": digest(self.raw)}))

    def test07_full_pin_accepts_then_changed_field_rejects(self):
        binding = self.post().json()["binding"]
        self.assertEqual(self.post(expected_binding=binding).json()["status"], "SAME_ASSET_BOUND")
        binding["target"] = "WRONG_TARGET"
        self.assert_mismatch(self.post(expected_binding=binding))

    def test08_wrong_asset_identity_same_bytes_rejected(self):
        payload = {**self.payload, "asset_id": "WRONG_ASSET"}
        other = self.client.post("/api/visual-cases", json=payload, headers=HEADERS).json()
        self.assert_mismatch(self.post(case_id=other["case_id"]))

    def test09_wrong_version_rejected(self):
        payload = {**self.payload, "asset_version": "wrong-version"}
        other = self.client.post("/api/visual-cases", json=payload, headers=HEADERS).json()
        self.assert_mismatch(self.post(case_id=other["case_id"]))

    def test10_tampered_candidate_bytes_rejected(self):
        path = self.workspace / "self_service" / "datasets" / self.dataset["dataset_id"] / "data.csv"
        path.write_bytes(self.raw + b"5,4,0\n")
        self.assert_mismatch(self.post())

    def test11_tampered_task_result_rejected(self):
        report = json.loads((self.folder / "report.json").read_bytes())
        report["validation"]["delta_u"] = 999
        (self.folder / "report.json").write_bytes(canonical(report))
        self.assert_mismatch(self.post())

    def test12_tampered_artifact_rejected(self):
        (self.folder / "measurement" / "validation_freeze.json").write_bytes(b"{}")
        self.assert_mismatch(self.post())

    def test13_invalid_run_id_path_rejected(self):
        for run in ("../../results/visual_evidence_live/value_run", "run_bad", 1, False):
            self.assertEqual(self.post(value_run_id=run).status_code, 422)

    def test14_client_results_paths_receipts_never_accepted(self):
        for key in ("report", "path", "validation_receipt", "trust", "result"):
            self.assertEqual(self.post(**{key: "FORGED"}).status_code, 422)

    def test15_forged_local_receipt_invalidates_case(self):
        path = self.app.state.visual_case_registry.root / "cases" / self.case["case_id"] / "case.json"
        envelope = json.loads(path.read_bytes())
        envelope["payload"]["protocol"]["source"]["description"] = "FORGED"
        path.write_bytes(canonical(envelope))
        self.assert_mismatch(self.post())

    def test16_wrong_evidence_bytes_rejected(self):
        folder = self.app.state.visual_case_registry.root / "cases" / self.case["case_id"]
        # Registry stores evidence by byte hash, not by a client filename.
        path = folder / self.case["evidence"][0]["stored_name"]
        self.assertTrue(path.is_file())
        path.write_bytes(b"WRONG_IMAGE_BYTES")
        self.assert_mismatch(self.post())

    def test17_saved_record_tamper_is_not_exported(self):
        doc = self.post().json()
        path = self.app.state.same_asset_joint_root / (doc["joint_id"] + ".json")
        saved = json.loads(path.read_bytes())
        saved["result"]["status"] = "FORGED"
        path.write_bytes(canonical(saved))
        self.assertEqual(self.client.get("/api/same-asset-joint/" + doc["joint_id"]).status_code, 409)

    def test18_saved_report_rechecks_source_before_download(self):
        doc = self.post().json()
        (self.folder / "measurement" / "metrics.csv").write_bytes(b"CHANGED")
        self.assertEqual(self.client.get("/api/same-asset-joint/" + doc["joint_id"]).status_code, 409)

    def test19_local_action_and_host_security_retained(self):
        body = {"case_id": self.case["case_id"], "value_run_id": RUN}
        self.assertEqual(self.client.post("/api/same-asset-joint", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/same-asset-joint", json=body,
            headers={**HEADERS, "Origin": "https://untrusted.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/same-asset-joint", headers={"Host": "evil.invalid"}).status_code, 403)

    def test20_strict_json_and_content_types(self):
        for raw in ('{"case_id":"a","case_id":"b","value_run_id":null}', '{"case_id":NaN,"value_run_id":null}'):
            result = self.client.post("/api/same-asset-joint", content=raw,
                headers={**HEADERS, "Content-Type": "application/json"})
            self.assertEqual(result.status_code, 422)
        self.assertEqual(self.client.post("/api/same-asset-joint", content="{}", headers=HEADERS).status_code, 415)

    def test21_missing_case_refuses_binding(self):
        self.assert_mismatch(self.post(case_id="case_" + "0" * 32))

    def test22_incomplete_run_not_evaluated(self):
        state = json.loads((self.folder / "status.json").read_bytes())
        state["status"] = "RUNNING"
        (self.folder / "status.json").write_bytes(canonical(state))
        (self.folder / "report.json").unlink()  # Disposable synthetic fixture only.
        result = self.post()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "NOT_EVALUATED")

    def test23_artifact_traversal_rejected(self):
        report = json.loads((self.folder / "report.json").read_bytes())
        report["artifacts"]["../../consumer.json"] = {"bytes": 0, "sha256": "0" * 64}
        (self.folder / "report.json").write_bytes(canonical(report))
        self.assert_mismatch(self.post())

    def test24_report_format_and_missing_identifier(self):
        doc = self.post().json()
        self.assertEqual(self.client.get("/api/same-asset-joint/" + doc["joint_id"] + "?format=exe").status_code, 422)
        self.assertEqual(self.client.get("/api/same-asset-joint/not-a-report").status_code, 404)

    def test25_invalid_case_expected_binding_and_oversize(self):
        self.assertEqual(self.post(case_id="../case").status_code, 422)
        self.assertEqual(self.post(expected_binding="FORGED").status_code, 422)
        self.assertEqual(self.client.post("/api/same-asset-joint", content=b"x" * 65537,
            headers={**HEADERS, "Content-Type": "application/json"}).status_code, 413)


    def test26_saved_record_array_is_controlled_mismatch(self):
        doc = self.post().json()
        path = self.app.state.same_asset_joint_root / (doc["joint_id"] + ".json")
        path.write_bytes(b"[]")
        self.assertEqual(self.client.get("/api/same-asset-joint/" + doc["joint_id"]).status_code, 409)

    def test27_corrupt_run_metadata_is_not_a_selectable_value(self):
        (self.folder / "status.json").write_bytes(b"[]")
        response = self.client.get("/api/same-asset-joint/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["value_runs"][0]["status"], "INVALID_SAVED_RECORD")


    def test28_append_validation_isolates_stale_list_entry(self):
        original = self.post().json()
        validated = self.client.post("/api/visual-cases/" + self.case["case_id"] + "/validate",
            json={"evidence_id": "fixture-image"}, headers=HEADERS)
        self.assertEqual(validated.status_code, 200, validated.text)
        # Pure append does not change original CSV/image, but changes the
        # current evidence snapshot. Strict GET continues to reject old joins.
        self.assertEqual(self.client.get("/api/same-asset-joint/" + original["joint_id"]).status_code, 409)
        response = self.client.get("/api/same-asset-joint")
        self.assertEqual(response.status_code, 200, response.text)
        stale = {r["joint_id"]: r for r in response.json()["reports"]}[original["joint_id"]]
        self.assertEqual(stale["status"], "SOURCE_CHANGED_OR_INVALID")
        self.assertTrue(stale["issues"])
        latest_response = self.post()
        self.assertEqual(latest_response.status_code, 200, latest_response.text)
        latest = latest_response.json()
        response = self.client.get("/api/same-asset-joint")
        self.assertEqual(response.status_code, 200, response.text)
        reports = {r["joint_id"]: r for r in response.json()["reports"]}
        self.assertEqual(reports[original["joint_id"]]["status"], "SOURCE_CHANGED_OR_INVALID")
        self.assertEqual(reports[latest["joint_id"]]["status"], "SAME_ASSET_BOUND")
        self.assertEqual(self.client.get("/api/same-asset-joint/" + latest["joint_id"]).status_code, 200)


if __name__ == "__main__":
    unittest.main()
