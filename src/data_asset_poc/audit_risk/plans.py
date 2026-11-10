"""Fixed audit plans, pinned before audit results are made available."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..credit.evidence import canonical_hash, parse_time
from .intervals import _integer, _probability

PLAN_VERSION = "reference_evidence-fixed-srs-plan-v2"
SAMPLING_ALGORITHM = "numpy_PCG64_choice_without_replacement_sorted_v1"


def normalized_hash(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("SHA256_REQUIRED")
    text = value.removeprefix("sha256:").lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError("SHA256_REQUIRED")
    return text


def sample_positions(population_size: int, sample_size: int, seed: int) -> list[int]:
    total = _integer(population_size, "POPULATION_SIZE", 1)
    n = _integer(sample_size, "SAMPLE_SIZE", 1)
    _integer(seed, "SEED")
    if n > total:
        raise ValueError("SAMPLE_SIZE_EXCEEDS_POPULATION")
    return sorted(int(x) for x in np.random.default_rng(seed).choice(total, n, replace=False))


def plan_hash(plan: dict[str, Any]) -> str:
    return canonical_hash({k: v for k, v in plan.items() if k != "plan_hash"})


def reference_descriptor_hash(reference: dict[str, Any]) -> str:
    """Pin the entire public reference descriptor, including its audit scope.

    The byte hash alone cannot freeze which fields/comparator/error-bound were
    intended. Extra public descriptor fields are deliberately covered as well;
    changing any of them requires a new plan rather than silent reinterpretation.
    """
    if not isinstance(reference, dict):
        raise ValueError("REFERENCE_DESCRIPTOR_MUST_BE_OBJECT")
    normalized_hash(reference.get("reference_hash"))
    protocol = reference.get("audit_protocol")
    if not isinstance(protocol, dict) or not protocol:
        raise ValueError("NONEMPTY_AUDIT_PROTOCOL_DESCRIPTOR_REQUIRED")
    return canonical_hash(reference)


def create_audit_plan(*, dataset_id: str, version: str, content_hash: str,
                      population_size: int, sample_size: int, seed: int,
                      created_at: str, reference_id: str, epsilon: float,
                      reference_hash: str, reference_descriptor_hash: str,
                      alpha: float = 0.05, claim_scope: str = "reference_conformance",
                      plan_id: str | None = None) -> dict[str, Any]:
    """Create a plan; the evaluator must store its hash independently of uploads.

    This routine proves reproducible row selection, not that someone actually
    preregistered the plan before inspecting outcomes or tried no other seed.
    """
    if any(not isinstance(x, str) or not x.strip() for x in (dataset_id, version, reference_id)):
        raise ValueError("NONEMPTY_DATASET_VERSION_REFERENCE_REQUIRED")
    if claim_scope not in {"reference_conformance", "factual_accuracy", "not_applicable"}:
        raise ValueError("UNKNOWN_CLAIM_SCOPE")
    _probability(epsilon, "EPSILON")
    _probability(alpha, "ALPHA", open_interval=True)
    normalized_hash(content_hash)
    parse_time(created_at)
    plan = {"plan_version": PLAN_VERSION, "dataset_id": dataset_id, "version": version,
            "content_hash": normalized_hash(content_hash), "population_size": population_size,
            "sample_size": sample_size, "seed": seed, "created_at": created_at,
            "reference_id": reference_id, "epsilon": float(epsilon), "alpha": float(alpha),
            "reference_hash": normalized_hash(reference_hash),
            "reference_descriptor_hash": normalized_hash(reference_descriptor_hash),
            "claim_scope": claim_scope, "sampling_mechanism": "simple_random_without_replacement",
            "sampling_algorithm": SAMPLING_ALGORITHM,
            "positions": sample_positions(population_size, sample_size, seed),
            "fixed_sample": True, "optional_stopping": False}
    if plan_id is not None and (not isinstance(plan_id, str) or not plan_id.strip()):
        raise ValueError("PLAN_ID_MUST_BE_NONEMPTY")
    plan["plan_id"] = plan_id or "audit-plan-" + canonical_hash(plan)[:24]
    plan["plan_hash"] = plan_hash(plan)
    return plan
