"""Strict adapter for saved research results; this module never trains models.

The local evaluator pins the registry bytes in consumer configuration. A browser
cannot upload measurements, alter evidence, or turn a test oracle into a policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

EXACT_KEYS = ('asset_id', 'version', 'content_hash', 'task_id', 'model_id',
              'buyer_id', 'scenario_id', 'distribution_id', 'protocol_hash')
CONTEXT_KEYS = ('task_id', 'model_id', 'buyer_id', 'scenario_id', 'distribution_id', 'protocol_hash')
VALUE_STATES = {'POSITIVE_VALUE_EVIDENCE', 'NEGATIVE_VALUE_EVIDENCE', 'VALUE_UNCERTAIN',
                'NOT_EVALUATED', 'TASK_MODEL_UNRELIABLE'}
SHA256 = re.compile(r'^[0-9a-f]{64}$')
MAX_REGISTRY_BYTES = 32 * 1024 * 1024


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('VALUE_REGISTRY_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('VALUE_REGISTRY_NONFINITE_NUMBER')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


class SavedValueRegistry:
    """Immutable snapshot of a hash-pinned, evaluator-owned JSON registry."""

    def __init__(self, path: Path | None = None, expected_sha256: str | None = None):
        self.path = path
        self.sha256 = None
        self._rows: dict[tuple[str, ...], dict[str, Any]] = {}
        self._selections: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {'status': 'NOT_CONFIGURED'}
        if path is None:
            return
        if path.is_symlink() or not path.is_file():
            raise ValueError('VALUE_REGISTRY_MISSING_OR_SYMLINK')
        if path.stat().st_size > MAX_REGISTRY_BYTES:
            raise ValueError('VALUE_REGISTRY_SIZE_LIMIT')
        raw = path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if not expected_sha256 or not SHA256.fullmatch(expected_sha256) or self.sha256 != expected_sha256:
            raise ValueError('VALUE_REGISTRY_HASH_MISMATCH')
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('VALUE_REGISTRY_NONFINITE_NUMBER')))
        _finite(document)
        if not isinstance(document, dict) or document.get('schema_version') != 1 or document.get('step') != 'task_value':
            raise ValueError('VALUE_REGISTRY_SCHEMA_OR_STEP_INVALID')
        rows = document.get('records')
        if not isinstance(rows, list) or len(rows) > 20000:
            raise ValueError('VALUE_REGISTRY_RECORDS_INVALID')
        protocol_hash = document.get('protocol_hash')
        if not isinstance(protocol_hash, str) or not SHA256.fullmatch(protocol_hash):
            raise ValueError('VALUE_REGISTRY_PROTOCOL_HASH_INVALID')
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] or len(row[k]) > 200 for k in EXACT_KEYS):
                raise ValueError('VALUE_REGISTRY_EXACT_KEYS_REQUIRED')
            if not SHA256.fullmatch(row['content_hash']) or row['protocol_hash'] != protocol_hash:
                raise ValueError('VALUE_REGISTRY_CONTENT_OR_PROTOCOL_HASH_INVALID')
            if row.get('task_type') not in {'classification', 'regression'} or not row.get('loss_unit') or not row.get('loss_name'):
                raise ValueError('VALUE_REGISTRY_TASK_AND_LOSS_UNIT_REQUIRED')
            validation = row.get('validation')
            if not isinstance(validation, dict) or validation.get('value_status') not in VALUE_STATES:
                raise ValueError('VALUE_REGISTRY_VALIDATION_STATUS_REQUIRED')
            status = validation['value_status']
            if status not in {'NOT_EVALUATED', 'TASK_MODEL_UNRELIABLE'}:
                values = [validation.get(k) for k in ('mean_utility', 'ci_lower', 'ci_upper')]
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    raise ValueError('VALUE_REGISTRY_FINITE_VALIDATION_METRICS_REQUIRED')
                _, lower, upper = values
                if lower > upper or (status == 'POSITIVE_VALUE_EVIDENCE' and lower <= 0) or (status == 'NEGATIVE_VALUE_EVIDENCE' and upper >= 0):
                    raise ValueError('VALUE_REGISTRY_INTERVAL_OR_STATUS_CONFLICT')
                if status == 'VALUE_UNCERTAIN' and not lower <= 0 <= upper:
                    raise ValueError('VALUE_REGISTRY_INTERVAL_OR_STATUS_CONFLICT')
            if not isinstance(row.get('test_posthoc'), dict) or not isinstance(row.get('trust'), dict):
                raise ValueError('VALUE_REGISTRY_TEST_AND_TRUST_SEPARATION_REQUIRED')
            key = tuple(row[k] for k in EXACT_KEYS)
            if key in self._rows:
                raise ValueError('VALUE_REGISTRY_DUPLICATE_EXACT_MEASUREMENT')
            self._rows[key] = copy.deepcopy(row)
        selections = document.get('selection_results', [])
        if not isinstance(selections, list):
            raise ValueError('VALUE_REGISTRY_SELECTIONS_INVALID')
        self._selections = copy.deepcopy(selections)
        self.metadata = {k: copy.deepcopy(v) for k, v in document.items() if k not in {'records', 'selection_results'}}
        self.metadata.update(status='READY', registry_sha256=self.sha256, record_count=len(self._rows))

    def options(self) -> dict[str, Any]:
        return {'registry': copy.deepcopy(self.metadata), 'exact_keys': list(EXACT_KEYS),
                'records': [{**{k: row[k] for k in EXACT_KEYS},
                             **{k: row.get(k) for k in ('dataset_id', 'task_type', 'loss_name', 'loss_unit', 'asset_seed', 'd0_size', 'row_count')},
                             'value_status': row['validation']['value_status']}
                            for row in self._rows.values()],
                'disclosure': 'Saved exact-version observations only. Validation selects; test is posthoc. No market price.'}

    def lookup(self, query: Mapping[str, str]) -> dict[str, Any] | None:
        row = self._rows.get(tuple(query[k] for k in EXACT_KEYS))
        return copy.deepcopy(row)

    def comparisons(self, query: Mapping[str, str]) -> dict[str, Any]:
        same = lambda row: all(row.get(k) == query[k] for k in CONTEXT_KEYS)
        return {'candidates': [copy.deepcopy(row) for row in self._rows.values() if same(row)],
                'selections': [copy.deepcopy(row) for row in self._selections if same(row)],
                'test_oracle_is_deployable': False}
