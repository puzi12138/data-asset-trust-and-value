"""Compare finite binary64 probability bounds under an explicit tolerance.

This utility does not load a reference result or certify any source. Calling
code must decide whether the supplied values and tolerances are appropriate.
"""
from __future__ import annotations

import math
import struct


def compare_probability_bounds(reference: float, candidate: float, *,
                               max_ulps: int = 8,
                               max_absolute_difference: float = 5e-15) -> dict:
    """Compare two probability bounds; require exact endpoints and signed zero.

    Return descriptive binary64 and absolute differences only. An exception
    denotes an invalid value or exceeded declared tolerance; equality of these
    values does not establish data authenticity or statistical validity.
    """
    if type(max_ulps) is not int or max_ulps < 0:
        raise ValueError('MAX_ULPS_MUST_BE_NONNEGATIVE_INTEGER')
    if (isinstance(max_absolute_difference, bool)
            or not isinstance(max_absolute_difference, (float, int))
            or not math.isfinite(max_absolute_difference)
            or max_absolute_difference < 0):
        raise ValueError('MAX_ABSOLUTE_DIFFERENCE_MUST_BE_FINITE_AND_NONNEGATIVE')
    if type(reference) is not float or type(candidate) is not float:
        raise ValueError('NUMERIC_BOUND_FLOAT_REQUIRED')
    if (not math.isfinite(reference) or not math.isfinite(candidate)
            or not 0 <= reference <= 1 or not 0 <= candidate <= 1):
        raise ValueError('NUMERIC_BOUND_FINITE_PROBABILITY_REQUIRED')
    if (reference in (0.0, 1.0) or candidate in (0.0, 1.0)) and reference.hex() != candidate.hex():
        raise ValueError('NUMERIC_ENDPOINT_MUST_BE_EXACT')
    index = lambda value: struct.unpack('>Q', struct.pack('>d', value))[0]
    ulps = abs(index(reference) - index(candidate))
    absolute = abs(reference - candidate)
    if ulps > max_ulps or absolute > max_absolute_difference:
        raise ValueError('NUMERIC_COMPATIBILITY_LIMIT_EXCEEDED')
    return {'reference': reference, 'candidate': candidate,
            'reference_hex': reference.hex(), 'candidate_hex': candidate.hex(),
            'ulp_distance': ulps, 'absolute_difference': absolute,
            'exact': reference.hex() == candidate.hex(),
            'max_ulps': max_ulps, 'max_absolute_difference': max_absolute_difference}
