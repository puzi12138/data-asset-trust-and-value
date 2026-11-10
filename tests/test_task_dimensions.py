"""Task dimension unit tests on declared fixtures, not challenge-set truth."""
import itertools
import unittest
from data_asset_poc.task_verification import core, dimensions as d


def spec(op="add", args=None):
    return {"family": "arithmetic", "operation": op, "arguments": args or {"a": 2, "b": 3}, "output_format": "json_integer"}


def rec(sid, group=None, instruction="Add 2 and 3.", response="5"):
    return {"sample_id": sid, "group_id": group or sid, "instruction": instruction, "input": "", "response": response}


def row(sid, s=None, c=1, r=1, fidelity=1):
    return {"sample_id": sid, "original_spec": s, "C": c, "R": r, "contract_fidelity": fidelity}


def annotation(r, s, source="independent_A"):
    return {"sample_id": r["sample_id"], "task_binding": core.task_binding(r), "status": "PARSED", "spec": s,
            "evidence_spans": [{"field": "instruction", "start": 0, "end": len(r["instruction"]), "quote": r["instruction"]}],
            "reason": "fixture", "source_id": source}


def meta(refs):
    return {"reference_id": "test_fixture", "version": "1", "scope": "fixture", "reference_sha256": core.digest(refs)}


class TestDimensions(unittest.TestCase):
    def test_all_evidence_is_separate_and_aligned_by_id(self):
        rr = [rec("a", "same", response="5"), rec("b", "same", response="6")]
        result = d.seven_dimensions(rr, [row("b", spec(), 0, 0, 1), row("a", spec(), 1, 1, 0)], None)
        a, b = [x["features"] for x in result["rows"]]
        self.assertEqual((a["correctness"]["value"], a["task_relevance"]["value"], a["consistency"]["instruction_contract"]), (1, 1, 0))
        self.assertEqual((b["correctness"]["value"], b["task_relevance"]["value"]), (0, 0))
        self.assertEqual(a["consistency"]["cross_record"], 0)
        self.assertEqual(result["batch_diversity"]["group_count"], 1)
        self.assertEqual(result["batch_diversity"]["record_count"], 2)

    def test_no_unverified_density(self):
        rr = [rec("a", response="5"), rec("b", response="6"), rec("c", response="5")]
        got = d.seven_dimensions(rr, [row("a", spec(), 1), row("b", spec(), 0), row("c", spec(), None)], None)["rows"]
        self.assertEqual(got[0]["features"]["information_density"]["verified_output_atomic_units"], 1)
        self.assertIsNone(got[1]["features"]["information_density"]["verified_units_per_response_byte"])
        self.assertIsNone(got[2]["features"]["information_density"]["verified_units_per_response_byte"])

    def test_partial_reference_positive_and_negative(self):
        refs = [rec("ref1"), rec("ref2", instruction="Unsupported open request.")]
        r = [rec("a"), rec("b", instruction="Add 4 and 5.")]
        rows = [row("a", spec()), row("b", spec(args={"a": 4, "b": 5}))]
        result = d.seven_dimensions(r, rows, refs, [annotation(refs[0], spec())], meta(refs))
        self.assertEqual(result["reference_annotation_status"]["states"], {"SINGLE_SOURCE_PARSED": 1, "UNKNOWN": 1})
        self.assertEqual(result["rows"][0]["features"]["novelty"]["canonical_task_duplicate"], 1)
        self.assertIsNone(result["rows"][1]["features"]["novelty"]["canonical_task_duplicate"])

    def test_reference_binding_or_span_tamper_keeps_unknown(self):
        refs = [rec("ref1")]; ann = annotation(refs[0], spec()); ann["task_binding"] = "0" * 64
        r = d.seven_dimensions([rec("a")], [row("a", spec())], refs, [ann], meta(refs))
        self.assertEqual(r["reference_specs_known"], 0)
        self.assertEqual(r["reference_annotation_status"]["ignored"], ["INVALID_OR_UNBOUND_REFERENCE_ANNOTATION"])
        self.assertIsNone(r["rows"][0]["features"]["novelty"]["canonical_task_duplicate"])

    def test_reference_conflict_is_not_majority_vote(self):
        refs = [rec("r")]; ann = [annotation(refs[0], spec(), "a"), annotation(refs[0], spec(), "b"),
                                    annotation(refs[0], spec("subtract"), "c")]
        result = d.seven_dimensions([rec("x")], [row("x", spec())], refs, ann, meta(refs))
        self.assertEqual(result["reference_specs_known"], 0)
        self.assertEqual(result["reference_annotation_status"]["states"], {"CONFLICT": 1})

    def test_complete_reference_allows_exact_absence(self):
        refs = [rec("r")]; ann = [annotation(refs[0], spec())]
        result = d.seven_dimensions([rec("x", instruction="Add 4 and 5.")], [row("x", spec(args={"a": 4, "b": 5}))], refs, ann, meta(refs))
        self.assertEqual(result["rows"][0]["features"]["novelty"]["canonical_task_duplicate"], 0)

    def test_wrong_reference_hash_rejected(self):
        refs = [rec("r")]
        with self.assertRaises(ValueError):
            d.seven_dimensions([rec("x")], [row("x", spec())], refs, [], {**meta(refs), "reference_sha256": "0" * 64})

    def test_group_unknown_and_logical_bounds(self):
        rr = [rec("a", "g1"), rec("b", "g1"), rec("c", "g2", "Unknown task")]
        result = d.seven_dimensions(rr, [row("a", spec()), row("b", spec()), row("c", None, None, None, None)], None)
        q = result["batch_diversity"]
        self.assertEqual((q["known_count"], q["unknown_count"]), (1, 1))
        self.assertEqual(q["canonical_task_distinct"]["all_group_logical_interval"], [1, 2])
        self.assertEqual(q["text_distinct"]["ratio"], 1.0)

    def test_group_binding_mismatch_rejected(self):
        rr = [rec("a", "g", "Add 2 and 3."), rec("b", "g", "Subtract 2 and 3.")]
        with self.assertRaises(ValueError):
            d.seven_dimensions(rr, [row("a", spec()), row("b", spec())], None)

    def test_intra_answer_contradiction(self):
        rr = [rec("a", response='{"claims":[5,6]}'), rec("b", response='{"claims":[5,5]}'), rec("c", response="5")]
        o = d.seven_dimensions(rr, [row("a", spec()), row("b", spec()), row("c", spec())], None)["rows"]
        self.assertEqual([x["features"]["consistency"]["intra_answer"] for x in o], [0, 1, None])

    def test_complexity_is_not_learning_difficulty(self):
        s = {"family": "arithmetic", "operation": "compound_add_multiply", "arguments": {"a": 3, "b": 2, "c": 5}, "output_format": "json_integer"}
        q = d.seven_dimensions([rec("a")], [row("a", s)], None)["rows"][0]["features"]["complexity"]
        self.assertEqual((q["input_size"], q["primitive_operations"]), (3, 2))
        self.assertIsNone(q["reasoning_steps"]); self.assertIsNone(q["learning_difficulty"])

    def test_numeric_quantization_is_descriptive(self):
        result = d._num_distinct([0.1, 0.10000000000000002, None])
        self.assertEqual(result["distinct_values_12_decimal"], 1)
        self.assertEqual(result["raw_distinct_values"], 2)
        self.assertEqual(result["discriminability"], "NO_DISCRIMINATION")
        self.assertEqual(d._num_distinct([0.0, -0.0])["distinct_values_12_decimal"], 1)

    def test_member_hash_and_ids_checked(self):
        a = rec("a"); bad = {**row("a", spec()), "member_sha256": "0" * 64}
        with self.assertRaises(ValueError): d.seven_dimensions([a], [bad], None)
        with self.assertRaises(ValueError): d.seven_dimensions([a], [row("b", spec())], None)

    def test_fixed_diversity_bounds_match_small_exhaustive_cases(self):
        nchecked = 0
        for counts in itertools.product(range(2), repeat=3):
            for unknown in range(3):
                n = sum(counts) + unknown
                if not n: continue
                values = []
                for assignments in itertools.product(range(3), repeat=unknown):
                    final = list(counts)
                    for i in assignments: final[i] += 1
                    values.append(1 - sum(i*i for i in final)/(n*n))
                lo, hi = d.diversity_bounds(list(counts), unknown)["fixed_k_interval"]
                self.assertAlmostEqual(lo, min(values)); self.assertAlmostEqual(hi, max(values)); nchecked += 1
        self.assertEqual(nchecked, 23)


if __name__ == "__main__": unittest.main(verbosity=2)
