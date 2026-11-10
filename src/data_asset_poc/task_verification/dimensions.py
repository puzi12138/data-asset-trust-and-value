"""Task seven independent dimensions, with explicit evidence and reference scope.

No provider declaration determines the original task or target, and this
module never loads a truth file or training outcomes. Interpreted specs are
conditional on the independently acquired evidence passed in evaluator rows.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_HALF_EVEN
import re
import time
from typing import Any

from data_asset_poc.task_verification.feature_helpers import diversity_bounds, _lexical_tf_idf
from data_asset_poc.task_verification import core

CAPABILITIES = tuple(core.OPERATIONS)


def _valid_spec(spec: Any) -> dict | None:
    try:
        return core.normalize(spec)
    except (ValueError, TypeError, KeyError):
        return None


def _text(record: dict) -> str | None:
    ins, inp = record.get("instruction"), record.get("input", "")
    return ins + "\n" + inp if isinstance(ins, str) and isinstance(inp, str) else None


def _group_view(records: list[dict], specs: list[dict | None]) -> list[dict]:
    """Count original instruction groups once; conflicting bindings become unknown."""
    by = {}
    for r, s in zip(records, specs):
        g = r.get("group_id", r["sample_id"])
        if not isinstance(g, str) or not g:
            raise ValueError("group_id must be a nonempty string")
        v = by.setdefault(g, {"group_id": g, "texts": set(), "specs": [], "ids": []})
        v["texts"].add(_text(r)); v["specs"].append(s); v["ids"].append(r["sample_id"])
    result = []
    for g in sorted(by):
        v = by[g]
        if len(v["texts"]) != 1 or None in v["texts"]:
            raise ValueError("one original instruction/input binding required per group")
        keys = {core.canonical(s) for s in v["specs"] if s is not None}
        # A partially observed group cannot be classified with certainty.
        s = v["specs"][0] if len(keys) == 1 and all(x is not None for x in v["specs"]) else None
        result.append({"group_id": g, "text": next(iter(v["texts"])), "spec": s,
                       "member_ids": sorted(v["ids"])})
    return result


def _reference_specs(references: list[dict], annotations: list[dict]) -> tuple[list[dict | None], dict]:
    """Validate ID/binding/quoted span before considering each independent annotation.

    Single source remains explicitly single-source interpretation. Multiple
    available sources with different normalized specifications are UNKNOWN;
    a majority vote never resolves conflicts.
    """
    ref_ids = [x.get("sample_id") for x in references]
    if any(not isinstance(x, str) or not x for x in ref_ids) or len(set(ref_ids)) != len(ref_ids):
        raise ValueError("reference sample IDs must be unique and nonempty")
    incoming = {x: [] for x in ref_ids}
    ignored = []
    for item in annotations:
        if not isinstance(item, dict) or item.get("sample_id") not in incoming:
            ignored.append("REFERENCE_ID_UNKNOWN")
            continue
        incoming[item["sample_id"]].append(item)
    outputs, states = [], Counter()
    for ref in references:
        parsed, sources = [], set()
        for item in incoming[ref["sample_id"]]:
            try:
                # Support old and new core validation contracts without weakening
                # the exact member ID and task binding checked here.
                clean = {k: v for k, v in item.items() if k not in ("sample_id", "group_id")}
                value = core.validate_annotation(clean, ref)
            except (ValueError, TypeError, KeyError):
                ignored.append("INVALID_OR_UNBOUND_REFERENCE_ANNOTATION")
                continue
            if value is not None:
                parsed.append(value); sources.add(item["source_id"])
        keys = {core.canonical(x) for x in parsed}
        if len(keys) > 1:
            value, state = None, "CONFLICT"
        elif parsed:
            value, state = parsed[0], "MULTI_SOURCE_AGREED" if len(sources) >= 2 else "SINGLE_SOURCE_PARSED"
        else:
            value, state = None, "UNKNOWN"
        outputs.append(value); states[state] += 1
    return outputs, {"states": dict(sorted(states.items())), "ignored": ignored,
                     "scope": "AI interpretations conditional on source correctness; no majority vote"}


def _answer_shape(value: Any, fmt: str) -> bool:
    if fmt == "json_integer": return type(value) is int
    if fmt == "json_string": return type(value) is str
    if fmt == "json_list_integer": return type(value) is list and all(type(x) is int for x in value)
    if fmt == "json_count_sum":
        return type(value) is dict and set(value) == {"count", "sum"} and all(type(value[x]) is int for x in value)
    return False


def _consistencies(records: list[dict], specs: list[dict | None]) -> tuple[list, list]:
    intra = []
    groups = {}
    for i, (record, spec) in enumerate(zip(records, specs)):
        claims = core.answer_claims(record.get("response"))
        if not claims or spec is None or not all(_answer_shape(v, spec["output_format"]) for v in claims):
            intra.append(None)
        elif len(claims) < 2:
            intra.append(None)
        else:
            intra.append(int(len({core.canonical(v) for v in claims}) == 1))
        if spec is not None:
            groups.setdefault(core.canonical(spec), []).append(i)
    cross = [None] * len(records)
    for indices in groups.values():
        if len(indices) < 2:
            continue
        keys, all_known = set(), True
        for i in indices:
            claims = core.answer_claims(records[i].get("response"))
            if not claims or not all(_answer_shape(v, specs[i]["output_format"]) for v in claims):
                all_known = False
            else:
                keys.update(core.canonical(v) for v in claims)
        v = 0 if len(keys) > 1 else 1 if all_known else None
        for i in indices: cross[i] = v
    return intra, cross


def _verified_units(record: dict, row: dict, spec: dict | None) -> tuple[int | None, float | None]:
    """Count answer fields/elements only after C=1; byte denominator is raw response."""
    if row.get("C") != 1 or spec is None:
        return None, None
    raw = record.get("response")
    claims = core.answer_claims(raw)
    if not isinstance(raw, str) or not raw or not claims or not _answer_shape(claims[0], spec["output_format"]):
        return None, None
    val = claims[0]
    units = len(val) if isinstance(val, (list, dict)) else 1
    return units, units / len(raw.encode("utf-8"))


def _num_distinct(values: list[Any]) -> dict:
    """Numerical observables use stable decimal quantization solely for reporting."""
    known = [v for v in values if v is not None]
    def key(v):
        if isinstance(v, float):
            q = Decimal(str(v)).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
            return str(q.copy_abs() if q.is_zero() else q)
        return core.canonical(v)
    unique = {key(v) for v in known}
    return {"known": len(known), "unknown": len(values) - len(known),
            "distinct_values_12_decimal": len(unique), "raw_distinct_values": len({core.canonical(v) for v in known}),
            "min": min(known) if known else None, "max": max(known) if known else None,
            "discriminability": "UNKNOWN" if not known else "NO_DISCRIMINATION" if len(unique) <= 1 else "VARIES"}


def seven_dimensions(records: list[dict], rows: list[dict], references: list[dict] | None,
                     reference_annotations: list[dict] | None = None,
                     reference_metadata: dict | None = None) -> dict:
    """Compute record and group dimensions with fixed denominators.

    `references` require an ID/version/scope and canonical content hash. Specs
    for these reference members may only come from separately supplied bound
    annotations; a provider contract or reference-carried spec is not used.
    """
    t = time.perf_counter_ns()
    if not isinstance(records, list) or not records or not isinstance(rows, list) or len(records) != len(rows):
        raise ValueError("nonempty equally sized records and rows required")
    ids = [r.get("sample_id") for r in records]; rids = [r.get("sample_id") for r in rows]
    if any(not isinstance(x, str) or not x for x in ids + rids) or len(set(ids)) != len(ids) or len(set(rids)) != len(rids) or set(ids) != set(rids):
        raise ValueError("unique candidate/evaluator sample IDs must match")
    by_id = {row["sample_id"]: row for row in rows}; aligned = [by_id[x] for x in ids]
    for record, row in zip(records, aligned):
        if row.get("member_sha256") is not None and row["member_sha256"] != core.digest(record):
            raise ValueError("evaluator member hash mismatch")
        if any(not (row.get(k) is None or type(row.get(k)) is int and row[k] in (0, 1)) for k in ("C", "R", "contract_fidelity")):
            raise ValueError("C/R/contract evidence is not trivalued")
    specs = [_valid_spec(row.get("original_spec")) for row in aligned]
    groups = _group_view(records, specs)
    counts = Counter((s["family"], s["operation"]) for s in (g["spec"] for g in groups) if s is not None)
    unknown = sum(g["spec"] is None for g in groups)
    diversity = diversity_bounds([counts[k] for k in CAPABILITIES], unknown)
    diversity.update({"unit": "original_task_group", "record_count": len(records), "group_count": len(groups),
                      "capability_order": [list(x) for x in CAPABILITIES], "counts": [counts[k] for k in CAPABILITIES],
                      "fixed_k_condition": "all unknown tasks belong to the ten supported capabilities",
                      "interval_type": "logical completion bounds, not confidence intervals"})
    texts = [g["text"] for g in groups]; known_specs = [core.canonical(g["spec"]) for g in groups if g["spec"] is not None]
    unique_specs = len(set(known_specs)); n = len(groups)
    diversity["text_distinct"] = {"unique": len(set(texts)), "total": n, "ratio": len(set(texts)) / n,
                                  "meaning": "distinct original instruction/input strings, not semantic diversity"}
    diversity["canonical_task_distinct"] = {"known_distinct": unique_specs, "known_groups": len(known_specs),
        "unknown_groups": unknown, "all_group_logical_interval": [max(unique_specs, 1 if unknown else 0), unique_specs + unknown],
        "meaning": "exact normalized known specifications including parameters; not general semantic diversity"}
    has_refs = references is not None
    if has_refs:
        if (not isinstance(references, list) or not references or not isinstance(reference_metadata, dict)
            or any(not reference_metadata.get(x) for x in ("reference_id", "version", "scope", "reference_sha256"))
            or core.digest(references) != reference_metadata["reference_sha256"]):
            raise ValueError("reference list needs nonempty ID, version, scope and matching canonical hash")
        ref_texts = [_text(x) for x in references]
        if any(x is None for x in ref_texts): raise ValueError("reference text missing")
        ref_specs, ref_status = _reference_specs(references, reference_annotations or [])
        ref_exact = set(ref_texts); ref_normalized = {" ".join(x.casefold().split()) for x in ref_texts}
        ref_keys = {core.canonical(x) for x in ref_specs if x is not None}
        similarities = _lexical_tf_idf(references, records)
    else:
        ref_specs, ref_status, ref_exact, ref_normalized, ref_keys = [], {"states": {}, "ignored": []}, set(), set(), set()
        similarities = [None] * len(records)
    intra, cross = _consistencies(records, specs)
    output = []
    for record, row, spec, insame, crosssame, similarity in zip(records, aligned, specs, intra, cross, similarities):
        text = _text(record)
        duplicate = (1 if core.canonical(spec) in ref_keys else (0 if all(x is not None for x in ref_specs) else None)) if has_refs and spec is not None else None
        if spec is None: size, unit, ops = None, None, None
        elif spec["family"] == "arithmetic":
            size, unit, ops = len(spec["arguments"]), "operands", 2 if spec["operation"] == "compound_add_multiply" else 1
        elif spec["family"] == "string":
            size, unit, ops = len(spec["arguments"]["text"]), "characters", 1
        else:
            size, unit, ops = len(spec["arguments"]["numbers"]), "list elements", 2 if spec["operation"] in ("count_sum", "sum_even") else 1
        units, density = _verified_units(record, row, spec)
        tokens = re.findall(r"\w+", record["response"].casefold()) if isinstance(record.get("response"), str) else []
        redundant = 1 - len(set(tokens))/len(tokens) if tokens else None
        output.append({"sample_id": record["sample_id"], "group_id": record.get("group_id", record["sample_id"]),
            "member_sha256": core.digest(record), "features": {
              "correctness": {"value": row.get("C"), "status": "UNKNOWN" if row.get("C") is None else "OBSERVED"},
              "complexity": {"input_size": size, "input_size_unit": unit, "primitive_operations": ops, "reasoning_steps": None, "learning_difficulty": None,
                             "status": "UNKNOWN" if spec is None else "STRUCTURAL_PROXY"},
              "novelty": {"exact_text_duplicate": int(text in ref_exact) if has_refs and text is not None else None,
                          "normalized_text_duplicate": int(" ".join(text.casefold().split()) in ref_normalized) if has_refs and text is not None else None,
                          "canonical_task_duplicate": duplicate, "max_char_tfidf_similarity": similarity,
                          "similarity_status": "LEXICAL_PROXY" if similarity is not None else "UNKNOWN"},
              "consistency": {"intra_answer": insame, "cross_record": crosssame, "instruction_contract": row.get("contract_fidelity")},
              "information_density": {"verified_output_atomic_units": units, "verified_units_per_response_byte": density,
                                      "lexical_redundancy_proxy": redundant,
                                      "status": "UNKNOWN" if density is None else "VERIFIED_OUTPUT_STRUCTURE_PROXY"},
              "task_relevance": {"value": row.get("R"), "status": "UNKNOWN" if row.get("R") is None else "OBSERVED"}}})
    fields = {"correctness": ("correctness", "value"), "complexity_input_size": ("complexity", "input_size"),
      "complexity_primitive_operations": ("complexity", "primitive_operations"), "novelty_exact_duplicate": ("novelty", "exact_text_duplicate"),
      "novelty_canonical_duplicate": ("novelty", "canonical_task_duplicate"), "novelty_lexical_similarity": ("novelty", "max_char_tfidf_similarity"),
      "consistency_intra_answer": ("consistency", "intra_answer"), "consistency_cross_record": ("consistency", "cross_record"),
      "consistency_instruction_contract": ("consistency", "instruction_contract"), "density_verified_units_per_byte": ("information_density", "verified_units_per_response_byte"),
      "density_lexical_redundancy": ("information_density", "lexical_redundancy_proxy"), "task_relevance": ("task_relevance", "value")}
    discrim = {name: _num_distinct([x["features"][d][field] for x in output]) for name, (d, field) in fields.items()}
    return {"rows": output, "batch_diversity": diversity, "reference_metadata": reference_metadata if has_refs else None,
            "reference_size": len(references) if has_refs else 0, "reference_specs_known": sum(x is not None for x in ref_specs),
            "reference_annotation_status": ref_status, "discriminability": discrim,
            "timing": {"wall_ns": time.perf_counter_ns() - t, "scope": "local computation; excludes obtaining AI annotations"},
            "semantic_boundary": "logical bounds and conditional interpretations are not statistical risk or model utility"}
