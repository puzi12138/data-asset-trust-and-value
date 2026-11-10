import copy
import math
import unittest
from data_asset_poc.assessment.binding import (
    BindingError, NORMALIZATION_VERSION, asset_digest, canonical_asset, json_digest,
)


def make_asset():
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "dataset_id": "demo-dataset", "dataset_version": "v1",
        "source_id": "publisher-a", "source_version": "2026-01",
        "license_sha256": json_digest({"license": "demo"}),
        "schema_sha256": json_digest({"features": ["a", "b"], "label": "y"}),
        "task_sha256": json_digest({"task": "demo"}),
        "records": [
            {"id": "a", "source_row": 1, "group": "g1", "x": [0.5, None], "y": "A"},
            {"id": "b", "source_row": 2, "group": "g2", "x": [2, 3.0], "y": "B"},
        ],
    }


class CanonicalBindingTests(unittest.TestCase):
    def setUp(self):
        self.asset = make_asset()
        self.digest = asset_digest(self.asset)

    def test_record_order_invariant_and_input_not_mutated(self):
        clone = copy.deepcopy(self.asset); clone["records"].reverse()
        self.assertEqual(asset_digest(clone), self.digest)
        self.assertEqual(clone["records"][0]["id"], "b")

    def test_each_required_record_field_is_bound(self):
        changes = {"id": "new-id", "source_row": 999, "group": "new-group", "x": [9, None], "y": "other"}
        for field, value in changes.items():
            with self.subTest(field=field):
                clone = copy.deepcopy(self.asset); clone["records"][0][field] = value
                self.assertNotEqual(asset_digest(clone), self.digest)

    def test_each_metadata_field_is_bound(self):
        values = {"dataset_id": "new-dataset", "dataset_version": "v2", "source_id": "other-source",
                  "source_version": "2026-02", "license_sha256": json_digest({"license": "other"}),
                  "schema_sha256": json_digest({"features": ["b", "a"]}),
                  "task_sha256": json_digest({"task": "changed"})}
        for field, value in values.items():
            with self.subTest(field=field):
                clone = copy.deepcopy(self.asset); clone[field] = value
                self.assertNotEqual(asset_digest(clone), self.digest)

    def test_normalization_version_wrong_rejected(self):
        self.asset["normalization_version"] = "ASSET-CANONICAL-2"
        with self.assertRaises(BindingError): asset_digest(self.asset)

    def test_missing_metadata_and_record_keys_rejected_not_ignored(self):
        for field in list(self.asset):
            clone = copy.deepcopy(self.asset); del clone[field]
            with self.subTest(field=field), self.assertRaises(BindingError): asset_digest(clone)
        for field in ["id", "source_row", "group", "x", "y"]:
            clone = copy.deepcopy(self.asset); del clone["records"][0][field]
            with self.subTest(field=field), self.assertRaises(BindingError): asset_digest(clone)

    def test_explicit_null_is_bound_but_extra_fields_not_discarded(self):
        clone = copy.deepcopy(self.asset); clone["source_version"] = None
        self.assertNotEqual(asset_digest(clone), self.digest)
        clone["accidental_extra"] = "forbidden"
        with self.assertRaises(BindingError): asset_digest(clone)
        clone = copy.deepcopy(self.asset); clone["records"][0]["provenance"] = "do-not-ignore"
        with self.assertRaises(BindingError): asset_digest(clone)

    def test_duplicate_normalized_ids_rejected(self):
        self.asset["records"][0]["id"] = "e\u0301"; self.asset["records"][1]["id"] = "é"
        with self.assertRaises(BindingError): asset_digest(self.asset)

    def test_unicode_normalization_and_dictionary_order(self):
        self.assertEqual(json_digest({"name": "e\u0301", "v": [1, 2]}), json_digest({"v": [1, 2], "name": "é"}))
        with self.assertRaises(BindingError): json_digest({"é": 1, "e\u0301": 2})

    def test_identity_whitespace_is_normalized_but_content_is_not(self):
        clone = copy.deepcopy(self.asset); clone['source_id'] = ' publisher-a '; clone['records'][0]['id'] = ' a '
        self.assertEqual(asset_digest(clone), self.digest)
        clone['records'][0]['y'] = ' A '
        self.assertNotEqual(asset_digest(clone), self.digest)

    def test_bool_int_and_float_are_not_conflated(self):
        self.assertEqual(len({json_digest(True), json_digest(1), json_digest(1.0)}), 3)
        clone = copy.deepcopy(self.asset); clone["records"][0]["source_row"] = True
        with self.assertRaises(BindingError): asset_digest(clone)

    def test_nonfinite_and_non_json_rejected_everywhere(self):
        for val in [float("nan"), float("inf"), float("-inf"), {1: "bad"}, (1, 2)]:
            with self.subTest(value=repr(val)):
                clone = copy.deepcopy(self.asset); clone["records"][0]["x"] = [val]
                with self.assertRaises(BindingError): asset_digest(clone)

    def test_feature_order_significant(self):
        clone = copy.deepcopy(self.asset); clone["records"][1]["x"].reverse()
        self.assertNotEqual(asset_digest(clone), self.digest)


if __name__ == "__main__": unittest.main(verbosity=2)
