"""Pure synthetic reference-audit boundaries; no historical models are fitted."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import itertools
import json
import math
import random
import unittest

import numpy as np
import pandas as pd

from data_asset_poc.self_service_trust import (duplicate_metrics, evaluate_plan,
    frame_payload, make_plan, value_semantics)
from data_asset_poc.self_service_trust.core import canonical_hash


def tables(n=10):
    candidate = pd.DataFrame({"row_id": range(n), "value": [float(i) for i in range(n)], "label": ["a"] * n})
    reference = candidate.rename(columns={"row_id": "record_id", "value": "measurement"}).copy()
    return candidate, reference


def descriptor(frame):
    raw = frame.to_csv(index=False, lineterminator="\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    return {"dataset_id": "ds_" + digest, "version": "sha256:" + digest, "content_hash": digest,
        "columns": [{"name": c, "dtype": str(frame[c].dtype)} for c in frame], "row_count": len(frame), "column_count": len(frame.columns)}


def bindings(c, r, relationship="UNKNOWN"):
    return {"candidate": descriptor(c), "reference": descriptor(r),
        "source_declaration": {"description": "Synthetic fixed reference", "source_url": "", "license": "synthetic",
            "observed_at": "", "version": "v1", "relationship": relationship}}


def config(n=10, **changes):
    return {"key_mapping": {"row_id": "record_id"}, "rules": [{"candidate_column": "value", "reference_column": "measurement", "comparison": "numeric"}],
        "sample_size": n, "seed": 103, "epsilon": .1, "alpha": .05, **changes}


def evaluate(c, r, **changes):
    plan = make_plan(c, r, config(min(len(c), 10), **changes), bindings(c, r))
    return evaluate_plan(c, r, plan)


class PlanTests(unittest.TestCase):
    def test_fixed_srs_positions_and_json_round_trip(self):
        c, r = tables(20)
        plan = make_plan(c, r, config(6), bindings(c, r))
        self.assertEqual(plan["sampled_positions"], sorted(random.Random(103).sample(range(20), 6)))
        self.assertEqual(plan["plan_hash"], canonical_hash({k: v for k, v in plan.items() if k != "plan_hash"}))
        self.assertEqual(evaluate_plan(c, r, json.loads(json.dumps(plan)))["sample_size"], 6)

    def test_samples_do_not_depend_on_reference_values_or_source_claim(self):
        c, r = tables(20)
        a = make_plan(c, r, config(6), bindings(c, r))
        r.measurement = -999.
        b = make_plan(c, r, config(6), bindings(c, r, "DECLARED_INDEPENDENT"))
        self.assertEqual(a["sampled_positions"], b["sampled_positions"])
        self.assertNotEqual(a["plan_hash"], b["plan_hash"])

    def test_empty_sample_returns_vacuous_interval(self):
        c, r = tables()
        result = evaluate(c, r, sample_size=0)
        self.assertEqual((result["disagreement_interval"]["lower"], result["disagreement_interval"]["upper"]), (0., 1.))
        self.assertIsNone(result["sample_disagreement_fraction_among_comparable"])
        self.assertEqual(result["statistical_decision"], "NEED_MORE_EVIDENCE")

    def test_all_pass_census_and_epsilon_zero(self):
        c, r = tables()
        result = evaluate(c, r, sampling_method="CENSUS", epsilon=0.)
        self.assertEqual(result["counts"]["passed"], 10)
        self.assertEqual(result["disagreement_interval"]["upper"], 0.)
        self.assertEqual(result["statistical_decision"], "ACCEPT_CONDITIONALLY")
        self.assertEqual(result["fact_status"], "UNKNOWN")
        self.assertEqual(result["formal_decision"], "NEED_MORE_EVIDENCE")

    def test_one_disagreement_not_automatic_rejection(self):
        c, r = tables()
        r.loc[0, "measurement"] = 100.
        result = evaluate(c, r, sampling_method="CENSUS", epsilon=.1)
        self.assertEqual(result["counts"]["disagreements"], 1)
        self.assertEqual(result["statistical_decision"], "ACCEPT_CONDITIONALLY")
        self.assertFalse(result["factual_truth_verified"])

    def test_rejection_is_interval_threshold_based(self):
        c, r = tables()
        r.loc[:1, "measurement"] = -1.
        self.assertEqual(evaluate(c, r, sampling_method="CENSUS")["statistical_decision"], "REJECT_CONFIRMED")

    def test_nonprobability_only_describes_fixed_positions(self):
        c, r = tables()
        result = evaluate(c, r, sample_size=2, sampling_method="NON_PROBABILITY", positions=[0, 4])
        self.assertEqual(result["sampled_positions"], [0, 4])
        self.assertIsNone(result["disagreement_interval"])
        self.assertEqual(result["statistical_decision"], "DESCRIPTIVE_ONLY")

    def test_missing_reference_keys_stay_in_sample(self):
        c, r = tables()
        r.record_id += 100
        result = evaluate(c, r, sampling_method="CENSUS")
        self.assertEqual(result["counts"]["incomparable"], 10)
        self.assertEqual(len(result["observations"]), 10)
        self.assertEqual((result["disagreement_interval"]["lower"], result["disagreement_interval"]["upper"]), (0., 1.))
        self.assertEqual(result["statistical_decision"], "NEED_MORE_EVIDENCE")

    def test_partial_missing_retains_original_n_and_worst_case_census(self):
        c, r = tables()
        r.loc[0, "measurement"] = -1.
        r.loc[1:3, "measurement"] = np.nan
        result = evaluate(c, r, sampling_method="CENSUS")
        self.assertEqual(result["counts"], {"population_size": 10, "sample_size": 10, "comparable": 7, "passed": 6, "disagreements": 1, "incomparable": 3})
        self.assertEqual((result["disagreement_interval"]["lower"], result["disagreement_interval"]["upper"]), (.1, .4))
        self.assertEqual(result["statistical_decision"], "NEED_MORE_EVIDENCE")

    def test_duplicate_or_missing_keys_reject_whole_input(self):
        c, r = tables()
        for role in ("candidate", "reference"):
            for invalid in (None, " ", 0, float("inf")):
                a, b = c.copy(), r.copy()
                table, key = (a, "row_id") if role == "candidate" else (b, "record_id")
                table[key] = table[key].astype(object)
                table.loc[1, key] = invalid
                with self.subTest(role=role, invalid=invalid), self.assertRaises(ValueError):
                    make_plan(a, b, config(), bindings(a, b))

    def test_wrong_explicit_key_has_no_row_position_fallback(self):
        c, r = tables()
        r.record_id += 1
        result = evaluate(c, r, sampling_method="CENSUS")
        self.assertEqual(result["observations"][0]["outcome"], "INCOMPARABLE")
        self.assertEqual(result["observations"][1]["reference_position"], 0)
        self.assertEqual(result["observations"][1]["outcome"], "DISAGREEMENT")

    def test_key_only_rules_cannot_manufacture_agreement(self):
        c, r = tables()
        for rules in ([], [{"candidate_column": "row_id", "reference_column": "record_id", "comparison": "exact"}]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                make_plan(c, r, config(rules=rules), bindings(c, r))

    def test_config_boundaries_and_unknown_or_inapplicable_flags(self):
        c, r = tables()
        invalid = [{"sample_size": True}, {"sample_size": -1}, {"sample_size": 11}, {"seed": True}, {"seed": -1},
            {"alpha": 0}, {"alpha": 1}, {"alpha": True}, {"alpha": float("nan")}, {"epsilon": -1}, {"epsilon": 2},
            {"sampling_method": "CENSUS", "sample_size": 9}, {"positions": [0]}, {"applicable": False},
            {"sampling_method": "NON_PROBABILITY", "positions": [0] * 10}, {"key_mapping": {}},
            {"rules": [{"candidate_column": "value", "reference_column": "measurement", "comparison": "exact", "atol": 1}]}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                make_plan(c, r, config(**change), bindings(c, r))

    def test_plan_tamper_and_rehashed_position_tamper_rejected(self):
        c, r = tables()
        plan = make_plan(c, r, config(2), bindings(c, r))
        for rehash in (False, True):
            bad = deepcopy(plan)
            bad["sampled_positions"] = [0, 1]
            if rehash:
                bad["plan_hash"] = canonical_hash({k: v for k, v in bad.items() if k != "plan_hash"})
            with self.subTest(rehash=rehash), self.assertRaises(ValueError):
                evaluate_plan(c, r, bad)

    def test_frame_mutation_or_reordering_invalidates_plan(self):
        c, r = tables()
        plan = make_plan(c, r, config(), bindings(c, r))
        changed = r.copy()
        changed.loc[1, "measurement"] = -1.
        for a, b in ((c, changed), (c.iloc[::-1].reset_index(drop=True), r)):
            with self.assertRaises(ValueError):
                evaluate_plan(a, b, plan)

    def test_optional_raw_bytes_and_version_binding(self):
        c, r = tables()
        bound = bindings(c, r)
        c.attrs["raw_bytes"] = c.to_csv(index=False, lineterminator="\n").encode()
        plan = make_plan(c, r, config(), bound)
        self.assertTrue(plan["raw_bytes_verified_by_core_at_plan"]["candidate"])
        c.attrs["raw_bytes"] += b"\n"
        with self.assertRaises(ValueError):
            evaluate_plan(c, r, plan)
        del c.attrs["raw_bytes"]
        bound["candidate"]["version"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            make_plan(c, r, config(), bound)

    def test_each_source_claim_stays_unknown(self):
        c, r = tables()
        for relationship in ("UNKNOWN", "SAME_UPSTREAM", "DECLARED_INDEPENDENT"):
            plan = make_plan(c, r, config(), bindings(c, r, relationship))
            result = evaluate_plan(c, r, plan)
            self.assertEqual(result["fact_status"], "UNKNOWN")
            self.assertIsNone(result["factual_error_interval"])

    def test_inputs_not_mutated(self):
        c, r = tables()
        a, b = c.copy(), r.copy()
        settings, bound = config(), bindings(c, r)
        original = deepcopy((settings, bound))
        evaluate_plan(c, r, make_plan(c, r, settings, bound))
        pd.testing.assert_frame_equal(c, a)
        pd.testing.assert_frame_equal(r, b)
        self.assertEqual((settings, bound), original)


class ComparisonTests(unittest.TestCase):
    def test_known_disagreement_dominates_other_unknown_fields(self):
        c, r = tables(1)
        r.loc[0, "measurement"], r.loc[0, "label"] = 4., None
        rules = config()["rules"] + [{"candidate_column": "label", "reference_column": "label", "comparison": "exact"}]
        result = evaluate(c, r, sample_size=1, sampling_method="CENSUS", rules=rules)
        self.assertEqual(result["observations"][0]["outcome"], "DISAGREEMENT")

    def test_exact_types_are_explicit(self):
        c, r = tables(1)
        rule = [{"candidate_column": "value", "reference_column": "measurement", "comparison": "exact"}]
        c.value = pd.Series(["0"], dtype=object)
        self.assertEqual(evaluate(c, r, sample_size=1, rules=rule)["observations"][0]["outcome"], "DISAGREEMENT")
        c.value = [0]
        self.assertEqual(evaluate(c, r, sample_size=1, rules=rule)["observations"][0]["outcome"], "PASS")

    def test_exact_and_numeric_large_integer_values_do_not_collapse(self):
        c = pd.DataFrame({"row_id": [0], "value": [2**53], "other_float": [.5]})
        r = pd.DataFrame({"record_id": [0], "measurement": [2**53 + 1], "other_float": [.5]})
        for comparison in ("exact", "numeric"):
            rule = [{"candidate_column": "value", "reference_column": "measurement", "comparison": comparison}]
            with self.subTest(comparison=comparison):
                self.assertEqual(evaluate(c, r, sample_size=1, rules=rule)["counts"]["disagreements"], 1)

    def test_numeric_tolerance_and_bad_values(self):
        c, r = tables(1)
        rule = [{"candidate_column": "value", "reference_column": "measurement", "comparison": "numeric", "atol": .2, "rtol": .01}]
        c.value, r.measurement = [1.1], [1.]
        self.assertEqual(evaluate(c, r, sample_size=1, rules=rule)["counts"]["passed"], 1)
        for bad in ("abc", np.inf, "", None, True):
            c.value = pd.Series([bad], dtype=object)
            self.assertEqual(evaluate(c, r, sample_size=1, rules=rule)["counts"]["incomparable"], 1)

    def test_finite_extreme_numeric_values_do_not_overflow_to_pass(self):
        c, r = tables(1)
        c.value, r.measurement = [1e308], [-1e308]
        self.assertEqual(evaluate(c, r, sample_size=1)["counts"]["disagreements"], 1)

    def test_typed_frame_export_json_safe_and_not_raw_bytes(self):
        c, _ = tables(2)
        c.loc[1, "value"] = np.nan
        payload = frame_payload(c)
        self.assertEqual(payload["rows"][1][1], ["missing", None])
        self.assertEqual(payload, json.loads(json.dumps(payload, allow_nan=False)))

    def test_unknown_completion_coverage_small_finite_populations(self):
        # Exhaustively enumerate all 3^4 fixed PASS/ERROR/UNKNOWN populations.
        # For each size-2 SRS sample, verify simultaneous coverage of every
        # completion: the confidence envelope contains [K_known,K_possible]/N.
        n_total, sample_size, alpha = 4, 2, .2
        subsets = list(itertools.combinations(range(n_total), sample_size))
        for states in itertools.product((0, 1, 2), repeat=n_total):
            c, r = tables(n_total)
            for i, state in enumerate(states):
                r.loc[i, "measurement"] = -1. if state == 1 else np.nan if state == 2 else float(i)
            bound = bindings(c, r)
            lower_truth = sum(s == 1 for s in states) / n_total
            upper_truth = sum(s in (1, 2) for s in states) / n_total
            failures = 0
            for subset in subsets:
                # Obtain each distinct SRS set using a deterministic seed;
                # preimage search depends only on positions, never outcomes.
                seed = next(seed for seed in range(1000) if tuple(sorted(random.Random(seed).sample(range(n_total), sample_size))) == subset)
                plan = make_plan(c, r, config(sample_size, seed=seed, alpha=alpha), bound)
                interval = evaluate_plan(c, r, plan)["disagreement_interval"]
                failures += interval["lower"] > lower_truth or interval["upper"] < upper_truth
            self.assertLessEqual(Fraction(failures, len(subsets)), Fraction(1, 5), states)

    def test_hg_integer_endpoints_match_exact_combination_tails(self):
        from data_asset_poc.self_service_trust.core import finite_population_interval
        for total in range(1, 9):
            for n in range(total + 1):
                for errors in range(n + 1):
                    possible = list(range(errors, total - n + errors + 1))
                    def mass(k, observed):
                        if 0 <= observed <= k and 0 <= n-observed <= total-k:
                            return Fraction(math.comb(k, observed)*math.comb(total-k, n-observed), math.comb(total, n))
                        return Fraction(0)
                    lower = min(k for k in possible if sum((mass(k, j) for j in range(errors, n+1)), Fraction(0)) >= Fraction(1, 40))
                    upper = max(k for k in possible if sum((mass(k, j) for j in range(errors+1)), Fraction(0)) >= Fraction(1, 40))
                    actual = finite_population_interval(total, n, errors, .05)
                    self.assertEqual((actual["error_total_lower"], actual["error_total_upper"]), (lower, upper))


class DuplicateTests(unittest.TestCase):
    def test_unique_row_id_does_not_hide_content_duplicates(self):
        frame = pd.DataFrame({"row_id": [1, 2, 3], "x": [1., 1., 2.], "y": ["a", "a", "b"]})
        result = duplicate_metrics(frame, ["row_id"], ["x"])
        self.assertEqual(result["full_row"]["excess_duplicate_rows"], 0)
        self.assertEqual(result["content_excluding_auxiliary_ids"]["excess_duplicate_rows"], 1)
        self.assertEqual(result["task_features"]["excess_duplicate_rows"], 1)
        self.assertEqual(result["content_excluding_auxiliary_ids"]["duplicate_group_count"], 1)
        self.assertEqual(result["factual_duplicates"]["status"], "UNKNOWN")

    def test_no_auxiliary_inference_from_name(self):
        c, _ = tables()
        result = duplicate_metrics(c)
        self.assertEqual(result["auxiliary_id_columns"], [])
        self.assertEqual(result["task_features"]["status"], "NOT_EVALUATED")

    def test_no_columns_and_no_rows_are_not_perfect_uniqueness(self):
        result = duplicate_metrics(pd.DataFrame({"row_id": [1, 2]}), ["row_id"])
        self.assertIsNone(result["content_excluding_auxiliary_ids"]["uniqueness"])
        self.assertEqual(duplicate_metrics(pd.DataFrame({"x": []}))["full_row"]["status"], "NOT_EVALUATED")

    def test_declared_features_cannot_include_auxiliary_id(self):
        c, _ = tables()
        for args in ((["missing"], None), (["row_id"], ["row_id"]), (["row_id", "row_id"], None)):
            with self.assertRaises(ValueError):
                duplicate_metrics(c, *args)


class ValueSemanticsTests(unittest.TestCase):
    def report(self, lo, hi, status):
        return {"validation": {"value_status": status, "ci_lower": lo, "ci_upper": hi, "delta_u": (lo+hi)/2, "model_reliable": True},
            "recommendation": {"action": "do_not_use", "reason": "saved-original"},
            "test": {"delta_u": 1000.}}

    def test_five_states_and_original_decision_preserved(self):
        for lo, hi, status in ((.1, .2, "POSITIVE_VALUE_EVIDENCE"), (-.2, -.1, "NEGATIVE_VALUE_EVIDENCE"),
                (-.1, .1, "VALUE_UNCERTAIN"), (0., .1, "VALUE_UNCERTAIN"), (-.1, 0., "VALUE_UNCERTAIN")):
            report = self.report(lo, hi, status)
            before = deepcopy(report)
            result = value_semantics(report)
            self.assertEqual(result["value_status"], status)
            self.assertEqual(result["original_decision"], report["recommendation"])
            self.assertEqual(report, before)
            result["original_decision"]["reason"] = "changed copy"
            self.assertEqual(report, before)
        report["validation"]["model_reliable"] = False
        self.assertEqual(value_semantics(report)["value_status"], "TASK_MODEL_UNRELIABLE")
        self.assertEqual(value_semantics(None)["value_status"], "NOT_EVALUATED")

    def test_uncertain_means_pause_not_negative(self):
        result = value_semantics(self.report(-1., 1., "VALUE_UNCERTAIN"))
        self.assertIn("暂缓", result["action_hint_zh"])
        self.assertIn("不等于价值为负", result["action_hint_zh"])
        self.assertFalse(result["uses_test_for_semantics"])

    def test_temporal_and_inconsistent_saved_record_not_promoted(self):
        report = self.report(-1., 1., "POSITIVE_VALUE_EVIDENCE")
        self.assertEqual(value_semantics(report)["value_status"], "VALUE_UNCERTAIN")
        report["validation"] = {"value_status": "NOT_EVALUATED_FOR_TEMPORAL_DEPENDENCE", "delta_u": .2}
        self.assertEqual(value_semantics(report)["value_status"], "NOT_EVALUATED")


if __name__ == "__main__":
    unittest.main()
