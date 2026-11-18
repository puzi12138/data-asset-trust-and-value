"""Prepare frozen EXT-01 inputs from the official decompressed covtype.data."""
from __future__ import annotations

import argparse
from array import array
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import struct
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from scripts.external_experiments.common import CLASSIFICATION_DATASETS, SEEDS, read, sha, write
from scripts.external_experiments.prepare_letter import FILES, MINIMUMS


DATASET, DIMENSION, LABELS = CLASSIFICATION_DATASETS['EXT-01']
SOURCE_URL = 'https://archive.ics.uci.edu/dataset/31/covertype'
OFFICIAL_ROWS = 581_012
QUANTITATIVE_COLUMNS = 10
FEATURE_VECTOR = struct.Struct('<10iQ')
LABEL_BYTES = {label.encode('ascii'): int(label) for label in LABELS}
EVIDENCE_SCOPE = 'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'
GIB = 1024 ** 3


class PreparationResources:
    """Sample available memory and record the preparation process peak."""

    def __init__(self):
        self.start = time.perf_counter()
        self.process = psutil.Process()
        self.available_at_start = psutil.virtual_memory().available
        if self.available_at_start < 1.5 * GIB:
            raise ValueError('NOT_RUN_PREPARATION_START_MEMORY_BELOW_1_5_GIB')
        self.peak_rss = 0

    def check(self):
        memory = self.process.memory_info()
        self.peak_rss = max(self.peak_rss, getattr(memory, 'peak_wset', memory.rss))
        if self.peak_rss > 1.5 * GIB or psutil.virtual_memory().available < .75 * GIB:
            raise ValueError('STOPPED_PREPARATION_MEMORY_GUARD')

    def receipt(self):
        self.check()
        return {'preparation_seconds_before_config_binding': time.perf_counter() - self.start,
                'preparation_peak_rss_bytes': self.peak_rss,
                'preparation_start_available_bytes': self.available_at_start}


def validate_source(source, raw_path):
    if not isinstance(source, dict) or source.get('dataset') != DATASET:
        raise ValueError('SOURCE_DATASET_MISMATCH')
    if source.get('source_url') != SOURCE_URL:
        raise ValueError('SOURCE_URL_INVALID')
    if source.get('license') != 'CC BY 4.0':
        raise ValueError('SOURCE_LICENSE_UNEXPECTED')
    notice = source.get('copyright_notice', '')
    if not isinstance(notice, str) or not all(name in notice for name in
            ('Jock A. Blackard', 'Colorado State University')):
        raise ValueError('SOURCE_COPYRIGHT_NOTICE_MISSING')
    expected = source.get('raw_sha256')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('SOURCE_SHA256_INVALID')
    size = source.get('raw_size_bytes')
    if type(size) is not int or size <= 0 or size != raw_path.stat().st_size:
        raise ValueError('SOURCE_SIZE_MISMATCH')
    return expected


def decode_row(line, source_row):
    fields = line.rstrip(b'\r\n').split(b',')
    if len(fields) != DIMENSION + 1:
        raise ValueError(f'INVALID_COLUMNS_AT_SOURCE_ROW_{source_row}')
    if fields[-1] not in LABEL_BYTES:
        raise ValueError(f'INVALID_LABEL_AT_SOURCE_ROW_{source_row}')
    flags = fields[QUANTITATIVE_COLUMNS:DIMENSION]
    if any(flag not in (b'0', b'1') for flag in flags):
        raise ValueError(f'INVALID_FLAG_AT_SOURCE_ROW_{source_row}')
    try:
        quantitative = tuple(map(int, fields[:QUANTITATIVE_COLUMNS]))
        packed = FEATURE_VECTOR.pack(*quantitative, int(b''.join(flags), 2))
    except (ValueError, struct.error) as exc:
        raise ValueError(f'INVALID_FEATURE_AT_SOURCE_ROW_{source_row}') from exc
    return packed, quantitative, flags, LABEL_BYTES[fields[-1]]


def scan_groups(raw_path, source_hash, resources, expected_rows=OFFICIAL_ROWS):
    """Stream raw bytes and retain one compact vector key per exact group."""
    first_by_vector, duplicates = {}, {}
    labels = bytearray([0])  # Index by one-based source row.
    digest = sha256()
    with raw_path.open('rb') as stream:
        for source_row, line in enumerate(stream, 1):
            if source_row > expected_rows:
                raise ValueError('RAW_ROW_COUNT_MISMATCH')
            digest.update(line)
            vector, _, _, label = decode_row(line, source_row)
            labels.append(label)
            first = first_by_vector.setdefault(vector, source_row)
            if first != source_row:
                duplicates.setdefault(first, []).append(source_row)
            if source_row % 8192 == 0:
                resources.check()
    resources.check()
    if digest.hexdigest() != source_hash:
        raise ValueError('SOURCE_BYTES_MISMATCH')
    if len(labels) != expected_rows + 1:
        raise ValueError('RAW_ROW_COUNT_MISMATCH')
    if set(labels[1:]) != set(LABEL_BYTES.values()):
        raise ValueError('RAW_LABEL_SET_MISMATCH')
    return array('I', first_by_vector.values()), duplicates, labels


def group_keys(duplicates):
    keys = {}
    for first, later in duplicates.items():
        members = [first, *later]
        key = ','.join(map(str, members))
        keys.update(dict.fromkeys(members, key))
    return keys


def allocate_groups(first_rows, duplicates, labels, keys, source_hash, seed, minimums):
    if set(minimums) != {'D0', 'pool', 'dev', 'test'} or any(
            type(count) is not int or count < 1 for count in minimums.values()):
        raise ValueError('INVALID_ROLE_MINIMUMS')

    def rank(first):
        key = keys.get(first, str(first))
        return sha256(f'{source_hash}:{seed}:{key}'.encode()).digest(), key

    ranked = sorted(first_rows, key=rank)
    allocated = {'D0': []}
    uncovered = set(LABEL_BYTES.values())
    d0_count = 0
    for first in ranked:
        members = [first, *duplicates.get(first, ())]
        found = uncovered.intersection(labels[row] for row in members)
        if found:
            allocated['D0'].append(first)
            d0_count += len(members)
            uncovered.difference_update(found)
        if not uncovered:
            break
    if uncovered:
        raise ValueError('D0_CLASS_COVERAGE_IMPOSSIBLE')
    used_in_cover = set(allocated['D0'])
    remaining = (first for first in ranked if first not in used_in_cover)

    def fill(role, count=0):
        groups = allocated.setdefault(role, [])
        while count < minimums[role]:
            first = next(remaining, None)
            if first is None:
                raise ValueError(f'NOT_RUN_INSUFFICIENT_GROUPED_ROWS:{role}')
            groups.append(first)
            count += 1 + len(duplicates.get(first, ()))
        return count

    if fill('D0', d0_count) > 768:
        raise ValueError('D0_SIZE_ABOVE_768')
    for role in ('pool', 'dev', 'test'):
        fill(role)
    return allocated


def row_id(source_row):
    return f'covertype:{source_row:06d}'


def read_assigned(raw_path, source_hash, roles, keys, resources):
    wanted = {row for source_rows in roles.values() for row in source_rows}
    records = {}
    digest = sha256()
    with raw_path.open('rb') as stream:
        for source_row, line in enumerate(stream, 1):
            digest.update(line)
            if source_row in wanted:
                _, numeric, flags, label = decode_row(line, source_row)
                records[source_row] = {'id': row_id(source_row), 'source_row': source_row,
                                       'group': keys.get(source_row, str(source_row)),
                                       'x': [*numeric, *map(int, flags)], 'y': str(label)}
            if source_row % 8192 == 0:
                resources.check()
    if digest.hexdigest() != source_hash or records.keys() != wanted:
        raise ValueError('SOURCE_CHANGED_DURING_PREPARATION')
    return {role: [records[row] for row in source_rows] for role, source_rows in roles.items()}


def make_selection(roles, seed, source_hash):
    original = sorted(roles['D0'] + roles['pool'], key=lambda record: record['source_row'])
    rows = [{**record, 'x': list(record['x'])} for record in original]
    index = {record['source_row']: i for i, record in enumerate(original)}
    d0 = [index[record['source_row']] for record in roles['D0']]
    pool = [index[record['source_row']] for record in roles['pool']]
    if len(pool) < 512:
        raise ValueError('NOT_RUN_INSUFFICIENT_POOL')
    references = [{'index': i, 'id': original[i]['id'], 'x': original[i]['x'], 'y': original[i]['y']}
                  for i in random.Random(seed + 160000).sample(pool, 256)]
    changed = random.Random(seed + 170000).sample(pool, math.floor(.2 * len(pool)))
    original_elevations = [record['x'][0] for record in roles['D0']]
    extreme = max(original_elevations) + 3 * (statistics.pstdev(original_elevations) or 1.)
    truth = []
    label_count = len(changed) // 2
    for position, i in enumerate(changed):
        record, before = rows[i], original[i]
        kind = 'label' if position < label_count else 'feature'
        if kind == 'label':
            record['y'] = LABELS[(LABELS.index(record['y']) + 1) % len(LABELS)]
        else:
            record['x'][0] = extreme
        if (record['x'], record['y']) == (before['x'], before['y']):
            raise ValueError('CONTROLLED_MODIFICATION_DID_NOT_CHANGE_RECORD')
        truth.append({'index': i, 'kind': kind, 'original_x': before['x'], 'original_y': before['y']})
    selection = {'rows': rows, 'roles': {'D0': d0, 'pool': pool},
                 'task': {'type': 'classification', 'feature_count': DIMENSION,
                          'allowed_labels': list(LABELS)},
                 'asset_metadata': {'dataset': DATASET, 'source_sha256': source_hash}}
    return selection, references, truth, extreme


def write_members(path, metadata, roles, keys, total_rows, resources):
    """Stream the five member lists, including unassigned, without vectors."""
    assigned = {row for source_rows in roles.values() for row in source_rows}
    unassigned = (row for row in range(1, total_rows + 1) if row not in assigned)
    with path.open('x', encoding='utf8', newline='\n') as stream:
        stream.write(json.dumps(metadata, separators=(',', ':'))[:-1] + ',"roles":{')
        for role_index, (role, source_rows) in enumerate((*roles.items(), ('unassigned', unassigned))):
            if role_index:
                stream.write(',')
            stream.write(f'\n"{role}":[')
            for i, source_row in enumerate(source_rows):
                if i:
                    stream.write(',')
                member = {'source_row': source_row, 'id': row_id(source_row),
                          'group': keys.get(source_row, str(source_row))}
                stream.write(json.dumps(member, separators=(',', ':')))
                if i % 8192 == 0:
                    resources.check()
            stream.write(']')
        stream.write('\n}}\n')
        stream.flush()
        os.fsync(stream.fileno())


def prepare(raw_path, source_path, seed, output, *, expected_rows=OFFICIAL_ROWS, minimums=MINIMUMS):
    if seed not in SEEDS:
        raise ValueError('UNKNOWN_SEED')
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    if output.exists():
        raise FileExistsError(output)
    raw_path = Path(raw_path)
    source = read(source_path)
    source_hash = validate_source(source, raw_path)
    resources = PreparationResources()
    first_rows, duplicates, labels = scan_groups(raw_path, source_hash, resources, expected_rows)
    keys = group_keys(duplicates)
    allocations = allocate_groups(first_rows, duplicates, labels, keys, source_hash, seed, minimums)
    resources.check()
    source_rows = {role: sorted(row for first in groups for row in (first, *duplicates.get(first, ())))
                   for role, groups in allocations.items()}
    roles = read_assigned(raw_path, source_hash, source_rows, keys, resources)
    selection, references, truth, extreme = make_selection(roles, seed, source_hash)
    counts = {role: len(records) for role, records in roles.items()}
    counts['unassigned'] = expected_rows - sum(counts.values())
    group_counts = {role: len(groups) for role, groups in allocations.items()}
    group_counts['unassigned'] = len(first_rows) - sum(group_counts.values())
    members = {'source_sha256': source_hash, 'seed': seed, 'unassigned_count': counts['unassigned'],
               'group_definition': 'exact 54-feature vector; key is sorted one-based source rows joined by comma',
               'split_order': 'sha256(raw_sha256:seed:group_key), hex lexical order',
               'reference_source_rows': [selection['rows'][record['index']]['source_row'] for record in references]}
    payloads = {'selection_input': selection,
                'audit_reference': {'records': references, 'scope': EVIDENCE_SCOPE},
                'development_input': {'records': roles['dev']}, 'test_input': {'records': roles['test']},
                'injection_truth': {'records': truth, 'scope': 'CONTROLLED_MODIFICATIONS_NOT_NATURAL_ERRORS'},
                'source_record': source}
    preparation = {'scope': 'PUBLIC_DATA_CONTROLLED_VERSION_MODIFICATIONS_NOT_NATURAL_TRUTH',
                   'seed': seed, 'source_sha256': source_hash, 'role_counts': counts,
                   'role_minimums': dict(minimums), 'raw_rows': expected_rows, 'raw_exact_groups': len(first_rows),
                   'whole_group_excess': {role: counts[role] - minimums[role] for role in roles},
                   'unique_groups': group_counts, 'reference_count': len(references),
                   'controlled_modifications': len(truth), 'label_modifications': len(truth) // 2,
                   'feature_modifications': len(truth) - len(truth) // 2, 'feature_mutation_index': 0,
                   'feature_mutation_name': 'Elevation', 'feature_mutation_value': extreme,
                   'd0_class_labels': list(LABELS), 'unseen_entity_relationships': 'NOT_CHECKED',
                   'training': 'NOT_RUN'}
    output.mkdir(parents=True, exist_ok=False)
    for key, payload in payloads.items():
        write(output / FILES[key], payload)
        resources.check()
    write_members(output / FILES['members'], members, source_rows, keys, expected_rows, resources)
    receipt = resources.receipt()
    write(output / FILES['preparation'], preparation)
    config = {'schema': 'external-experiment-v1', 'task_id': 'EXT-01', 'dataset': DATASET,
              'seed': seed, 'task_type': 'classification', 'feature_count': DIMENSION,
              'mutation_feature_index': 0, 'budgets': [128, 512], 'models': ['logistic', 'random_forest'],
              **{key: FILES[key] for key in ('selection_input', 'audit_reference', 'development_input',
                                             'test_input', 'injection_truth')},
              'inputs': [{'path': name, 'sha256': sha(output / name)} for name in FILES.values()]}
    resources.check()
    write(output / 'config.json', config)
    return {'status': 'PREPARED', 'task_id': 'EXT-01', 'seed': seed, 'source_sha256': source_hash,
            'role_counts': counts, 'raw_exact_groups': len(first_rows), 'training': 'NOT_RUN',
            **receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', required=True, type=Path)
    parser.add_argument('--source-record', required=True, type=Path)
    parser.add_argument('--seed', required=True, type=int, choices=sorted(SEEDS))
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.raw, args.source_record, args.seed, args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
