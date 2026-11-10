"""SYNTHETIC security and lifecycle fixtures; zero external model calls."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin
from data_asset_poc.visual_evidence_live.registry import Registry, RegistryError, canonical, digest


def image(color="red", *, metadata=None, format="PNG"):
    stream = io.BytesIO()
    im = Image.new("RGB", (8, 6), color)
    kwargs = {}
    if metadata is not None:
        info = PngImagePlugin.PngInfo()
        info.add_text("comment", metadata)
        kwargs["pnginfo"] = info
    im.save(stream, format=format, **kwargs)
    return stream.getvalue()


def payload(*, rows=b"row_id,name,appearance\n1,A,red\n2,B,blue\n", images=None, upload=False, version="v1"):
    images = images or [("red", image())]
    return {"asset_id": "synthetic-asset", "asset_version": version,
            "asset_sha256": digest(rows), "dataset_csv_base64": base64.b64encode(rows).decode(),
            "task_id": "appearance-test", "entity_key": "name", "entity_value": "A", "field": "appearance",
            "observation_target": "SYNTHETIC rectangular color fixture only",
            "observation_rule": "Report only visible predominant color; no real world fact inference.",
            "capture_time": None,
            "source": {"description": "SYNTHETIC generated test image", "url": "", "license": "test fixture",
                       "independence_declaration": "NOT_A_REAL_OBSERVATION"},
            "authorization": {"local_processing": True, "external_model_upload": upload},
            "evidence": [{"evidence_id": name, "filename": name + ".png", "sha256": digest(raw),
                          "data_base64": base64.b64encode(raw).decode()} for name, raw in images]}


class SyntheticAdapter:
    """Controlled adapter stub. Its receipts explicitly say synthetic=True."""
    def __init__(self, outcome="MATCH", failure=None):
        self.calls = 0
        self.outcome = outcome
        self.failure = failure
        self.inputs = []

    def availability(self):
        return {"available": True, "provider": "SYNTHETIC_NO_EXTERNAL_CALL", "model": "synthetic-unit-test"}

    def review(self, raw, context):
        self.calls += 1
        self.inputs.append((raw, context))
        if self.failure:
            raise self.failure
        return {"outcome": self.outcome, "observation_basis": "SYNTHETIC deterministic test output",
                "visible_features": ["test color"], "competing_interpretations": [], "uncertainty_reason": "Not real evidence",
                "receipt": {"provider": "SYNTHETIC_NO_EXTERNAL_CALL", "model": "synthetic-unit-test",
                            "request_id": "synthetic-receipt-" + str(self.calls), "input_sha256": digest(raw),
                            "called_at": datetime.now(timezone.utc).isoformat(), "execution_mode": "LIVE",
                            "usage": None, "synthetic": True}}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visual_evidence_live_registry_synthetic_")
        self.root = Path(self.temp.name)
        self.registry = Registry(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def register(self, **kwargs):
        return self.registry.register_case(payload(**kwargs))

    def request(self, case, **kwargs):
        return {"mode": "LIVE", "asset_sha256": case["asset"]["asset_sha256"],
                "asset_version": case["asset"]["asset_version"], "evidence_id": case["evidence"][0]["evidence_id"],
                "external_upload_authorized": True, **kwargs}

    def assert_error(self, code, function, *args):
        with self.assertRaises(RegistryError) as caught:
            function(*args)
        self.assertEqual(code, caught.exception.code)

    def test_register_exact_dataset_schema_entity_and_unknown(self):
        case = self.register()
        self.assertEqual(2, case["dataset"]["row_count"])
        self.assertEqual(0, case["dataset"]["matched_row_position"])
        self.assertEqual("UNKNOWN", case["assessment"]["fact_status"])
        self.assertEqual(1, case["assessment"]["unknown_or_unconfirmed_entity_count"])
        self.assertEqual("NOT_EVALUATED", case["assessment"]["task_value"]["status"])
        self.assertIsNone(case["assessment"]["population_error_interval"])
        self.assertEqual([case], self.registry.list_cases())

    def test_asset_hash_mismatch_rejected(self):
        p = payload()
        p["asset_sha256"] = "a" * 64
        self.assert_error("ASSET_SHA256_MISMATCH", self.registry.register_case, p)

    def test_unknown_field_cannot_self_report_authenticated_source(self):
        p = payload()
        p["validation_receipt"] = "valid-trust-me"
        self.assert_error("UNKNOWN_CASE_FIELDS", self.registry.register_case, p)

    def test_source_receipt_cannot_elevate(self):
        p = payload()
        p["source"]["validation_receipt"] = "trusted-signature"
        self.assert_error("SOURCE_DECLARATION_INVALID", self.registry.register_case, p)

    def test_image_receipt_cannot_elevate(self):
        p = payload()
        p["evidence"][0]["validation_receipt"] = "anything"
        self.assert_error("EVIDENCE_FIELDS_INVALID_SELF_REPORTED_RECEIPTS_NOT_ACCEPTED", self.registry.register_case, p)

    def test_invalid_image_magic_not_sufficient(self):
        p = payload(images=[("fake", b"\x89PNG\r\n\x1a\nNOT_IMAGE")])
        self.assert_error("IMAGE_DECODE_FAILED", self.registry.register_case, p)

    def test_evidence_hash_mismatch(self):
        p = payload()
        p["evidence"][0]["sha256"] = "a" * 64
        self.assert_error("EVIDENCE_SHA256_MISMATCH", self.registry.register_case, p)

    def test_filename_traversal_rejected(self):
        p = payload()
        p["evidence"][0]["filename"] = "../image.png"
        self.assert_error("SAFE_FILENAME_REQUIRED", self.registry.register_case, p)

    def test_extension_content_mismatch(self):
        p = payload()
        p["evidence"][0]["filename"] = "photo.jpeg"
        self.assert_error("IMAGE_EXTENSION_CONTENT_MISMATCH", self.registry.register_case, p)

    def test_duplicate_evidence_id_rejected(self):
        self.assert_error("DUPLICATE_EVIDENCE_ID", self.registry.register_case, payload(images=[("a", image()), ("a", image("blue"))]))

    def test_image_pixel_limit_before_decode(self):
        with patch("data_asset_poc.visual_evidence_live.registry.MAX_IMAGE_PIXELS", 10):
            self.assert_error("IMAGE_PIXEL_LIMIT", self.registry.register_case, payload())

    def test_csv_size_limit(self):
        with patch("data_asset_poc.visual_evidence_live.registry.MAX_CSV_BYTES", 8):
            self.assert_error("CSV_SIZE_LIMIT", self.registry.register_case, payload())

    def test_duplicate_headers_and_bad_column_count(self):
        self.assert_error("CSV_SCHEMA_INVALID", self.registry.register_case, payload(rows=b"name,name,appearance\nA,A,red\n"))
        self.assert_error("CSV_ROWS_INVALID", self.registry.register_case, payload(rows=b"name,appearance\nA\n"))

    def test_key_missing_and_ambiguous(self):
        self.assert_error("ENTITY_MATCH_MUST_EXIST_AND_BE_UNIQUE", self.registry.register_case,
                          payload(rows=b"row_id,name,appearance\n1,A,red\n2,A,blue\n"))
        self.assert_error("ENTITY_MATCH_MUST_EXIST_AND_BE_UNIQUE", self.registry.register_case,
                          payload(rows=b"row_id,name,appearance\n1,B,red\n"))

    def test_local_authorization_required_and_no_string_true(self):
        p = payload()
        p["authorization"]["local_processing"] = "true"
        self.assert_error("EXPLICIT_LOCAL_PROCESSING_AUTHORIZATION_REQUIRED", self.registry.register_case, p)

    def test_same_version_cannot_change_bytes(self):
        self.register()
        self.assert_error("ASSET_VERSION_ALREADY_BOUND_TO_DIFFERENT_BYTES", self.registry.register_case,
                          payload(rows=b"row_id,name,appearance\n1,A,green\n"))

    def test_new_version_no_old_reviews_or_receipts(self):
        first = self.register(upload=True)
        self.registry.execute(first["case_id"], self.request(first), SyntheticAdapter())
        second = self.register(rows=b"row_id,name,appearance\n1,A,green\n", version="v2")
        self.assertNotEqual(first["case_id"], second["case_id"])
        self.assertEqual([], second["reviews"])
        self.assertEqual([], second["validations"])
        self.assert_error("RUN_NOT_FOUND", self.registry.replay, second["case_id"], self.registry.get_case(first["case_id"])["executions"][0]["run_id"])

    def test_metadata_changed_image_deduplicates_pixels(self):
        first, second = image(metadata="first"), image(metadata="different metadata")
        self.assertNotEqual(digest(first), digest(second))
        case = self.register(images=[("one", first), ("two", second)])
        self.assertEqual(2, case["deduplication"]["unique_byte_count"])
        self.assertEqual(1, case["deduplication"]["unique_pixel_observation_count"])

    def test_same_image_resubmitted_does_not_create_observation(self):
        case = self.register(images=[("one", image()), ("two", image())])
        self.assertEqual(1, case["deduplication"]["unique_pixel_observation_count"])

    def test_local_validation_does_not_authenticate_source_or_facts(self):
        case = self.register()
        receipt = self.registry.validate_evidence(case["case_id"], "red")
        self.assertEqual("VALIDATED_LOCAL_INTEGRITY", receipt["status"])
        self.assertEqual("NOT_VERIFIED", receipt["source_identity_status"])
        self.assertEqual("UNKNOWN", receipt["fact_status"])
        self.assertIsNone(receipt["trusted_assertion"])

    def test_forged_persisted_receipt_even_with_recomputed_sha_rejected(self):
        case = self.register()
        self.registry.validate_evidence(case["case_id"], "red")
        path = next((self.root / "cases" / case["case_id"] / "validations").glob("*.json"))
        envelope = json.loads(path.read_text())
        envelope["payload"]["source_identity_status"] = "VERIFIED"
        envelope["payload"]["trusted_assertion"] = {"assertion": "SUPPORT"}
        envelope["local_mac"] = digest(canonical(envelope["payload"]))
        path.write_text(json.dumps(envelope))
        self.assert_error("LOCAL_RECORD_AUTHENTICATION_FAILED", self.registry.get_case, case["case_id"])

    def test_case_record_tamper_rejected(self):
        case = self.register()
        path = self.root / "cases" / case["case_id"] / "case.json"
        envelope = json.loads(path.read_text())
        envelope["payload"]["protocol"]["observation_rule"] = "tampered"
        path.write_text(json.dumps(envelope))
        self.assert_error("LOCAL_RECORD_AUTHENTICATION_FAILED", self.registry.get_case, case["case_id"])

    def test_raw_asset_and_image_tamper_rejected(self):
        case = self.register()
        path = self.root / "cases" / case["case_id"]
        original = (path / "dataset.csv").read_bytes()
        (path / "dataset.csv").write_bytes(original + b"\n")
        self.assert_error("ASSET_BYTES_CHANGED", self.registry.get_case, case["case_id"])
        (path / "dataset.csv").write_bytes(original)
        (path / case["evidence"][0]["stored_name"]).write_bytes(image("blue"))
        self.assert_error("EVIDENCE_BYTES_CHANGED", self.registry.get_case, case["case_id"])

    def test_real_configured_source_workflow_still_no_fact_assertion(self):
        registry = Registry(self.root, trusted_verifier=lambda context, raw: {
            "source_identity_verified": True, "relevance_verified": False,
            "verifier_id": "SYNTHETIC_LOCAL_WORKFLOW", "scope": "Test acquisition provenance only"})
        case = registry.register_case(payload())
        receipt = registry.validate_evidence(case["case_id"], "red")
        self.assertEqual("VERIFIED_BY_CONFIGURED_LOCAL_WORKFLOW", receipt["source_identity_status"])
        self.assertIsNone(receipt["trusted_assertion"])
        self.assertEqual("UNKNOWN", registry.get_case(case["case_id"])["assessment"]["fact_status"])

    def test_two_authenticated_opposed_distinct_evidence_allow_conflict_only(self):
        def verifier(context, raw):
            return {"source_identity_verified": True, "relevance_verified": True,
                    "verifier_id": "SYNTHETIC_TEST_VERIFIER", "scope": "SYNTHETIC color fixture only",
                    "proposition_sha256": context["proposition_sha256"],
                    "assertion": "SUPPORT" if context["evidence"]["evidence_id"] == "red" else "CONTRADICT", "synthetic": True}
        registry = Registry(self.root, trusted_verifier=verifier)
        case = registry.register_case(payload(images=[("red", image()), ("blue", image("blue"))]))
        registry.validate_evidence(case["case_id"], "red")
        self.assertNotEqual("EVIDENCE_CONFLICT", registry.get_case(case["case_id"])["coordination_state"])
        registry.validate_evidence(case["case_id"], "blue")
        self.assertEqual("EVIDENCE_CONFLICT", registry.get_case(case["case_id"])["coordination_state"])
        self.assertEqual("UNKNOWN", registry.get_case(case["case_id"])["assessment"]["fact_status"])

    def test_opposed_claims_on_same_pixels_not_distinct_evidence(self):
        def verifier(context, raw):
            return {"source_identity_verified": True, "relevance_verified": True,
                    "verifier_id": "SYNTHETIC", "scope": "SYNTHETIC only", "synthetic": True,
                    "proposition_sha256": context["proposition_sha256"],
                    "assertion": "SUPPORT" if context["evidence"]["evidence_id"] == "one" else "CONTRADICT"}
        registry = Registry(self.root, trusted_verifier=verifier)
        case = registry.register_case(payload(images=[("one", image(metadata="1")), ("two", image(metadata="2"))]))
        registry.validate_evidence(case["case_id"], "one")
        registry.validate_evidence(case["case_id"], "two")
        self.assertNotEqual("EVIDENCE_CONFLICT", registry.get_case(case["case_id"])["coordination_state"])

    def test_trusted_callback_wrong_proposition_rejected(self):
        registry = Registry(self.root, trusted_verifier=lambda context, raw: {
            "source_identity_verified": True, "relevance_verified": True, "assertion": "SUPPORT",
            "proposition_sha256": "a" * 64, "verifier_id": "SYNTHETIC", "scope": "SYNTHETIC"})
        case = registry.register_case(payload())
        self.assert_error("TRUSTED_ASSERTION_NOT_BOUND_OR_AUTHENTICATED", registry.validate_evidence, case["case_id"], "red")

    def test_no_adapter_unavailable_persisted_no_replay(self):
        case = self.register()
        result = self.registry.execute(case["case_id"], self.request(case))
        self.assertEqual("UNAVAILABLE", result["status"])
        self.assertFalse(result["model_invoked"])
        self.assertFalse(result["fallback_performed"])
        self.assertEqual("LIVE_UNAVAILABLE", result["reason"])
        self.assertEqual(1, len(self.registry.get_case(case["case_id"])["executions"]))
        self.assert_error("REPLAY_REQUIRES_ACTUAL_REVIEW_RESULT", self.registry.replay, case["case_id"], result["run_id"])

    def test_both_authorizations_required_before_adapter_call(self):
        adapter = SyntheticAdapter()
        no_registration_consent = self.register(upload=False)
        self.assert_error("EXTERNAL_UPLOAD_NOT_AUTHORIZED", self.registry.execute,
                          no_registration_consent["case_id"], self.request(no_registration_consent), adapter)
        case = self.register(upload=True)
        self.assert_error("EXTERNAL_UPLOAD_NOT_AUTHORIZED", self.registry.execute,
                          case["case_id"], self.request(case, external_upload_authorized=False), adapter)
        self.assertEqual(0, adapter.calls)

    def test_wrong_version_or_hash_blocks_model_call(self):
        case = self.register(upload=True)
        adapter = SyntheticAdapter()
        self.assert_error("ASSET_VERSION_MISMATCH", self.registry.execute, case["case_id"], self.request(case, asset_version="v2"), adapter)
        self.assert_error("ASSET_SHA256_MISMATCH", self.registry.execute, case["case_id"], self.request(case, asset_sha256="b" * 64), adapter)
        self.assertEqual(0, adapter.calls)

    def test_synthetic_success_and_repeat_does_not_add_physical_sample(self):
        case = self.register(upload=True)
        adapter = SyntheticAdapter()
        result = self.registry.execute(case["case_id"], self.request(case), adapter)
        self.assertTrue(result["synthetic"])
        self.assertEqual("AI", result["review"]["reviewer_type"])
        self.assertEqual(case["asset"], adapter.inputs[0][1]["asset"])
        self.assertEqual(case["asset"]["asset_sha256"], adapter.inputs[0][1]["asset_sha256"])
        self.registry.execute(case["case_id"], self.request(case), adapter)
        updated = self.registry.get_case(case["case_id"])
        self.assertEqual(2, updated["deduplication"]["review_count"])
        self.assertEqual(1, updated["deduplication"]["unique_pixel_observation_count"])
        self.assertEqual(0, updated["deduplication"]["additional_physical_observations_from_repeat_reviews"])
        self.assertEqual("UNKNOWN", updated["assessment"]["fact_status"])

    def test_opposite_ai_opinions_do_not_make_authenticated_conflict(self):
        case = self.register(upload=True)
        self.registry.execute(case["case_id"], self.request(case), SyntheticAdapter("MATCH"))
        self.registry.execute(case["case_id"], self.request(case), SyntheticAdapter("MISMATCH"))
        self.assertEqual("REVIEW_DISAGREEMENT", self.registry.get_case(case["case_id"])["coordination_state"])

    def test_adapter_failure_and_timeout_persist_no_fallback(self):
        case = self.register(upload=True)
        for failure, expected in [(RuntimeError("do not persist secret detail"), "ADAPTER_ERROR"), (TimeoutError("secret"), "TIMEOUT")]:
            adapter = SyntheticAdapter(failure=failure)
            result = self.registry.execute(case["case_id"], self.request(case), adapter)
            self.assertEqual("FAILED", result["status"])
            self.assertEqual(expected, result["error_type"])
            self.assertFalse(result["fallback_performed"])
            self.assertIsNone(result["request_attempted"])
            self.assertIsNone(result["model_invoked"])
            self.assertTrue(result["adapter_invocation_attempted"])
            self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(2, len(self.registry.get_case(case["case_id"])["executions"]))

    def test_invalid_model_receipt_cannot_create_review(self):
        case = self.register(upload=True)
        for change in [lambda r: r["receipt"].update(input_sha256="a" * 64),
                       lambda r: r["receipt"].update(execution_mode="REPLAY"),
                       lambda r: r["receipt"].update(model="another-model"),
                       lambda r: r.update(outcome="TRUE"),
                       lambda r: r.pop("receipt")]:
            adapter = SyntheticAdapter()
            original = adapter.review
            def changed(raw, context):
                result = original(raw, context)
                change(result)
                return result
            adapter.review = changed
            result = self.registry.execute(case["case_id"], self.request(case), adapter)
            self.assertEqual("FAILED", result["status"])
            self.assertIsNone(result["review"])
        self.assertEqual([], self.registry.get_case(case["case_id"])["reviews"])

    def test_replay_does_not_call_or_count_again(self):
        case = self.register(upload=True)
        adapter = SyntheticAdapter()
        run = self.registry.execute(case["case_id"], self.request(case), adapter)
        replay = self.registry.execute(case["case_id"], self.request(case, mode="REPLAY", run_id=run["run_id"]), adapter)
        self.assertEqual("REPLAY", replay["status"])
        self.assertFalse(replay["model_invoked"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual(1, len(self.registry.get_case(case["case_id"])["reviews"]))
        self.assertEqual(run["result_sha256"], replay["original_result_sha256"])
        self.assertNotEqual(run["result_sha256"], replay["result_sha256"])
        self.assertEqual(digest(canonical({k: v for k, v in replay.items() if k != "result_sha256"})), replay["result_sha256"])
        self.assertEqual(run, self.registry.get_case(case["case_id"])["executions"][0])

    def test_safe_failure_receipt_preserved_but_secret_fields_omitted(self):
        from data_asset_poc.visual_evidence_live.adapters import AdapterTimeout
        case = self.register(upload=True)
        error = AdapterTimeout({"input_sha256": case["evidence"][0]["sha256"],
                                "protocol_sha256": case["protocol_sha256"], "call_id": "synthetic-call",
                                "elapsed_seconds": 1.0, "request_attempted": True, "model_invoked": False,
                                "api_key": "SYNTHETIC_SECRET_MUST_NOT_APPEAR", "env": {"secret": "discard"}})
        result = self.registry.execute(case["case_id"], self.request(case), SyntheticAdapter(failure=error))
        self.assertEqual("synthetic-call", result["receipt"]["call_id"])
        self.assertEqual(1.0, result["receipt"]["elapsed_seconds"])
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))
        self.assertNotIn("env", result["receipt"])

    def test_failure_receipt_tristate_not_confused_with_unavailable(self):
        from data_asset_poc.visual_evidence_live.adapters import AdapterError
        case = self.register(upload=True)
        for invoked, attempted in [(True, True), (None, True), (False, False)]:
            error = AdapterError("SYNTHETIC_FAILURE", {
                "input_sha256": case["evidence"][0]["sha256"], "protocol_sha256": case["protocol_sha256"],
                "model_invoked": invoked, "request_attempted": attempted, "synthetic": True})
            result = self.registry.execute(case["case_id"], self.request(case), SyntheticAdapter(failure=error))
            self.assertIs(result["model_invoked"], invoked)
            self.assertIs(result["request_attempted"], attempted)
            self.assertEqual("FAILED", result["status"])
            self.assertFalse(result["fallback_performed"])
        unavailable = self.registry.execute(case["case_id"], self.request(case))
        self.assertEqual("UNAVAILABLE", unavailable["status"])
        self.assertIs(unavailable["model_invoked"], False)
        self.assertIs(unavailable["request_attempted"], False)

    def test_protocol_mismatch_model_result_or_receipt_rejected(self):
        case = self.register(upload=True)
        for where in ("output", "receipt"):
            adapter = SyntheticAdapter()
            original = adapter.review
            def changed(raw, context):
                result = original(raw, context)
                (result if where == "output" else result["receipt"])["protocol_sha256"] = "a" * 64
                return result
            adapter.review = changed
            result = self.registry.execute(case["case_id"], self.request(case), adapter)
            self.assertEqual("FAILED", result["status"])
            self.assertIsNone(result["review"])

    def test_replay_rejects_different_image_selected(self):
        case = self.register(upload=True, images=[("red", image()), ("blue", image("blue"))])
        run = self.registry.execute(case["case_id"], self.request(case), SyntheticAdapter())
        self.assert_error("EVIDENCE_ID_MISMATCH", self.registry.execute, case["case_id"],
                          self.request(case, mode="REPLAY", run_id=run["run_id"], evidence_id="blue"))

    def test_replay_rejects_nonexistent_image_selected(self):
        case = self.register(upload=True)
        run = self.registry.execute(case["case_id"], self.request(case), SyntheticAdapter())
        self.assert_error("EVIDENCE_NOT_FOUND", self.registry.execute, case["case_id"],
                          self.request(case, mode="REPLAY", run_id=run["run_id"], evidence_id="missing"))

    def test_row_id_does_not_hide_content_duplicates(self):
        case = self.register(rows=b"row_id,name,appearance\n1,A,red\n2,B,blue\n3,B,blue\n")
        self.assertEqual(0, case["dataset"]["registry_whole_row_duplicate_count"])
        self.assertEqual(1, case["dataset"]["content_duplicate_count_excluding_system_ids"])
        self.assertIsNone(case["dataset"]["task_feature_duplicate_count"])

    def test_parallel_registration_append_only(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            cases = list(pool.map(lambda _: Registry(self.root).register_case(payload()), range(12)))
        self.assertEqual(12, len({c["case_id"] for c in cases}))
        self.assertEqual(12, len(self.registry.list_cases()))

    def test_parallel_execution_and_validation_no_lost_records(self):
        case = self.register(upload=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            runs = list(pool.map(lambda _: Registry(self.root).execute(case["case_id"], self.request(case), SyntheticAdapter()), range(8)))
        self.assertEqual(8, len({r["run_id"] for r in runs}))
        self.assertEqual(8, len(self.registry.get_case(case["case_id"])["reviews"]))
        self.assertEqual(8, len(self.registry.get_case(case["case_id"])["validations"]))

    def test_restarted_registry_authenticates_existing_records(self):
        case = self.register()
        self.assertEqual(case, Registry(self.root).get_case(case["case_id"]))

    def test_chinese_report_contains_unknown_and_version_sources(self):
        case = self.register()
        report = self.registry.report(case["case_id"])
        self.assertIn(case["asset"]["asset_sha256"], report["report_markdown"])
        self.assertIn("UNKNOWN", report["report_markdown"])
        self.assertIn("NOT_EVALUATED", report["report_markdown"])


if __name__ == "__main__":
    unittest.main()
