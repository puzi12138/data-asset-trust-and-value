"""Prepare hash-bound UCI Bike Sharing hour.csv inputs for EXT-04."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from scripts.external_experiments.common import INPUT_KEYS, SEEDS, read, sha, write
from scripts.external_experiments.regression_contract import BIKE_FEATURES


DATASET = 'Bike Sharing'
SOURCE_URL = 'https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset'
DOWNLOAD_URL = 'https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip'
RECORD_COUNT = 17379
FEATURE_INDEX = 8
REFERENCE_COUNT = 256
ROLES = ('D0', 'pool', 'dev', 'test', 'buffer')
COLUMN_RANGES = ((1, 4), (0, 1), (1, 12), (0, 23), (0, 1), (0, 6), (0, 1), (1, 4))
RAW_COLUMNS = {'instant', 'dteday', 'casual', 'registered', 'cnt', *BIKE_FEATURES}
VERSION_SCOPE = 'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'
GROUP_DEFINITION = 'dteday, assigned as a whole to a chronological role'
IDENTITY_DEFINITION = 'ISO date and zero-padded hour, joined by a colon'


def validate_source(source, raw):
    if not isinstance(source, dict) or source.get('dataset') != DATASET:
        raise ValueError('SOURCE_DATASET_MISMATCH')
    if source.get('source_url') != SOURCE_URL or source.get('download_url') != DOWNLOAD_URL:
        raise ValueError('SOURCE_URL_MISMATCH')
    if source.get('license') != 'CC BY 4.0':
        raise ValueError('SOURCE_LICENSE_MISMATCH')
    attribution = source.get('attribution')
    if not isinstance(attribution, str) or not attribution.strip():
        raise ValueError('SOURCE_ATTRIBUTION_REQUIRED')
    try:
        retrieved = datetime.fromisoformat(source['retrieved_at_utc'].replace('Z', '+00:00'))
    except (AttributeError, KeyError, ValueError):
        raise ValueError('SOURCE_RETRIEVAL_TIME_INVALID') from None
    if retrieved.utcoffset() != timedelta(0):
        raise ValueError('SOURCE_RETRIEVAL_TIME_INVALID')
    size = source.get('raw_size_bytes')
    if type(size) is not int or size != len(raw):
        raise ValueError('SOURCE_SIZE_MISMATCH')
    if source.get('raw_sha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('SOURCE_HASH_MISMATCH')


def parse_bike(raw, expected_rows=RECORD_COUNT):
    """Read published hourly fields, preserving the one-based source index."""
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('RAW_ENCODING_INVALID') from exc
    reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
    headers = reader.fieldnames or []
    if len(headers) != len(RAW_COLUMNS) or set(headers) != RAW_COLUMNS:
        raise ValueError('RAW_COLUMNS_MISMATCH')
    rows = []
    identities = set()
    for n, record in enumerate(reader, start=1):
        if None in record or any(value is None for value in record.values()):
            raise ValueError(f'INVALID_COLUMN_COUNT_AT_SOURCE_ROW_{n}')
        try:
            if int(record['instant']) != n:
                raise ValueError('expected instant to match the source row')
            day = date.fromisoformat(record['dteday'])
            if day.isoformat() != record['dteday']:
                raise ValueError('expected a canonical ISO date')
            categories = [int(record[name]) for name in BIKE_FEATURES[:8]]
            if any(not lo <= value <= hi for value, (lo, hi) in zip(categories, COLUMN_RANGES)):
                raise ValueError('categorical value outside its range')
            observed = [float(record[name]) if record[name].strip() else None for name in BIKE_FEATURES[8:]]
            if any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for v in observed):
                raise ValueError('normalized weather value outside its range')
            target = int(record['cnt'])
            if target < 0:
                raise ValueError('rental count is negative')
        except ValueError as exc:
            raise ValueError(f'INVALID_RECORD_AT_SOURCE_ROW_{n}: {exc}') from exc
        group = day.isoformat()
        identity = f"{group}:{categories[3]:02d}"
        if identity in identities:
            raise ValueError(f'DUPLICATE_HOUR_AT_SOURCE_ROW_{n}')
        identities.add(identity)
        rows.append({'id': identity, 'source_row': n, 'group': group, 'split_group': group,
                     'record_identity': identity, 'x': categories + observed, 'y': target})
    if len(rows) != expected_rows:
        raise ValueError('RAW_ROW_COUNT_MISMATCH')
    return rows


def split_dates(dates):
    """Allocate sorted observed days, keeping each two-day buffer explicit."""
    days = sorted(set(dates))
    usable = len(days) - 6
    if usable <= 0:
        raise ValueError('INSUFFICIENT_DAYS')
    counts = (usable * 20 // 100, usable * 40 // 100, usable * 15 // 100)
    if min(counts) == 0:
        raise ValueError('EMPTY_DATE_ROLE')
    assigned = {}
    cursor = 0
    for role, count in zip(('D0', 'pool', 'dev'), counts):
        assigned[role] = days[cursor:cursor + count]
        cursor += count
        assigned[f'buffer_after_{role}'] = days[cursor:cursor + 2]
        cursor += 2
    assigned['test'] = days[cursor:]
    if not assigned['test']:
        raise ValueError('EMPTY_DATE_ROLE')
    return assigned


def build_payloads(records, source, seed):
    if seed not in SEEDS:
        raise ValueError('UNKNOWN_SEED')
    days = split_dates(r['group'] for r in records)
    role_by_day = {day: role if role in ROLES else 'buffer'
                   for role, dates in days.items() for day in dates}
    splits = {role: [] for role in ROLES}
    for r in sorted(records, key=lambda row: row['source_row']):
        splits[role_by_day[r['group']]].append(r)
    if len(splits['pool']) < 512:
        raise ValueError('NOT_RUN_INSUFFICIENT_POOL')
    original = sorted(splits['D0'] + splits['pool'], key=lambda r: r['source_row'])
    positions = {r['source_row']: i for i, r in enumerate(original)}
    d0 = [positions[r['source_row']] for r in splits['D0']]
    pool = [positions[r['source_row']] for r in splits['pool']]
    rows = [{**row, 'x': row['x'].copy()} for row in original]

    reference_indexes = random.Random(seed + 160000).sample(pool, REFERENCE_COUNT)
    references = [{'index': i, 'id': original[i]['id'],
                   'x': original[i]['x'].copy(), 'y': original[i]['y']} for i in reference_indexes]
    modified = random.Random(seed + 170000).sample(pool, len(pool) // 5)
    label_count = len(modified) // 2
    delta = 2 * (statistics.pstdev(r['y'] for r in splits['D0']) or 1)
    temperatures = [r['x'][FEATURE_INDEX] for r in splits['D0'] if r['x'][FEATURE_INDEX] is not None]
    if not temperatures:
        raise ValueError('NO_D0_TEMPERATURES')
    extreme = max(temperatures) + 3 * (statistics.pstdev(temperatures) or 1)
    truth = []
    for j, i in enumerate(modified):
        row = rows[i]
        if j < label_count:
            kind = 'label'
            previous = row['y']
            row['y'] += delta
        else:
            kind = 'feature'
            previous = row['x'][FEATURE_INDEX]
            row['x'][FEATURE_INDEX] = extreme
        if (row['x'], row['y']) == (original[i]['x'], original[i]['y']):
            raise ValueError('CONTROLLED_MODIFICATION_DID_NOT_CHANGE_RECORD')
        truth.append({'index': i, 'kind': kind, 'original': previous})

    raw_hash = source['raw_sha256']
    role_counts = {role: len(rows_in_role) for role, rows_in_role in splits.items()}
    boundaries = {role: {'first': values[0], 'last': values[-1], 'day_count': len(values)}
                  for role, values in days.items()}
    members = {'source_sha256': raw_hash, 'seed': seed, 'date_boundaries': boundaries,
               'roles': {role: [{k: r[k] for k in ('source_row', 'id', 'group')} for r in split]
                         for role, split in splits.items()}}
    preparation = {'seed': seed, 'training': 'NOT_RUN', 'source_sha256': raw_hash,
                   'role_counts': role_counts, 'date_boundaries': boundaries,
                   'group_definition': GROUP_DEFINITION, 'record_identity_definition': IDENTITY_DEFINITION,
                   'reference_count': REFERENCE_COUNT, 'label_modifications': label_count,
                   'feature_modifications': len(modified) - label_count,
                   'label_mutation_delta': delta, 'feature_mutation_index': FEATURE_INDEX,
                   'feature_mutation_value': extreme}
    return {
        'source_record.json': source,
        'MEMBERS.json': members,
        'selection_input.json': {
            'asset_metadata': {'dataset': DATASET, 'source_sha256': raw_hash},
            'task': {'type': 'regression', 'feature_count': len(BIKE_FEATURES), 'feature_names': BIKE_FEATURES.copy()},
            'rows': rows, 'roles': {'D0': d0, 'pool': pool},
        },
        'audit_reference.json': {'scope': VERSION_SCOPE, 'records': references},
        'development_input.json': {'records': splits['dev']},
        'test_input.json': {'records': splits['test']},
        'injection_truth.json': {'scope': 'CONTROLLED_MODIFICATIONS_NOT_NATURAL_ERRORS', 'records': truth},
        'preparation.json': preparation,
    }


def prepare(raw_path, source_path, seed, out):
    directory = Path(out).resolve()
    if directory.is_relative_to(ROOT):
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    if directory.exists():
        raise FileExistsError('OUTPUT_ALREADY_EXISTS')
    raw = Path(raw_path).read_bytes()
    source = read(source_path)
    validate_source(source, raw)
    payloads = build_payloads(parse_bike(raw), source, seed)
    preparation = payloads.pop('preparation.json')
    directory.mkdir(parents=True, exist_ok=False)
    for name, value in payloads.items():
        write(directory / name, value)
    inputs = [{'path': name, 'sha256': sha(directory / name)} for name in payloads]
    preparation['inputs'] = inputs.copy()
    write(directory / 'preparation.json', preparation)
    inputs.append({'path': 'preparation.json', 'sha256': sha(directory / 'preparation.json')})
    config = {'schema': 'external-experiment-v1', 'task_id': 'EXT-04', 'dataset': DATASET,
              'seed': seed, 'task_type': 'regression', 'feature_count': len(BIKE_FEATURES),
              'mutation_feature_index': FEATURE_INDEX, 'models': ['ridge', 'random_forest'],
              'budgets': [128, 512], 'training': 'NOT_RUN', 'inputs': inputs,
              **{key: f'{key}.json' for key in INPUT_KEYS}}
    write(directory / 'config.json', config)
    return {'status': 'PREPARED', 'training': 'NOT_RUN', 'task_id': 'EXT-04',
            'seed': seed, 'role_counts': preparation['role_counts'],
            'source_sha256': source['raw_sha256'], 'config_sha256': sha(directory / 'config.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', required=True, type=Path)
    parser.add_argument('--source-record', required=True, type=Path)
    parser.add_argument('--seed', required=True, type=int, choices=sorted(SEEDS))
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.raw, args.source_record, args.seed, args.out)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f'Bike input preparation stopped: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
