import copy
import unittest
from data_asset_poc.assessment.binding import NORMALIZATION_VERSION, asset_digest, json_digest
from data_asset_poc.assessment.decision import CALIBRATION_VERSION, FACT_VERSION, decide
from tests.test_asset_binding import make_asset


def fixture():
    asset = make_asset(); asset["records"][0]["x"] = [0.5, 0.0]
    digest = asset_digest(asset)
    context = {"task_sha256": asset["task_sha256"], "model_id": "demo-model", "model_version": "v1",
               "model_artifact_sha256": json_digest({"model": "v1"}), "budget_sha256": json_digest({"budget": 50})}
    fact = {"receipt_version": FACT_VERSION, "receipt_id": "synthetic-fact-1", "scope": "INDEPENDENT_FACTUAL_ORACLE",
            "outcome": "VERIFIED", "source_id": "oracle-b", "evidence_reference": "synthetic://fact-fixture",
            "audit_method": "synthetic deterministic fixture", "asset_sha256": digest, "task_sha256": context["task_sha256"],
            "risk_upper": 0.02, "known_conflict_lower_bound": 0.0}
    cal = {"receipt_version": CALIBRATION_VERSION, "receipt_id": "synthetic-cal-1", "status": "CALIBRATED_INDEPENDENTLY",
           "split": "INDEPENDENT_HELD_OUT", "asset_sha256": digest, "dataset_id": asset["dataset_id"],
           "dataset_version": asset["dataset_version"], "task_sha256": context["task_sha256"],
           "model_id": context["model_id"], "model_version": context["model_version"],
           "model_artifact_sha256": context["model_artifact_sha256"], "budget_sha256": context["budget_sha256"],
           "calibration_members_sha256": json_digest(["independent-c1", "independent-c2"]), "calibration_n": 2,
           "calibration_evidence_reference": "synthetic://calibration-fixture", "lcb": 0.2}
    cert = {"asset_sha256": digest, "binding_valid": True, "risk_upper": 0.02,
            "known_conflict_lower_bound": 0.0, "fact_receipt": fact}
    utility = {"status": "CALIBRATED_INDEPENDENTLY", **context, "lcb": 0.2, "calibration_receipt": cal}
    registry = {"fact": {fact["receipt_id"]: json_digest(fact)}, "calibration": {cal["receipt_id"]: json_digest(cal)}}
    return asset, context, cert, utility, registry


class FailClosedDecisionTests(unittest.TestCase):
    def setUp(self):
        self.a, self.ctx, self.cert, self.util, self.reg = fixture()

    def decision(self): return decide(self.a, self.ctx, self.cert, self.util, self.reg)
    def no_admit(self): self.assertNotEqual(self.decision()["decision"], "ADMIT")
    def register_fact(self): self.reg["fact"][self.cert["fact_receipt"]["receipt_id"]] = json_digest(self.cert["fact_receipt"])
    def register_cal(self): self.reg["calibration"][self.util["calibration_receipt"]["receipt_id"]] = json_digest(self.util["calibration_receipt"])

    def test_valid_synthetic_end_to_end_only(self):
        self.assertEqual(self.decision()["decision"], "ADMIT")
        # Synthetic receipt hashes demonstrate execution mechanics, never real calibration.

    def test_missing_none_false_and_numeric_binding_fail_closed(self):
        for v in [None, False, 0, 1, "True"]:
            with self.subTest(v=v): self.cert["binding_valid"] = v; self.no_admit()
        del self.cert["binding_valid"]; self.no_admit()

    def test_risk_upper_invalid_or_missing(self):
        for v in [None, float("nan"), float("inf"), float("-inf"), -0.1, 1.01, True, "0.02"]:
            with self.subTest(v=v): self.cert["risk_upper"] = v; self.no_admit()
        del self.cert["risk_upper"]; self.no_admit()

    def test_conflict_lower_bound_invalid_or_missing(self):
        for v in [None, float("nan"), float("inf"), -0.1, 1.01, True, 0.03]:
            with self.subTest(v=v): self.cert["known_conflict_lower_bound"] = v; self.no_admit()
        del self.cert["known_conflict_lower_bound"]; self.no_admit()

    def test_lcb_invalid_missing_nonpositive(self):
        for v in [None, float("nan"), float("inf"), float("-inf"), -0.1, 0, True, "0.2"]:
            with self.subTest(v=v): self.util["lcb"] = v; self.no_admit()
        del self.util["lcb"]; self.no_admit()

    def test_unauthorized_fact_and_same_source_never_admitted(self):
        self.cert["fact_receipt"] = None; self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.cert["fact_receipt"]["source_id"] = self.a["source_id"]; self.register_fact(); self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.cert["fact_receipt"]["evidence_reference"] = ""; self.register_fact(); self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.cert["fact_receipt"]["scope"] = "SAME_SOURCE_CONFORMITY"; self.register_fact(); self.no_admit()

    def test_same_source_with_whitespace_and_unicode_alias_fails(self):
        self.cert['fact_receipt']['source_id'] = ' publisher-a '; self.register_fact(); self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.a['source_id'] = 'café'; digest = asset_digest(self.a)
        self.cert['asset_sha256'] = digest; self.cert['fact_receipt']['asset_sha256'] = digest
        self.cert['fact_receipt']['source_id'] = 'cafe\u0301'; self.register_fact()
        self.util['calibration_receipt']['asset_sha256'] = digest; self.register_cal(); self.no_admit()

    def test_old_certificate_invalid_for_any_change(self):
        changes = [("group", "g-new"), ("source_row", 56), ("id", "c"), ("x", [8]), ("y", "C")]
        for field, value in changes:
            with self.subTest(field=field):
                self.a,self.ctx,self.cert,self.util,self.reg = fixture(); self.a["records"][0][field] = value; self.no_admit()
        for field in ["schema_sha256", "task_sha256", "license_sha256"]:
            with self.subTest(field=field):
                self.a,self.ctx,self.cert,self.util,self.reg = fixture(); self.a[field] = json_digest({"changed": field}); self.no_admit()

    def test_context_changed_without_recalibration_never_admitted(self):
        for field, value in [("model_id", "other"), ("model_version", "v2"),
                             ("model_artifact_sha256", json_digest({"other": "model"})),
                             ("budget_sha256", json_digest({"budget": 999})),
                             ("task_sha256", json_digest({"task": "other"}))]:
            with self.subTest(field=field):
                self.a,self.ctx,self.cert,self.util,self.reg = fixture(); self.ctx[field] = value; self.no_admit()

    def test_calibration_receipt_missing_invalid_or_wrong_version(self):
        self.util["calibration_receipt"] = None; self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.util["calibration_receipt"]["model_version"] = "v2"; self.register_cal(); self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.util["calibration_receipt"]["receipt_version"] = "future"; self.register_cal(); self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.util["calibration_receipt"]["split"] = "TRAINING"; self.register_cal(); self.no_admit()

    def test_signed_fields_or_registry_tampering_not_admitted(self):
        self.cert["fact_receipt"]["risk_upper"] = 0.0; self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture(); self.reg["calibration"].clear(); self.no_admit()
        self.a,self.ctx,self.cert,self.util,self.reg = fixture(); self.util["lcb"] = 5.0; self.no_admit()

    def test_each_fact_receipt_required_field_missing_fails_even_if_registered(self):
        required = ['receipt_version', 'receipt_id', 'scope', 'outcome', 'source_id',
                    'evidence_reference', 'audit_method', 'asset_sha256', 'task_sha256',
                    'risk_upper', 'known_conflict_lower_bound']
        for field in required:
            with self.subTest(field=field):
                self.a,self.ctx,self.cert,self.util,self.reg = fixture(); rec = self.cert['fact_receipt']; del rec[field]
                if field != 'receipt_id': self.register_fact()
                self.no_admit()

    def test_each_calibration_required_field_missing_fails_even_if_registered(self):
        required = ['receipt_version', 'receipt_id', 'status', 'split', 'asset_sha256', 'dataset_id',
                    'dataset_version', 'task_sha256', 'model_id', 'model_version', 'model_artifact_sha256',
                    'budget_sha256', 'calibration_members_sha256', 'calibration_n', 'calibration_evidence_reference', 'lcb']
        for field in required:
            with self.subTest(field=field):
                self.a,self.ctx,self.cert,self.util,self.reg = fixture(); rec = self.util['calibration_receipt']; del rec[field]
                if field != 'receipt_id': self.register_cal()
                self.no_admit()

    def test_bound_high_conflict_rejected_and_high_risk_unknown(self):
        self.cert["risk_upper"] = self.cert["known_conflict_lower_bound"] = 0.2
        self.cert["fact_receipt"]["risk_upper"] = self.cert["fact_receipt"]["known_conflict_lower_bound"] = 0.2
        self.register_fact(); self.assertEqual(self.decision()["decision"], "REJECT")
        self.a,self.ctx,self.cert,self.util,self.reg = fixture()
        self.cert["risk_upper"] = self.cert["fact_receipt"]["risk_upper"] = 0.2; self.register_fact()
        self.assertEqual(self.decision()["decision"], "NEEDS_VERIFICATION")

    def test_incomplete_asset_does_not_admit_even_with_self_consistent_receipts(self):
        for path in [("source_version",), ("license_sha256",), ("records", 0, "group"), ("records", 0, "source_row"), ("records", 0, "y")]:
            with self.subTest(path=path):
                self.a,self.ctx,self.cert,self.util,self.reg = fixture(); target = self.a
                for key in path[:-1]: target = target[key]
                target[path[-1]] = None; newhash = asset_digest(self.a); self.cert["asset_sha256"] = newhash
                self.cert["fact_receipt"]["asset_sha256"] = newhash; self.register_fact()
                self.util["calibration_receipt"]["asset_sha256"] = newhash; self.register_cal(); self.no_admit()

    def test_invalid_thresholds_fail_closed(self):
        for tau in [float("nan"), float("inf"), -0.1, 1.1, True]:
            self.assertNotEqual(decide(self.a,self.ctx,self.cert,self.util,self.reg,tau=tau)["decision"], "ADMIT")
        for gain in [float("nan"), float("inf"), -0.1, True]:
            self.assertNotEqual(decide(self.a,self.ctx,self.cert,self.util,self.reg,minimum_gain=gain)["decision"], "ADMIT")

    def test_nan_missing_binding_is_not_admitted(self):
        self.cert.pop("binding_valid"); self.cert["risk_upper"] = float("nan"); self.util["lcb"] = float("nan"); self.no_admit()


if __name__ == "__main__": unittest.main(verbosity=2)
