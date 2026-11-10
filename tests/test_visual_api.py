"""Actual local TestClient/Registry tests, with explicitly synthetic adapters.

Every generated CSV/image/model result is a software boundary fixture, never
research observation, independent fact evidence or a real provider invocation.
Only the old workbench shell is stubbed; legacy CSV/trust routes remain real.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from data_asset_poc.self_service_value import gateway as legacy
from data_asset_poc.visual_evidence_live import gateway


HEADERS = {"X-Research-Request": "1", "Origin": "http://localhost:12350"}
JSON_HEADERS = {**HEADERS, "Content-Type": "application/json"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def old_app():
    app = FastAPI()

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.headers.get("host", "").split(":")[0] not in {"testserver", "localhost", "127.0.0.1"}:
            return JSONResponse(status_code=403, content={"detail": "LOCAL_HOST_REQUIRED"})
        if request.method == "POST" and (request.headers.get("x-research-request") != "1"
                or request.headers.get("origin") not in {None, "http://localhost:12350"}):
            return JSONResponse(status_code=403, content={"detail": "LOCAL_ACTION_REQUIRED"})
        response = await call_next(request)
        response.headers["X-Inherited-Security"] = "retained"
        return response

    @app.get("/api/workbench/options")
    def old_options():
        return {"status": "HISTORICAL_ROUTE_RETAINED"}

    return app


class SyntheticAdapter:
    """No network, no model: every success receipt is marked synthetic."""
    def __init__(self, outcome="MATCH", failure=None):
        self.outcome = outcome
        self.failure = failure
        self.calls = []

    def availability(self):
        return {"available": True, "reason": "SYNTHETIC_TEST_ONLY", "provider": "SYNTHETIC",
            "model": "SYNTHETIC_NO_MODEL", "adapter_type": "synthetic_test"}

    def review(self, raw, context):
        self.calls.append((raw, copy.deepcopy(context)))
        if self.failure is not None:
            raise self.failure
        return {"outcome": self.outcome, "observation_basis": "合成软件边界夹具；不是事实观测。",
            "visible_features": ["SYNTHETIC"], "competing_interpretations": [], "uncertainty_reason": "NO_REAL_MODEL",
            "receipt": {"provider": "SYNTHETIC", "model": "SYNTHETIC_NO_MODEL", "request_id": "SYNTHETIC_REQUEST",
                "called_at": "2026-10-25T00:00:00+00:00", "input_sha256": sha(raw),
                "execution_mode": "LIVE", "usage": None, "synthetic": True}}


class GeneralVisualAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="visual_evidence_live_api_SYNTHETIC_")
        self.workspace = Path(self.tmp.name)
        self.config = self.workspace / "consumer.json"
        self.config.write_text(json.dumps({"workspace": str(self.workspace)}), encoding="utf-8")
        image = io.BytesIO()
        Image.new("RGB", (12, 9), (12, 34, 56)).save(image, format="PNG")
        self.png = image.getvalue()
        self.csv = b"entity,attribute\nfixture_1,declared_only\nfixture_2,unknown\n"
        self.payload = {"asset_id": "SYNTHETIC_DATASET", "asset_version": "synthetic-v1",
            "asset_sha256": sha(self.csv), "dataset_csv_base64": base64.b64encode(self.csv).decode("ascii"),
            "task_id": "synthetic-observation", "entity_key": "entity", "entity_value": "fixture_1",
            "field": "attribute", "observation_target": "合成夹具的局部可见属性，不是现实事实。",
            "observation_rule": "只输出四态；没有相应可见证据必须INCOMPARABLE。", "capture_time": None,
            "source": {"description": "SYNTHETIC_TEST_ONLY", "url": "", "license": "test fixture",
                "independence_declaration": "NOT_INDEPENDENT_FACT_EVIDENCE"},
            "authorization": {"local_processing": True, "external_model_upload": False},
            "evidence": [{"evidence_id": "synthetic-image", "filename": "fixture.png", "sha256": sha(self.png),
                "data_base64": base64.b64encode(self.png).decode("ascii")} ]}
        self.base = old_app()
        self.patches = [patch.object(legacy, "create_workbench_gateway", return_value=self.base),
            patch("data_asset_poc.visual_evidence_live.adapters.configured_adapter", return_value=None)]
        for replacement in self.patches:
            replacement.start()
        self.app = gateway.create_live_gateway(self.config, Path(__file__).resolve().parents[1],
            registry_root=self.workspace / "new-cases")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        for replacement in reversed(self.patches):
            replacement.stop()
        self.tmp.cleanup()

    def register(self, payload=None):
        response = self.client.post("/api/visual-cases", json=payload or self.payload, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def execution(self, case, **changes):
        return {"mode": "LIVE", "asset_sha256": case["asset"]["asset_sha256"],
            "asset_version": case["asset"]["asset_version"], "evidence_id": "synthetic-image",
            "external_upload_authorized": False, **changes}

    def execute(self, case, **changes):
        return self.client.post("/api/visual-cases/" + case["case_id"] + "/execute",
            json=self.execution(case, **changes), headers=HEADERS)

    def live_case(self, outcome="MATCH", failure=None):
        payload = copy.deepcopy(self.payload)
        payload["authorization"]["external_model_upload"] = True
        case = self.register(payload)
        self.app.state.visual_case_adapter = SyntheticAdapter(outcome, failure)
        return case

    def test_01_inherits_routes_and_real_legacy_csv_registration(self):
        self.assertIs(self.app, self.base)
        response = self.client.get("/api/workbench/options")
        self.assertEqual(response.json()["status"], "HISTORICAL_ROUTE_RETAINED")
        self.assertEqual(response.headers["X-Inherited-Security"], "retained")
        for path in ("/api/self-service/options", "/api/self-service/datasets"):
            self.assertTrue(any(route.path == path for route in self.app.routes), path)
        self.assertFalse(any(route.path.startswith("/api/visual-review/") for route in self.app.routes))
        self.assertTrue(any(route.path == "/api/evidence/plans" for route in self.app.routes))
        response = self.client.post("/api/self-service/datasets", content=self.csv,
            headers={**HEADERS, "Content-Type": "text/csv"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["content_hash"], sha(self.csv))
        self.assertEqual(self.client.get("/api/self-service/options").status_code, 200)

    def test_02_local_middleware_remains_on_new_routes(self):
        self.assertEqual(self.client.post("/api/visual-cases", json=self.payload).status_code, 403)
        self.assertEqual(self.client.post("/api/visual-cases", json=self.payload,
            headers={**HEADERS, "Origin": "https://untrusted.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/visual-cases", headers={"Host": "evil.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/visual-cases").headers["X-Inherited-Security"], "retained")

    def test_03_capabilities_honestly_unavailable(self):
        result = self.client.get("/api/visual-cases/capabilities").json()
        self.assertFalse(result["live_available"])
        self.assertEqual(result["reason"], "LIVE_UNAVAILABLE")
        self.assertEqual(result["limits"]["request_bytes"], 30 * 1024 * 1024)
        self.assertEqual(result["fact_status_without_independent_confirmation"], "UNKNOWN")

    def test_04_registration_version_unknown_and_no_automatic_value(self):
        case = self.register()
        self.assertEqual(case["asset"]["asset_sha256"], sha(self.csv))
        self.assertEqual(case["assessment"]["fact_status"], "UNKNOWN")
        self.assertEqual(case["assessment"]["task_value"]["status"], "NOT_EVALUATED")
        self.assertEqual(case["source_status"], "DECLARED_NOT_AUTHENTICATED")
        listing = self.client.get("/api/visual-cases").json()["cases"]
        self.assertEqual([v["case_id"] for v in listing], [case["case_id"]])
        self.assertEqual(self.client.get("/api/visual-cases/" + case["case_id"]).status_code, 200)

    def test_05_image_bytes_are_actually_hashed_and_bound(self):
        case = self.register()
        url = "/api/visual-cases/" + case["case_id"] + "/evidence/synthetic-image"
        response = self.client.get(url)
        self.assertEqual(response.content, self.png)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(response.headers["X-Evidence-SHA256"], sha(self.png))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get(url, params={"asset_sha256": "f" * 64}).status_code, 409)

    def test_06_local_receipt_is_not_source_or_fact_authentication(self):
        case = self.register()
        url = "/api/visual-cases/" + case["case_id"] + "/validate"
        response = self.client.post(url, json={"evidence_id": "synthetic-image"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "VALIDATED_LOCAL_INTEGRITY")
        self.assertEqual(response.json()["source_identity_status"], "NOT_VERIFIED")
        self.assertEqual(response.json()["fact_status"], "UNKNOWN")
        self.assertEqual(self.client.post(url, json={"evidence_id": "synthetic-image",
            "validation_receipt": "forged"}, headers=HEADERS).status_code, 422)

    def test_07_unavailable_is_persisted_and_never_replay(self):
        case = self.register()
        response = self.execute(case)
        self.assertEqual(response.status_code, 503, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["status"], "UNAVAILABLE")
        self.assertFalse(detail["model_invoked"])
        self.assertFalse(detail["fallback_performed"])
        self.assertEqual(detail["execution"]["reason"], "LIVE_UNAVAILABLE")
        current = self.client.get("/api/visual-cases/" + case["case_id"]).json()
        self.assertEqual(len(current["executions"]), 1)
        self.assertEqual(current["reviews"], [])

    def test_08_synthetic_success_receipt_and_explicit_replay(self):
        case = self.live_case()
        response = self.execute(case, external_upload_authorized=True)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "LIVE")
        self.assertTrue(result["receipt"]["synthetic"])
        self.assertEqual(result["receipt"]["input_sha256"], sha(self.png))
        self.assertEqual(result["review"]["reviewer_type"], "AI")
        adapter = self.app.state.visual_case_adapter
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0][0], self.png)
        replay = self.execute(case, mode="REPLAY", run_id=result["run_id"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["execution_mode"], "REPLAY")
        self.assertFalse(replay.json()["model_invoked"])
        self.assertEqual(len(adapter.calls), 1)

    def test_09_failed_call_is_502_and_saved_without_replay(self):
        case = self.live_case(failure=ValueError("SYNTHETIC_FAILURE_WITH_PRIVATE_DETAILS"))
        response = self.execute(case, external_upload_authorized=True)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn("SYNTHETIC_FAILURE_WITH_PRIVATE_DETAILS", response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["execution"]["status"], "FAILED")
        self.assertFalse(detail["fallback_performed"])
        self.assertIsNone(detail["execution"]["review"])

    def test_10_timeout_is_504_and_saved(self):
        case = self.live_case(failure=TimeoutError("SYNTHETIC_TIMEOUT"))
        response = self.execute(case, external_upload_authorized=True)
        self.assertEqual(response.status_code, 504, response.text)
        self.assertEqual(response.json()["detail"]["error_type"], "TIMEOUT")
        current = self.client.get("/api/visual-cases/" + case["case_id"]).json()
        self.assertEqual(current["executions"][0]["status"], "FAILED")

    def test_11_upload_needs_registered_and_per_call_authorization(self):
        case = self.register()
        adapter = SyntheticAdapter()
        self.app.state.visual_case_adapter = adapter
        self.assertEqual(self.execute(case, external_upload_authorized=True).status_code, 422)
        self.assertEqual(adapter.calls, [])
        case = self.live_case()
        self.assertEqual(self.execute(case).status_code, 422)
        self.assertEqual(self.app.state.visual_case_adapter.calls, [])

    def test_12_duplicate_reviews_do_not_create_physical_observations(self):
        case = self.live_case()
        for _ in range(2):
            self.assertEqual(self.execute(case, external_upload_authorized=True).status_code, 200)
        current = self.client.get("/api/visual-cases/" + case["case_id"]).json()
        self.assertEqual(len(current["reviews"]), 2)
        self.assertEqual(current["deduplication"]["unique_pixel_observation_count"], 1)
        self.assertEqual(current["deduplication"]["additional_physical_observations_from_repeat_reviews"], 0)
        self.assertEqual(current["assessment"]["unknown_or_unconfirmed_entity_count"], 1)
        self.assertIsNone(current["assessment"]["population_error_interval"])

    def test_13_disagreement_is_not_authenticated_evidence_conflict(self):
        case = self.live_case()
        self.assertEqual(self.execute(case, external_upload_authorized=True).status_code, 200)
        self.app.state.visual_case_adapter.outcome = "INCOMPARABLE"
        self.assertEqual(self.execute(case, external_upload_authorized=True).status_code, 200)
        current = self.client.get("/api/visual-cases/" + case["case_id"]).json()
        self.assertEqual(current["coordination_state"], "REVIEW_DISAGREEMENT")
        self.assertEqual(current["assessment"]["fact_status"], "UNKNOWN")

    def test_14_source_evidence_and_execution_cannot_inject_trust_or_model(self):
        variants = []
        for position in (None, "source", "authorization"):
            payload = copy.deepcopy(self.payload)
            (payload if position is None else payload[position])["validation_receipt"] = "self-reported"
            variants.append(payload)
        payload = copy.deepcopy(self.payload)
        payload["evidence"][0]["validation_receipt"] = "self-reported"
        variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/visual-cases", json=payload, headers=HEADERS).status_code, 422)
        case = self.register()
        for key in ("adapter", "model", "trusted_verifier", "validation_receipt", "receipt", "result"):
            with self.subTest(key=key):
                self.assertEqual(self.execute(case, **{key: "forged"}).status_code, 422)

    def test_15_nested_duplicate_keys_nan_and_overflow_rejected_without_write(self):
        raw_cases = [b"{", b"[]", b"null", b"\xff", b'{"source":{"a":1,"a":2}}',
            b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}', b'{"value":1e999}']
        for raw in raw_cases:
            with self.subTest(raw=raw):
                result = self.client.post("/api/visual-cases", content=raw, headers=JSON_HEADERS)
                self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(self.client.get("/api/visual-cases").json()["cases"], [])

    def test_16_content_length_and_encoding_are_checked(self):
        for value in ("-1", "bad", "9" * 100):
            response = self.client.post("/api/visual-cases", content=b"{}",
                headers={**JSON_HEADERS, "Content-Length": value})
            self.assertEqual(response.status_code, 400, response.text)
        response = self.client.post("/api/visual-cases", content=b"{}",
            headers={**JSON_HEADERS, "Content-Length": str(gateway.MAX_REGISTRATION_BYTES + 1)})
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/visual-cases", content=b"{}",
            headers={**JSON_HEADERS, "Content-Length": "4"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/visual-cases", content=b"{}",
            headers={**JSON_HEADERS, "Content-Encoding": "gzip"}).status_code, 415)
        self.assertEqual(self.client.post("/api/visual-cases", content=b"{}", headers=HEADERS).status_code, 415)

    def test_17_streamed_size_limit_cannot_be_bypassed_without_content_length(self):
        with patch.object(gateway, "MAX_REGISTRATION_BYTES", 16):
            response = self.client.post("/api/visual-cases", content=iter([b"{" + b" " * 8, b" " * 8 + b"}"]),
                headers=JSON_HEADERS)
            self.assertEqual(response.status_code, 413, response.text)

    def test_18_version_queries_and_execution_do_not_reuse_wrong_version(self):
        case = self.register()
        prefix = "/api/visual-cases/" + case["case_id"]
        for path in ("", "/evidence/synthetic-image", "/report"):
            for parameter, value in (("asset_sha256", "f" * 64), ("asset_version", "new-version")):
                self.assertEqual(self.client.get(prefix + path, params={parameter: value}).status_code, 409)
        self.assertEqual(self.execute(case, asset_sha256="f" * 64).status_code, 409)
        self.assertEqual(self.execute(case, asset_version="new-version").status_code, 409)
        payload = copy.deepcopy(self.payload)
        payload["asset_version"] = "synthetic-v2"
        new = self.register(payload)
        self.assertNotEqual(new["case_id"], case["case_id"])
        self.assertEqual(new["reviews"], [])

    def test_19_same_named_version_cannot_change_bytes(self):
        self.register()
        changed = b"entity,attribute\nfixture_1,changed\n"
        payload = copy.deepcopy(self.payload)
        payload["dataset_csv_base64"] = base64.b64encode(changed).decode("ascii")
        payload["asset_sha256"] = sha(changed)
        response = self.client.post("/api/visual-cases", json=payload, headers=HEADERS)
        self.assertEqual(response.status_code, 409, response.text)

    def test_20_registration_rejects_hash_wrong_entity_url_and_paths(self):
        variants = [{"asset_sha256": "f" * 64}, {"entity_key": "wrong"}, {"entity_value": "missing"},
            {"dataset_csv_base64": "https://not-fetched.invalid/dataset.csv"}, {"path": "C:/private.csv"},
            {"url": "https://not-fetched.invalid/"}]
        for changes in variants:
            payload = {**copy.deepcopy(self.payload), **changes}
            self.assertIn(self.client.post("/api/visual-cases", json=payload, headers=HEADERS).status_code, {409, 422})
        for changes in ({"sha256": "f" * 64}, {"filename": "../private.png"}, {"data_base64": "https://not-fetched.invalid/image.png"}):
            payload = copy.deepcopy(self.payload)
            payload["evidence"][0].update(changes)
            self.assertIn(self.client.post("/api/visual-cases", json=payload, headers=HEADERS).status_code, {409, 422})

    def test_21_registered_bytes_tampering_blocks_read_and_execution(self):
        case = self.register()
        folder = self.app.state.visual_case_registry.root / "cases" / case["case_id"]
        image = next(folder.glob("image_*.png"))
        image.write_bytes(self.png + b"TAMPERED")
        prefix = "/api/visual-cases/" + case["case_id"]
        self.assertEqual(self.client.get(prefix).status_code, 409)
        self.assertEqual(self.client.get(prefix + "/evidence/synthetic-image").status_code, 409)
        self.assertEqual(self.execute(case).status_code, 409)

    def test_22_unknown_case_evidence_and_replay_are_not_found(self):
        self.assertEqual(self.client.get("/api/visual-cases/not-a-case").status_code, 404)
        case = self.register()
        prefix = "/api/visual-cases/" + case["case_id"]
        self.assertEqual(self.client.get(prefix + "/evidence/unknown").status_code, 404)
        self.assertEqual(self.execute(case, mode="REPLAY", run_id="run_" + "0" * 32).status_code, 404)

    def test_23_report_downloads_preserve_chinese_binding_and_unknown(self):
        case = self.register()
        prefix = "/api/visual-cases/" + case["case_id"] + "/report"
        md = self.client.get(prefix + "?format=md")
        self.assertEqual(md.status_code, 200, md.text)
        self.assertIn("数据集视觉证据", md.content.decode("utf-8"))
        self.assertIn("UNKNOWN", md.text)
        self.assertIn("NOT_EVALUATED", md.text)
        self.assertIn(sha(self.csv), md.text)
        self.assertIn("attachment", md.headers["Content-Disposition"])
        self.assertEqual(md.headers["X-Asset-SHA256"], sha(self.csv))
        result = self.client.get(prefix + "?format=json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["asset"], case["asset"])
        self.assertEqual(self.client.get(prefix + "?format=html").status_code, 422)

    def test_24_optional_outcomes_retain_unknown(self):
        payload = {**copy.deepcopy(self.payload), "allowed_outcomes": ["MATCH", "INCOMPARABLE"]}
        self.assertEqual(self.register(payload)["protocol"]["allowed_outcomes"], ["MATCH", "INCOMPARABLE"])
        payload["allowed_outcomes"] = ["MATCH"]
        self.assertEqual(self.client.post("/api/visual-cases", json=payload, headers=HEADERS).status_code, 422)

    def test_25_metadata_changes_do_not_duplicate_pixels(self):
        image = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("description", "SYNTHETIC metadata change")
        Image.new("RGB", (12, 9), (12, 34, 56)).save(image, format="PNG", pnginfo=info)
        changed = image.getvalue()
        self.assertNotEqual(sha(changed), sha(self.png))
        payload = copy.deepcopy(self.payload)
        payload["evidence"].append({"evidence_id": "metadata-copy", "filename": "copy.png",
            "sha256": sha(changed), "data_base64": base64.b64encode(changed).decode("ascii")})
        case = self.register(payload)
        self.assertEqual(case["deduplication"]["unique_byte_count"], 2)
        self.assertEqual(case["deduplication"]["unique_pixel_observation_count"], 1)

    def test_26_invalid_execution_fields_do_not_enter_adapter(self):
        case = self.live_case()
        for changes in ({"mode": "bad"}, {"mode": []}, {"asset_sha256": "bad"}, {"asset_version": ""},
                {"asset_version": 7}, {"external_upload_authorized": 1}, {"evidence_id": ""},
                {"run_id": "extra-on-live"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.execute(case, **changes).status_code, 422)
        self.assertEqual(self.app.state.visual_case_adapter.calls, [])

    def test_27_replay_cannot_be_requested_as_a_different_image(self):
        payload = copy.deepcopy(self.payload)
        payload["authorization"]["external_model_upload"] = True
        payload["evidence"].append({**copy.deepcopy(payload["evidence"][0]), "evidence_id": "existing-other-image"})
        case = self.register(payload)
        adapter = SyntheticAdapter()
        self.app.state.visual_case_adapter = adapter
        result = self.execute(case, external_upload_authorized=True).json()
        self.assertEqual(len(adapter.calls), 1)
        response = self.execute(case, mode="REPLAY", run_id=result["run_id"], evidence_id="missing-image")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("EVIDENCE_NOT_FOUND", response.text)
        response = self.execute(case, mode="REPLAY", run_id=result["run_id"], evidence_id="existing-other-image")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("EVIDENCE_ID_MISMATCH", response.text)
        self.assertEqual(len(adapter.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
