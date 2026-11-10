"""dataset canonical, versioned asset and object digests (Python standard library).

The digest identifies the exact declared bytes after the normalization rules
below. It is not a signature, provenance attestation, or proof of truth.
All consumers in dataset must call this module rather than copy its serialization.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Mapping

NORMALIZATION_VERSION = "ASSET-CANONICAL-1"
_ASSET_FIELDS = frozenset({
    "normalization_version", "dataset_id", "dataset_version", "source_id",
    "source_version", "license_sha256", "schema_sha256", "task_sha256", "records",
})
_RECORD_FIELDS = frozenset({"id", "source_row", "group", "x", "y"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BindingError(ValueError):
    """Invalid or incomplete canonical structure, not a factual judgement."""


def _norm(value: Any, path: str = "$") -> Any:
    """Normalize JSON recursively: NFC text, no implicit missing keys or NaN.

    Boolean, integer, float and string remain different JSON types; in
    particular ``true``, ``1`` and ``1.0`` do not have the same bytes. Arrays
    retain their order. Objects are serialized with sorted normalized keys.
    Finite floats use Python's JSON float serialization; this version is a
    Python JSON contract and is not claimed to be cross-language RFC 8785.
    """
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise BindingError(f"NONFINITE_NUMBER:{path}")
        return value
    if isinstance(value, str):
        result = unicodedata.normalize("NFC", value)
        try:
            result.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BindingError(f"INVALID_UNICODE:{path}") from exc
        return result
    if isinstance(value, list):
        return [_norm(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, Mapping):
        result = {}
        for key, v in value.items():
            if not isinstance(key, str):
                raise BindingError(f"NON_STRING_KEY:{path}")
            normalized = _norm(key, f"{path}.<key>")
            if normalized in result:
                raise BindingError(f"NORMALIZED_KEY_COLLISION:{path}")
            result[normalized] = _norm(v, f"{path}.{normalized}")
        return result
    raise BindingError(f"NON_JSON_TYPE:{path}:{type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """NFC-normalized compact sorted JSON in UTF-8; rejects non-JSON inputs."""
    normalized = _norm(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def json_digest(value: Any) -> str:
    """Digest normalized JSON such as a declared schema, task or receipt."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BindingError(f"INVALID_TEXT:{field}")
    # Identity fields use NFC and trim leading/trailing whitespace. Value
    # strings inside x/y retain their whitespace and are never trimmed.
    return _norm(value, field).strip()


def _optional_hash(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise BindingError(f"INVALID_SHA256:{field}")
    return value


def canonical_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    """Return the dataset normalized asset. Required missing values use explicit null.

    ``id`` must be nonempty and unique; ``source_row`` and ``group`` may be
    explicitly null and are always bound. ``x`` and ``y`` can contain JSON
    nulls so incomplete assets can still be identified and subsequently
    retained as NEEDS_VERIFICATION. A missing key or unexpected key is an error:
    no source metadata is silently discarded.
    """
    if not isinstance(asset, Mapping):
        raise BindingError("ASSET_NOT_OBJECT")
    if set(asset) != _ASSET_FIELDS:
        raise BindingError(f"ASSET_FIELDS:missing={sorted(_ASSET_FIELDS-set(asset))};extra={sorted(set(asset)-_ASSET_FIELDS)}")
    if asset["normalization_version"] != NORMALIZATION_VERSION:
        raise BindingError("NORMALIZATION_VERSION_MISMATCH")
    clean: dict[str, Any] = {"normalization_version": NORMALIZATION_VERSION}
    for field in ("dataset_id", "dataset_version", "source_id", "source_version"):
        clean[field] = _optional_text(asset[field], field)
    for field in ("license_sha256", "schema_sha256", "task_sha256"):
        clean[field] = _optional_hash(asset[field], field)
    if not isinstance(asset["records"], list):
        raise BindingError("RECORDS_NOT_LIST")
    rows = []
    ids = set()
    for i, row in enumerate(asset["records"]):
        if not isinstance(row, Mapping):
            raise BindingError(f"RECORD_NOT_OBJECT:{i}")
        if set(row) != _RECORD_FIELDS:
            raise BindingError(f"RECORD_FIELDS:{i}:missing={sorted(_RECORD_FIELDS-set(row))};extra={sorted(set(row)-_RECORD_FIELDS)}")
        rid = _optional_text(row["id"], f"records[{i}].id")
        if rid is None:
            raise BindingError(f"RECORD_ID_MISSING:{i}")
        if rid in ids:
            raise BindingError(f"DUPLICATE_RECORD_ID:{rid}")
        ids.add(rid)
        source_row = row["source_row"]
        if source_row is not None:
            if type(source_row) is int:
                pass
            elif isinstance(source_row, str) and source_row.strip():
                source_row = _optional_text(source_row, f"records[{i}].source_row")
            else:
                raise BindingError(f"INVALID_SOURCE_ROW:{i}")
        rows.append({"id": rid, "source_row": source_row,
                     "group": _optional_text(row["group"], f"records[{i}].group"),
                     "x": _norm(row["x"], f"records[{i}].x"),
                     "y": _norm(row["y"], f"records[{i}].y")})
    clean["records"] = sorted(rows, key=lambda row: row["id"])
    return clean


def asset_digest(asset: Mapping[str, Any]) -> str:
    """Bind asset metadata and all declared members, invariant to row order."""
    return json_digest(canonical_asset(asset))
