import itertools
import json
import unittest
from data_asset_poc.task_verification.core import *

class CoreTests(unittest.TestCase):
    @staticmethod
    def profile(operation="add", family="arithmetic", output="json_integer"):
        p={"task_id":"controlled", "task_version":"1", "task_description":"controlled test",
           "required_capabilities":[{"family":family,"operation":operation}], "expected_output":output,
           "evaluation_criteria":"exact_deterministic_answer", "task_source":"controlled"}
        p["task_profile_sha256"]=digest(p)
        return p

    def test_all_operations_and_oracle(self):
        cases = [
            ({"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"},5),
            ({"family":"arithmetic","operation":"subtract","arguments":{"a":2,"b":3},"output_format":"json_integer"},-1),
            ({"family":"arithmetic","operation":"multiply","arguments":{"a":2,"b":3},"output_format":"json_integer"},6),
            ({"family":"arithmetic","operation":"compound_add_multiply","arguments":{"a":2,"b":3,"c":4},"output_format":"json_integer"},14),
            ({"family":"string","operation":"reverse","arguments":{"text":"aß!"},"output_format":"json_string"},"!ßa"),
            ({"family":"string","operation":"filter_alpha","arguments":{"text":"a1-B!"},"output_format":"json_string"},"aB"),
            ({"family":"structured","operation":"count_sum","arguments":{"numbers":[2,-1,4]},"output_format":"json_count_sum"},{"count":3,"sum":5}),
            ({"family":"structured","operation":"sum_even","arguments":{"numbers":[2,-1,4]},"output_format":"json_integer"},6),
            ({"family":"list","operation":"sort_ascending","arguments":{"numbers":[2,-1,4]},"output_format":"json_list_integer"},[-1,2,4]),
            ({"family":"list","operation":"filter_even","arguments":{"numbers":[2,-1,4]},"output_format":"json_list_integer"},[2,4]),
        ]
        for spec, answer in cases:
            self.assertEqual(expected(spec), answer)
            self.assertEqual(correct(spec, json.dumps(answer, ensure_ascii=False)), 1)

    def test_canonical_commutativity_and_order(self):
        a={"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}
        b={"family":"arithmetic","operation":"add","arguments":{"a":3,"b":2},"output_format":"json_integer"}
        self.assertEqual(normalize(a),normalize(b))
        x={"family":"arithmetic","operation":"subtract","arguments":{"a":2,"b":3},"output_format":"json_integer"}
        y={"family":"arithmetic","operation":"subtract","arguments":{"a":3,"b":2},"output_format":"json_integer"}
        self.assertNotEqual(normalize(x),normalize(y))

    def test_spec_boundaries(self):
        valid={"family":"string","operation":"reverse","arguments":{"text":"x"*80},"output_format":"json_string"}
        self.assertIsNotNone(normalize(valid))
        bads=[
            {**valid,"output_format":"json_integer"},
            {"family":"string","operation":"reverse","arguments":{"text":"x"*81},"output_format":"json_string"},
            {"family":"arithmetic","operation":"add","arguments":{"a":True,"b":1},"output_format":"json_integer"},
            {"family":"list","operation":"filter_even","arguments":{"numbers":[1]*13},"output_format":"json_list_integer"},
            {"family":"list","operation":"filter_even","arguments":{"numbers":[]},"output_format":"json_list_integer"},
            {"family":"arithmetic","operation":"nope","arguments":{"a":1,"b":2},"output_format":"json_integer"},
        ]
        for spec in bads:
            with self.assertRaises(ValueError): normalize(spec)

    def test_tristate_states_and_fixed_intervals(self):
        record={"sample_id":"s","instruction":"x","input":"","response":"5",
                "contract":{"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}}
        spec={"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}
        seen=set()
        profile=self.profile()
        for e,c,r in itertools.product((0,1,None),repeat=3):
            row=evaluate_record(record,spec,c,e,profile,r)
            seen.add((e,c,r,row["strict_decision"],tuple(row["strict_interval"])))
            expected_state=0 if 0 in (e,c,r) else None if None in (e,c,r) else 1
            self.assertEqual(row["strict_decision"],decision(expected_state))
            self.assertEqual(row["strict_interval"],bounds(expected_state))
            self.assertEqual(row["strict_decision"],simple_same_evidence_gate(e,c,r))
        self.assertEqual(len(seen),27)

    def test_source_only_controls_E(self):
        record={"sample_id":"s","response":"5","contract":{"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}}
        spec=record["contract"]
        profile=self.profile()
        yes=evaluate_record(record,spec,1,1,profile,1)
        no=evaluate_record(record,spec,1,0,profile,1)
        self.assertEqual((yes["C"],yes["R"],yes["E"]),(1,1,1))
        self.assertEqual((no["C"],no["R"],no["E"]),(1,1,0))

    def test_annotation_binding_spans_conflicts_and_unknown(self):
        record={"instruction":"Add 2 and 3.","input":"","response":"5"}
        binding=task_binding(record)
        spec={"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}
        def ann(s,status="PARSED",source="a"):
            return {"annotation_id":source,"task_binding":binding,"status":status,"spec":s,
                    "evidence_spans":[{"field":"instruction","start":0,"end":12,"quote":"Add 2 and 3."}],
                    "uncertainty":"","reason":"","source_id":source}
        same,state,_,bad=combine_annotations(record,[ann(spec,source="a"),ann(spec,source="b")])
        self.assertEqual((same,state,bad),(normalize(spec),"AI_AGREED",[]))
        other={**spec,"arguments":{"a":2,"b":4}}
        self.assertEqual(combine_annotations(record,[ann(spec,source="a"),ann(other,source="b")])[1],"CONFLICT")
        self.assertEqual(combine_annotations(record,[ann(None,"UNKNOWN","a"),ann(None,"UNKNOWN","b")])[1],"UNKNOWN")
        one=ann(spec,source="a"); unknown=ann(None,"UNKNOWN","b")
        self.assertEqual(combine_annotations(record,[one,unknown,ann(spec,source="c")])[:2],(None,"AI_SINGLE"))
        self.assertEqual(combine_annotations(record,[one,one])[:2],(None,"AI_SINGLE"))
        self.assertEqual(combine_annotations(record,[one,ann(other,source="b"),ann(spec,source="c")])[1],"CONFLICT")
        forged=ann(spec,source="x");forged["task_binding"]="0"*64
        self.assertEqual(combine_annotations(record,[forged])[1],"UNKNOWN")
        wrong=ann(spec,source="x");wrong["evidence_spans"][0]["quote"]="Add 2"
        self.assertEqual(combine_annotations(record,[wrong])[3],["INVALID_OR_UNBOUND_ANNOTATION"])
        with_id=dict(one,group_id="g1")
        self.assertEqual(validate_annotation(with_id,dict(record,group_id="g1")),normalize(spec))
        with self.assertRaises(ValueError):validate_annotation(with_id,dict(record,group_id="g2"))

    def test_answer_conflict_and_json_injection(self):
        spec={"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}
        self.assertEqual(correct(spec,'{"claims":[5,5]}'),1)
        self.assertEqual(correct(spec,'{"claims":[5,6]}'),0)
        self.assertEqual(correct(spec,'{"answer":5,"extra":0}'),0)
        self.assertIsNone(correct(spec,'{"answer":NaN}'))

    def test_target_compatibility_separate_from_authority(self):
        spec={"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}
        profile=self.profile()
        self.assertEqual(target_compatibility(spec,"5",profile),1)
        self.assertEqual(target_compatibility(spec,"5",self.profile("subtract")),0)
        self.assertIsNone(target_compatibility(spec,"5",{**profile,"task_version":"2"}))
        self.assertIsNone(target_compatibility(spec,"5",{**profile,"expected_output":[]}))
        self.assertIsNone(target_compatibility(spec,"5",{"required_capabilities":[],"expected_output":"json_integer"}))
        self.assertIsNone(target_compatibility(spec,"5",None))
        self.assertEqual(target_compatibility(spec,"Final: 5",profile),0)

    def test_unacquired_content_not_computed(self):
        record={"sample_id":"s","response":"5","contract":{"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}}
        profile=self.profile()
        row=evaluate_record(record,record["contract"],None,1,profile,1)
        self.assertIsNone(row["C"])
        self.assertEqual(row["strict_decision"],"ABSTAIN")

    def test_unauthorized_profile_is_not_trusted(self):
        record={"sample_id":"s","response":"5","contract":{"family":"arithmetic","operation":"add","arguments":{"a":2,"b":3},"output_format":"json_integer"}}
        profile=self.profile("subtract")
        row=evaluate_record(record,record["contract"],1,1,profile,None)
        self.assertIsNone(row["R"])
        self.assertEqual(evaluate_record(record,record["contract"],1,1,profile,0)["R"],0)

    def test_fixed_denominator(self):
        rows=[{"sample_id":"a","strict_interval":[1,1],"content_interval":[1,1]},
              {"sample_id":"b","strict_interval":[0,1],"content_interval":[0,1]},
              {"sample_id":"c","strict_interval":[0,0],"content_interval":[0,0]}]
        self.assertEqual(summarize(rows)["strict_interval"],[1/3,2/3])

if __name__ == "__main__": unittest.main(verbosity=2)
