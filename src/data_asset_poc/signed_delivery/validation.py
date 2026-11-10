"""CSV byte-level structure checks, distinct from truth or task quality."""
from __future__ import annotations
import csv
import io
import math
import re
from decimal import Decimal, InvalidOperation
from .security import canonical_hash, parse_time, utcnow

MODES = {"normal", "tamper", "wrong_version", "timeout", "invalid_signature", "schema_mismatch", "unknown"}


def validate_metadata(m: dict, now: str | None = None):
    required = {"asset_id", "provider_id", "version", "content_hash", "schema_hash", "schema", "description",
                "license", "usage_policy", "created_at", "published_at", "provenance"}
    if required - m.keys():
        raise ValueError("MISSING_METADATA_FIELDS:" + ",".join(sorted(required-m.keys())))
    for field in ("asset_id", "version", "provider_id"):
        if not isinstance(m[field], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", m[field]):
            raise ValueError("INVALID_METADATA_ID")
    for field in ("content_hash", "schema_hash"):
        if not isinstance(m[field], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", m[field]):
            raise ValueError("INVALID_HASH")
    if m["schema_hash"] != "sha256:" + canonical_hash(m["schema"]):
        raise ValueError("SCHEMA_HASH_MISMATCH")
    if not isinstance(m["schema"], dict) or not m["schema"]:
        raise ValueError("EMPTY_SCHEMA")
    if any(not isinstance(name, str) or not name or not isinstance(rule, dict)
           or rule.get("dtype") not in {"int64", "float64", "object"} for name, rule in m["schema"].items()):
        raise ValueError("INVALID_SCHEMA_RULE")
    if not parse_time(m["created_at"]) <= parse_time(m["published_at"]) <= parse_time(now or utcnow()):
        raise ValueError("INVALID_PUBLICATION_TIME")
    p = m["usage_policy"]
    if not isinstance(p, dict) or not all(isinstance(p.get(k), list) and p[k] and all(isinstance(x, str) for x in p[k]) for k in ("allowed_consumers", "allowed_purposes")):
        raise ValueError("EXPLICIT_USAGE_POLICY_REQUIRED")
    parse_time(p["expires_at"])


def check_schema(data: bytes, schema: dict) -> tuple[bool, str | None, int]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        # Canonical object hashes sort field keys. Actual CSV order is not a
        # separate identity claim; the exact file hash already binds its order.
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(schema):
            return False, None, 0
        count = 0
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                return False, None, count
            for name, rule in schema.items():
                value = row[name]
                if value == "":
                    continue  # missing cells are measured quality, not delivery failure
                dtype = rule.get("dtype", "object")
                if dtype == "int64":
                    number = Decimal(value)
                    if not number.is_finite() or number != number.to_integral_value() or not -(2**63) <= number < 2**63:
                        return False, None, count
                elif dtype == "float64":
                    number = float(value)
                    if not math.isfinite(number):
                        return False, None, count
            count += 1
        return count > 0, "sha256:" + canonical_hash(schema) if count else None, count
    except (ValueError, UnicodeError, csv.Error, TypeError, AttributeError, InvalidOperation):
        return False, None, 0
