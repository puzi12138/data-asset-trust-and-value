"""Prepare frozen UCI Letter Recognition inputs, without downloading or fitting.

The four data roles are allocated by whole identical-feature groups.  Only the
training/pool records enter selection_input; evaluation labels are held in
separate inputs.  The output is a fresh directory and never overwrites data.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import statistics
import string
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
LABELS = list(string.ascii_uppercase)
SEEDS = (1103, 2207, 3301)
MINIMUMS = {'D0': 512, 'pool': 8192, 'dev': 1024, 'test': 2048}
OUTPUT_KEYS = ('selection_input', 'audit_reference', 'development_input',
               'test_input', 'injection_truth', 'members', 'source_record',
               'preparation')
FILES = {'selection_input': 'selection_input.json',
         'audit_reference': 'audit_reference.json',
         'development_input': 'development_input.json',
         'test_input': 'test_input.json',
         'injection_truth': 'injection_truth.json', 'members': 'MEMBERS.json',
         'source_record': 'source_record.json', 'preparation': 'preparation.json'}


def sha_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def _json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf8')


def _write_exclusive(path, blob):
    with Path(path).open('xb') as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())


def validate_source(source, raw):
    """Bind source metadata to raw bytes; this does not establish authenticity."""
    if not isinstance(source, dict) or source.get('dataset') != 'Letter Recognition':
        raise ValueError('SOURCE_DATASET_MISMATCH')
    declared = source.get('raw_sha256')
    if not isinstance(declared, str) or not re.fullmatch(r'[0-9a-f]{64}', declared):
        raise ValueError('SOURCE_SHA256_INVALID')
    size = source.get('raw_size_bytes')
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError('SOURCE_SIZE_INVALID')
    if len(raw) != size or sha_bytes(raw) != declared:
        raise ValueError('SOURCE_BYTES_MISMATCH')
    parsed = urlparse(source.get('source_url', ''))
    if parsed.scheme != 'https' or parsed.hostname != 'archive.ics.uci.edu':
        raise ValueError('SOURCE_URL_INVALID')
    if source.get('license') != 'CC BY 4.0':
        raise ValueError('SOURCE_LICENSE_UNEXPECTED')
    return declared


def parse_letter(raw, expected_rows=20000):
    """Parse the official 17-column format, assigning one-based source rows."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 10_000_000:
        raise ValueError('RAW_SIZE_INVALID')
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ValueError('RAW_ENCODING_INVALID') from exc
    result = []
    for n, fields in enumerate(csv.reader(io.StringIO(text, newline='')), start=1):
        if len(fields) != 17:
            raise ValueError(f'INVALID_COLUMNS_AT_SOURCE_ROW_{n}')
        label = fields[0].strip()
        if label not in LABELS:
            raise ValueError(f'INVALID_LABEL_AT_SOURCE_ROW_{n}')
        try:
            # Official attributes are integer-valued, each between 0 and 15.
            values = [int(v.strip()) for v in fields[1:]]
        except ValueError as exc:
            raise ValueError(f'INVALID_FEATURE_AT_SOURCE_ROW_{n}') from exc
        if any(not 0 <= v <= 15 for v in values):
            raise ValueError(f'FEATURE_OUT_OF_RANGE_AT_SOURCE_ROW_{n}')
        result.append({'id': f'letter:{n:05d}', 'source_row': n,
                       'x': values, 'y': label})
    if len(result) != expected_rows:
        raise ValueError('RAW_ROW_COUNT_MISMATCH')
    if {r['y'] for r in result} != set(LABELS):
        raise ValueError('RAW_LABEL_SET_MISMATCH')
    return result


def group_and_split(records, source_sha, seed, minimums=None, d0_limit=768):
    """Partition entire exact-feature groups by the published hash ordering.

    Cover all D0 classes first.  Thereafter revisit previously skipped groups
    in the same original order; every group is allocated at most once.
    """
    if seed not in SEEDS or not re.fullmatch(r'[0-9a-f]{64}', source_sha):
        raise ValueError('INVALID_SPLIT_SEED_OR_SOURCE_SHA')
    minimums = dict(MINIMUMS if minimums is None else minimums)
    if set(minimums) != {'D0', 'pool', 'dev', 'test'} or any(
        isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in minimums.values()
    ):
        raise ValueError('INVALID_ROLE_MINIMUMS')
    groups_by_features = {}
    observed_rows = set()
    for r in records:
        source_row = r['source_row']
        if source_row in observed_rows:
            raise ValueError('DUPLICATE_SOURCE_ROW')
        observed_rows.add(source_row)
        groups_by_features.setdefault(tuple(r['x']), []).append(r)
    groups = []
    for members in groups_by_features.values():
        members.sort(key=lambda r: r['source_row'])
        key = ','.join(str(r['source_row']) for r in members)
        sort_key = sha_bytes(f'{source_sha}:{seed}:{key}'.encode('utf8'))
        groups.append((sort_key, key, members))
    groups.sort(key=lambda v: (v[0], v[1]))
    selected = set()
    d0_groups = []
    uncovered = set(LABELS)
    for _, key, members in groups:
        present = {r['y'] for r in members}
        if present & uncovered:
            d0_groups.append((key, members))
            selected.add(key)
            uncovered.difference_update(present)
            if not uncovered:
                break
    if uncovered:
        raise ValueError('D0_CLASS_COVERAGE_IMPOSSIBLE')
    d0_count = sum(len(members) for _, members in d0_groups)
    for _, key, members in groups:
        if d0_count >= minimums['D0']:
            break
        if key not in selected:
            d0_groups.append((key, members))
            selected.add(key)
            d0_count += len(members)
    if d0_count < minimums['D0'] or d0_count > d0_limit:
        raise ValueError('D0_SIZE_BELOW_MINIMUM_OR_ABOVE_768')
    allocated = {'D0': d0_groups}
    remaining = [(key, members) for _, key, members in groups if key not in selected]
    cursor = 0
    for role in ('pool', 'dev', 'test'):
        role_groups = []
        count = 0
        while count < minimums[role] and cursor < len(remaining):
            key, members = remaining[cursor]
            cursor += 1
            role_groups.append((key, members))
            count += len(members)
        if count < minimums[role]:
            raise ValueError('NOT_RUN_INSUFFICIENT_GROUPED_ROWS:' + role)
        allocated[role] = role_groups
    allocated['unassigned'] = remaining[cursor:]
    role_rows = {}
    for role, role_groups in allocated.items():
        rows = []
        for key, members in role_groups:
            for member in members:
                rows.append({**member, 'group': key})
        role_rows[role] = sorted(rows, key=lambda r: r['source_row'])
    return role_rows


def build_payloads(records, source, seed, minimums=None, reference_count=256):
    """Create selection/evaluation payloads and controlled source mutations."""
    source_sha = source['raw_sha256']
    roles = group_and_split(records, source_sha, seed, minimums=minimums)
    if len(roles['pool']) < reference_count or len(roles['pool']) < 512:
        raise ValueError('NOT_RUN_INSUFFICIENT_POOL')
    original = sorted(roles['D0'] + roles['pool'], key=lambda r: r['source_row'])
    rows = copy.deepcopy(original)
    source_index = {r['source_row']: i for i, r in enumerate(rows)}
    d0 = [source_index[r['source_row']] for r in roles['D0']]
    pool = [source_index[r['source_row']] for r in roles['pool']]
    assert d0 == sorted(d0) and pool == sorted(pool)
    reference_indices = random.Random(seed + 160000).sample(pool, reference_count)
    references = [{'index': i, 'id': original[i]['id'],
                   'x': list(original[i]['x']), 'y': original[i]['y']}
                  for i in reference_indices]
    n = math.floor(.2 * len(pool))
    changed = random.Random(seed + 170000).sample(pool, n)
    label_count = n // 2
    vals = [float(rows[i]['x'][0]) for i in d0]
    extreme = max(vals) + 3 * (statistics.pstdev(vals) or 1.)
    truth = []
    for position, i in enumerate(changed):
        old = original[i]
        kind = 'label' if position < label_count else 'feature'
        if kind == 'label':
            rows[i]['y'] = LABELS[(LABELS.index(rows[i]['y']) + 1) % len(LABELS)]
        else:
            rows[i]['x'][0] = extreme
        if rows[i]['x'] == old['x'] and rows[i]['y'] == old['y']:
            raise ValueError('CONTROLLED_MODIFICATION_DID_NOT_CHANGE_RECORD')
        truth.append({'index': i, 'kind': kind,
                      'original_x': list(old['x']), 'original_y': old['y']})
    names = ('D0', 'pool', 'dev', 'test', 'unassigned')
    member_rows = {role: [{'source_row': r['source_row'], 'id': r['id'],
                           'group': r['group']} for r in roles[role]] for role in names}
    members = {'roles': member_rows, 'unassigned_count': len(roles['unassigned']),
               'source_sha256': source_sha, 'seed': seed,
               'group_definition': 'exact 16-feature vector; key is sorted one-based source rows joined by comma',
               'split_order': 'sha256(raw_sha256:seed:group_key), hex lexical order',
               'reference_source_rows': [original[i]['source_row'] for i in reference_indices]}
    minima = MINIMUMS if minimums is None else minimums
    preparation = {'scope': 'PUBLIC_DATA_CONTROLLED_VERSION_MODIFICATIONS_NOT_NATURAL_TRUTH',
                   'seed': seed, 'source_sha256': source_sha,
                   'role_counts': {r: len(roles[r]) for r in names},
                   'role_minimums': dict(minima),
                   'whole_group_excess': {r: len(roles[r]) - minima[r]
                                          for r in ('D0', 'pool', 'dev', 'test')},
                   'unique_groups': {r: len({x['group'] for x in roles[r]}) for r in names},
                   'reference_count': reference_count, 'controlled_modifications': n,
                   'label_modifications': label_count, 'feature_modifications': n-label_count,
                   'feature_mutation_index': 0, 'feature_mutation_value': extreme,
                   'd0_class_labels': sorted({rows[i]['y'] for i in d0}),
                   'unseen_entity_relationships': 'NOT_CHECKED', 'training': 'NOT_RUN'}
    task = {'type': 'classification', 'feature_count': 16, 'allowed_labels': LABELS}
    asset = {'dataset': 'Letter Recognition', 'source_sha256': source_sha}
    return {'selection_input': {'rows': rows, 'roles': {'D0': d0, 'pool': pool},
                                'task': task, 'asset_metadata': asset},
            'audit_reference': {'records': references,
                                'scope': 'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'},
            'development_input': {'records': roles['dev']},
            'test_input': {'records': roles['test']},
            'injection_truth': {'records': truth,
                                'scope': 'CONTROLLED_MODIFICATIONS_NOT_NATURAL_ERRORS'},
            'members': members, 'source_record': source, 'preparation': preparation}


def prepare(raw_path, source_path, seed, output):
    if seed not in SEEDS:
        raise ValueError('UNKNOWN_SEED')
    raw = Path(raw_path).read_bytes()
    source = json.loads(Path(source_path).read_text(encoding='utf-8-sig'))
    validate_source(source, raw)
    records = parse_letter(raw)
    payloads = build_payloads(records, source, seed)
    encoded = {key: _json_bytes(payloads[key]) for key in OUTPUT_KEYS}
    config = {'schema': 'external-experiment-v1', 'task_id': 'EXT-02',
              'dataset': 'Letter Recognition', 'seed': seed,
              'task_type': 'classification', 'feature_count': 16,
              'mutation_feature_index': 0, 'budgets': [128, 512],
              'models': ['logistic', 'random_forest'],
              'selection_input': FILES['selection_input'],
              'audit_reference': FILES['audit_reference'],
              'development_input': FILES['development_input'],
              'test_input': FILES['test_input'],
              'injection_truth': FILES['injection_truth'],
              'inputs': [{'path': FILES[key], 'sha256': sha_bytes(encoded[key])}
                         for key in OUTPUT_KEYS]}
    directory = Path(output).resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    directory.mkdir(parents=True, exist_ok=False)
    for key in OUTPUT_KEYS:
        _write_exclusive(directory / FILES[key], encoded[key])
    _write_exclusive(directory / 'config.json', _json_bytes(config))
    return {'status': 'PREPARED', 'task_id': 'EXT-02', 'seed': seed,
            'source_sha256': source['raw_sha256'], 'files': len(encoded) + 1,
            'role_counts': payloads['preparation']['role_counts'],
            'training': 'NOT_RUN'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', required=True, type=Path)
    parser.add_argument('--source-record', required=True, type=Path)
    parser.add_argument('--seed', required=True, type=int, choices=SEEDS)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.raw, args.source_record, args.seed, args.out),
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
