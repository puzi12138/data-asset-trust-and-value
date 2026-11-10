"""Task independent semantic and evidence kernel.

This module accepts an
independently interpreted task specification, an independently measured content
result, source evidence, and an externally authorised target result.  A provider
contract, a member hash, or an annotation confidence value is never treated as
task truth by this module.
"""
from __future__ import annotations
import hashlib
import json
import re
from typing import Any

VERSION = "IndependentTaskEvidence-v0.1"
MAX_INT = 10_000
MAX_TEXT = 80
MAX_LIST = 12

OPERATIONS = {
    ("arithmetic", "add"): ({"a", "b"}, "json_integer"),
    ("arithmetic", "subtract"): ({"a", "b"}, "json_integer"),
    ("arithmetic", "multiply"): ({"a", "b"}, "json_integer"),
    ("arithmetic", "compound_add_multiply"): ({"a", "b", "c"}, "json_integer"),
    ("string", "reverse"): ({"text"}, "json_string"),
    ("string", "filter_alpha"): ({"text"}, "json_string"),
    ("structured", "count_sum"): ({"numbers"}, "json_count_sum"),
    ("structured", "sum_even"): ({"numbers"}, "json_integer"),
    ("list", "sort_ascending"): ({"numbers"}, "json_list_integer"),
    ("list", "filter_even"): ({"numbers"}, "json_list_integer"),
}

def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)

def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()

def strict_json(text: str) -> Any:
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out
    def nonfinite(_):
        raise ValueError("non-finite number")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)

def _int(x: Any) -> bool:
    return type(x) is int and -MAX_INT <= x <= MAX_INT

def normalize(spec: Any) -> dict:
    """Validate and canonicalise a complete Task task specification.

    Add and multiply are commutative, so their operands are sorted in the
    canonical representation.  Other operations preserve argument order.
    """
    if not isinstance(spec, dict) or set(spec) != {"family", "operation", "arguments", "output_format"}:
        raise ValueError("spec schema")
    family, operation, args, output = spec["family"], spec["operation"], spec["arguments"], spec["output_format"]
    if not all(type(v) is str for v in (family, operation, output)):
        raise ValueError("spec type")
    key = (family, operation)
    if key not in OPERATIONS or output != OPERATIONS[key][1] or not isinstance(args, dict):
        raise ValueError("unsupported operation or output format")
    required = OPERATIONS[key][0]
    if set(args) != required:
        raise ValueError("argument schema")
    a = dict(args)
    if family == "arithmetic":
        if not all(_int(a[k]) for k in required):
            raise ValueError("integer argument")
        if operation in ("add", "multiply"):
            a = dict(zip(("a", "b"), sorted((a["a"], a["b"]))))
    elif family == "string":
        if type(a["text"]) is not str or len(a["text"]) > MAX_TEXT:
            raise ValueError("string argument")
    else:
        xs = a["numbers"]
        if type(xs) is not list or not 1 <= len(xs) <= MAX_LIST or not all(_int(x) for x in xs):
            raise ValueError("integer list argument")
        a["numbers"] = list(xs)
    return {"family": family, "operation": operation, "arguments": a,
            "output_format": output}

def expected(spec: Any) -> Any:
    s = normalize(spec)
    f, o, a = s["family"], s["operation"], s["arguments"]
    if f == "arithmetic":
        if o == "add": return a["a"] + a["b"]
        if o == "subtract": return a["a"] - a["b"]
        if o == "multiply": return a["a"] * a["b"]
        return a["a"] + a["b"] * a["c"]
    if f == "string":
        if o == "reverse": return a["text"][::-1]
        return "".join(ch for ch in a["text"] if "A" <= ch <= "Z" or "a" <= ch <= "z")
    if f == "structured":
        if o == "count_sum": return {"count": len(a["numbers"]), "sum": sum(a["numbers"])}
        return sum(x for x in a["numbers"] if x % 2 == 0)
    if o == "sort_ascending": return sorted(a["numbers"])
    return [x for x in a["numbers"] if x % 2 == 0]

def _valid_answer(value: Any, output: str) -> bool:
    if output == "json_integer": return type(value) is int
    if output == "json_string": return type(value) is str
    if output == "json_count_sum": return (isinstance(value, dict) and set(value) == {"count", "sum"}
        and all(type(value[k]) is int for k in ("count", "sum")))
    if output == "json_list_integer": return type(value) is list and all(type(x) is int for x in value)
    return False

def answer_claims(response: Any) -> list | None:
    if not isinstance(response, str) or len(response) > 100_000:
        return None
    text = response.strip()
    if text.startswith("Result: "): text = text[8:]
    elif text.startswith("Final: "): text = text[7:]
    m = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    if m: text = m.group(1)
    try: value = strict_json(text)
    except (ValueError, TypeError, RecursionError, json.JSONDecodeError): return None
    if isinstance(value, dict) and set(value) == {"answer"}: return [value["answer"]]
    if isinstance(value, dict) and set(value) == {"claims"}:
        return value["claims"] if type(value["claims"]) is list and len(value["claims"]) == 2 else None
    return [value]

def correct(spec: Any, response: Any) -> int | None:
    if spec is None: return None
    try: s = normalize(spec)
    except (ValueError, TypeError, KeyError): return None
    claims = answer_claims(response)
    if claims is None: return None
    wanted = expected(s)
    return int(all(_valid_answer(v, s["output_format"]) and v == wanted for v in claims))

def task_binding(record: dict) -> str:
    return digest({"instruction": record.get("instruction"), "input": record.get("input", "")})

_ANNOTATION_FIELDS = {"annotation_id", "group_id", "sample_id", "task_binding", "status",
                      "spec", "evidence_spans", "uncertainty", "reason", "source_id"}

def validate_annotation(annotation: Any, record: dict) -> dict | None:
    """Validate an annotation's binding and spans, without treating it as truth."""
    if not isinstance(annotation, dict) or not set(annotation).issubset(_ANNOTATION_FIELDS):
        raise ValueError("annotation fields")
    if annotation.get("task_binding") != task_binding(record): raise ValueError("annotation binding")
    for field in ("group_id", "sample_id"):
        if field in annotation and annotation[field] != record.get(field):
            raise ValueError("annotation " + field + " binding")
    if not isinstance(annotation.get("source_id"), str) or not annotation["source_id"].strip():
        raise ValueError("annotation source")
    status = annotation.get("status")
    if status not in ("PARSED", "UNKNOWN", "CONFLICT"): raise ValueError("annotation status")
    spans = annotation.get("evidence_spans", [])
    if not isinstance(spans, list): raise ValueError("annotation spans")
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"field", "start", "end", "quote"}:
            raise ValueError("span schema")
        field, start, end, quote = span["field"], span["start"], span["end"], span["quote"]
        text = record.get(field) if field in ("instruction", "input") else None
        if type(start) is not int or type(end) is not int or type(quote) is not str or text is None:
            raise ValueError("span type")
        if not (0 <= start < end <= len(text)) or text[start:end] != quote:
            raise ValueError("span offsets")
    if status != "PARSED":
        if annotation.get("spec") is not None: raise ValueError("unknown annotation has spec")
        return None
    if not spans: raise ValueError("parsed annotation needs evidence")
    return normalize(annotation.get("spec"))

def combine_annotations(record: dict, annotations: Any) -> tuple[dict | None, str, list[dict], list[str]]:
    """Require the first two independent sources to agree; later ones diagnose only."""
    if not isinstance(annotations, (list, tuple)): raise ValueError("annotations")
    sources, ignored, specs = [], [], []
    for item in annotations[:2]:
        try:
            spec = validate_annotation(item, record)
        except (ValueError, TypeError, KeyError):
            ignored.append("INVALID_OR_UNBOUND_ANNOTATION"); continue
        if spec is not None:
            specs.append(spec); sources.append({"source_id": item["source_id"], "spec": spec,
                "annotation_sha256": digest(item), "evidence_spans": item.get("evidence_spans", [])})
    keys = {canonical(s) for s in specs}
    unique_sources = {s["source_id"] for s in sources}
    if len(keys) > 1: return None, "CONFLICT", sources, ignored
    if len(unique_sources) >= 2: return specs[0], "AI_AGREED", sources, ignored
    if specs: return None, "AI_SINGLE", sources, ignored
    return None, "UNKNOWN", sources, ignored

def tri_and(values: Any) -> int | None:
    values = list(values)
    if any(not (v is None or (type(v) is int and v in (0, 1))) for v in values):
        raise ValueError("tri-valued evidence must be integer 0, integer 1 or None")
    if any(v == 0 for v in values): return 0
    if any(v is None for v in values): return None
    if all(v == 1 for v in values): return 1
    raise ValueError("tri-valued evidence must be 0, 1 or None")

def bounds(value: int | None) -> list[int]: return [0, 1] if value is None else [value, value]

def decision(value: int | None) -> str:
    return {1: "ACCEPT", 0: "REJECT", None: "ABSTAIN"}[value]

def simple_same_evidence_gate(e: int | None, c: int | None, r: int | None) -> str:
    """Ordinary selective conjunction, directly comparable with interval output."""
    return decision(tri_and((e, c, r)))

def summarize(rows: list[dict]) -> dict:
    """Fixed member denominator, including unknowns; logical, not a CI."""
    if not rows or len({r.get("sample_id") for r in rows}) != len(rows):
        raise ValueError("nonempty rows with unique sample_id required")
    n = len(rows)
    return {key: [sum(r[key][i] for r in rows) / n for i in (0, 1)]
            for key in ("strict_interval", "content_interval")}

_PROFILE_FIELDS = {"task_id", "task_version", "task_description", "required_capabilities",
                   "expected_output", "evaluation_criteria", "task_source", "task_profile_sha256"}
_OUTPUTS = {v[1] for v in OPERATIONS.values()}

def valid_target_profile(profile: Any) -> bool:
    """Validate target content and hash; this is not caller authentication."""
    if not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS:
        return False
    if any(type(profile[k]) is not str or not profile[k].strip()
           for k in ("task_id", "task_version", "task_description", "task_source")):
        return False
    if (profile["evaluation_criteria"] != "exact_deterministic_answer"
        or type(profile["expected_output"]) is not str or profile["expected_output"] not in _OUTPUTS):
        return False
    caps = profile["required_capabilities"]
    if type(caps) is not list or not caps:
        return False
    for c in caps:
        if not isinstance(c, dict) or set(c) != {"family", "operation"}:
            return False
        if type(c["family"]) is not str or type(c["operation"]) is not str:
            return False
        key = (c["family"], c["operation"])
        if key not in OPERATIONS or OPERATIONS[key][1] != profile["expected_output"]:
            return False
    try:
        return profile["task_profile_sha256"] == digest({k: v for k, v in profile.items() if k != "task_profile_sha256"})
    except (TypeError, ValueError):
        return False

def target_compatibility(spec: Any, response: Any, profile: Any) -> int | None:
    """Capability/output check only. Caller must separately supply authorization."""
    if not valid_target_profile(profile): return None
    try: s = normalize(spec)
    except (ValueError, TypeError, KeyError): return None
    caps = profile.get("required_capabilities")
    if not isinstance(caps, list): return None
    try: capset = {(c["family"], c["operation"]) for c in caps}
    except (TypeError, KeyError): return None
    if (s["family"], s["operation"]) not in capset: return 0
    expected_output = profile.get("expected_output")
    if not isinstance(expected_output, str): return None
    if expected_output != s["output_format"]: return 0
    if not isinstance(response, str): return 0
    try: value = strict_json(response.strip())
    except (ValueError, TypeError, RecursionError): return 0
    return int(_valid_answer(value, expected_output))

def evaluate_record(record: dict, spec: Any, content_value: int | None,
                    source_value: int | None, target_profile: Any,
                    target_value: int | None) -> dict:
    """Propagate independent C, source E and authorised target R.

    `target_value` is supplied by the authority layer after identity, scope,
    version and revocation checks.  This function never treats a profile hash as
    caller authentication.
    """
    try: normalized = normalize(spec) if spec is not None else None
    except (ValueError, TypeError, KeyError): normalized = None
    try: provider = normalize(record.get("contract"))
    except (ValueError, TypeError, KeyError): provider = None
    fidelity = None if normalized is None or provider is None else int(normalized == provider)
    for name, value in (("content_value", content_value), ("source_value", source_value), ("target_value", target_value)):
        if not (value is None or (type(value) is int and value in (0, 1))):
            raise ValueError(name + " must be integer 0, integer 1, or None")
    # A content result cannot be asserted without a known original task.
    # Absence is not permission to compute the expensive content action here.
    c = None if normalized is None else content_value
    # A target profile is not used for an evaluation before authority has been
    # checked. An explicitly unauthorised profile produces rejection.
    r = (target_compatibility(normalized, record.get("response"), target_profile)
         if target_value == 1 else target_value)
    e = tri_and((fidelity, source_value))
    strict = tri_and((e, c, r)); content = tri_and((c, r))
    return {"sample_id": record.get("sample_id"), "original_spec": normalized,
            "contract_fidelity": fidelity, "E": e, "C": c, "R": r,
            "strict_decision": decision(strict), "content_decision": decision(content),
            "strict_interval": bounds(strict), "content_interval": bounds(content),
            "strict_asset_admission": decision(strict),
            "training_content_eligibility": decision(content),
            "target_profile_supplied": isinstance(target_profile, dict),
            "evidence_semantics": "source affects E only; target_value is independently authorised; content is acquired explicitly"}

