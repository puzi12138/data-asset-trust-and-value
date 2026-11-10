"""Unit tests for the standalone numerical bound comparison."""
import math
import unittest

from data_asset_poc.numeric_validation import compare_probability_bounds


class ProbabilityBoundTests(unittest.TestCase):
    def test_exact_and_ulp_tolerance(self):
        self.assertTrue(compare_probability_bounds(0.3, 0.3)['exact'])
        changed = 0.3
        for _ in range(8):
            changed = math.nextafter(changed, math.inf)
        result = compare_probability_bounds(0.3, changed)
        self.assertFalse(result['exact'])
        self.assertEqual(result['ulp_distance'], 8)
        with self.assertRaisesRegex(ValueError, 'NUMERIC_COMPATIBILITY_LIMIT_EXCEEDED'):
            compare_probability_bounds(0.3, math.nextafter(changed, math.inf))

    def test_endpoints_and_signed_zero_require_exact(self):
        self.assertTrue(compare_probability_bounds(0.0, 0.0)['exact'])
        self.assertTrue(compare_probability_bounds(1.0, 1.0)['exact'])
        for left, right in ((0.0, -0.0), (0.0, math.nextafter(0.0, 1.0)),
                            (1.0, math.nextafter(1.0, 0.0))):
            with self.subTest(left=left, right=right), self.assertRaisesRegex(ValueError, 'NUMERIC_ENDPOINT_MUST_BE_EXACT'):
                compare_probability_bounds(left, right)

    def test_invalid_values_and_thresholds_are_rejected(self):
        for value in (float('nan'), float('inf'), -0.1, 1.1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'NUMERIC_BOUND_FINITE_PROBABILITY_REQUIRED'):
                compare_probability_bounds(0.2, value)
        for value in (0, True, '0.2'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'NUMERIC_BOUND_FLOAT_REQUIRED'):
                compare_probability_bounds(0.2, value)
        for value in (-1, True, 0.1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'MAX_ULPS_MUST_BE_NONNEGATIVE_INTEGER'):
                compare_probability_bounds(0.2, 0.2, max_ulps=value)
        for value in (float('nan'), -1, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'MAX_ABSOLUTE_DIFFERENCE_MUST_BE_FINITE_AND_NONNEGATIVE'):
                compare_probability_bounds(0.2, 0.2, max_absolute_difference=value)

    def test_absolute_threshold_is_enforced_separately(self):
        candidate = math.nextafter(0.3, math.inf)
        with self.assertRaisesRegex(ValueError, 'NUMERIC_COMPATIBILITY_LIMIT_EXCEEDED'):
            compare_probability_bounds(0.3, candidate, max_ulps=8, max_absolute_difference=0.0)
