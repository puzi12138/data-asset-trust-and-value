"""Reusable evidence-scoped diversity and lexical helpers."""
from __future__ import annotations
import heapq
from typing import Any

def diversity_bounds(counts: list[int], unknown: int) -> dict[str, Any]:
    """Sharp Gini–Simpson bounds under fixed K, plus open-world bounds.

    Unknown categories can be allocated independently.  Fixed-K bounds apply
    only if all unknowns truly belong to one of the represented K categories.
    The open-world bounds allow an unlimited number of new categories.
    """
    if (not counts or any(type(n) is not int or n < 0 for n in counts) or
            type(unknown) is not int or unknown < 0):
        raise ValueError("counts and unknown must be nonnegative integers; K > 0")
    known = sum(counts)
    n = known + unknown
    if n == 0:
        return {"known_subset": None, "known_count": 0, "unknown_count": 0,
                "fixed_k_interval": None, "open_world_interval": None}

    def index(squares: int, denom: int) -> float:
        return 1.0 - squares / (denom * denom)

    known_index = None if known == 0 else index(sum(x*x for x in counts), known)
    # Convexity: concentrating all additions on a largest count maximizes sum squares.
    largest = max(counts)
    maximum_squares = sum(x*x for x in counts) + 2 * largest * unknown + unknown*unknown
    lower = index(maximum_squares, n)
    # Separable convex minimization: each next unit goes to the smallest count.
    balanced = counts[:]
    heapq.heapify(balanced)
    for _ in range(unknown):
        heapq.heappush(balanced, heapq.heappop(balanced) + 1)
    fixed_upper = index(sum(x*x for x in balanced), n)
    # Each unknown can form its own previously unobserved category in open world.
    open_upper = index(sum(x*x for x in counts) + unknown, n)
    return {"known_subset": known_index, "known_count": known, "unknown_count": unknown,
            "fixed_k_interval": [lower, fixed_upper],
            "open_world_interval": [lower, open_upper]}

def _record_text(record: dict[str, Any]) -> str | None:
    instruction = record.get("instruction")
    input_text = record.get("input", "")
    if not isinstance(instruction, str) or not isinstance(input_text, str):
        return None
    return instruction + "\n" + input_text

def _lexical_tf_idf(references: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[float | None]:
    """Char n-gram TF-IDF fit on D0 only; outputs max cosine as lexical proxy."""
    ref_text = [_record_text(r) for r in references]
    if not ref_text or any(t is None for t in ref_text):
        return [None] * len(records)
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                     lowercase=True, norm="l2")
        matrix = vectorizer.fit_transform(ref_text)
    except (ImportError, ValueError):
        return [None] * len(records)
    values: list[float | None] = []
    for record in records:
        text = _record_text(record)
        if text is None:
            values.append(None)
            continue
        query = vectorizer.transform([text])
        if query.nnz == 0:
            values.append(None)
        else:
            products = query @ matrix.T
            maximum = float(products.max())
            values.append(max(0.0, min(1.0, maximum)))
    return values
