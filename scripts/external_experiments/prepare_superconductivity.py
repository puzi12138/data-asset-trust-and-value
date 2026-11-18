"""Prepare hash-bound UCI Superconductivity inputs for EXT-03 without model fitting."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
from itertools import zip_longest
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


DATASET = 'Superconductivity'
SOURCE_URL = 'https://archive.ics.uci.edu/dataset/464/superconductivty+data'
DOWNLOAD_URL = 'https://archive.ics.uci.edu/static/public/464/superconductivty+data.zip'
ORIGINAL_SCRIPT = (
    'https://github.com/khamidieh/predict_tc/blob/ab093ab19a4f34ec7c7bd0342afe0f7d26f0f8b6'
    '/main_script_production_9.R#L1083-L1089'
)
ORIGINAL_EXPORT = (
    'https://github.com/khamidieh/predict_tc/blob/ab093ab19a4f34ec7c7bd0342afe0f7d26f0f8b6'
    '/README.md#L66-L75'
)
RECORD_COUNT = 21263
REFERENCE_COUNT = 256
FEATURE_INDEX = 0
MINIMUMS = {'D0': 512, 'pool': 8192, 'dev': 1024, 'test': 2048}
GROUP_DEFINITION = 'connected components of equal formula strings or equal raw feature vectors'
SPLIT_ORDER = 'sha256(train_sha256:seed:comma-separated one-based group rows), hex lexical order'
VERSION_SCOPE = 'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'
PROPERTIES = ('atomic_mass', 'fie', 'atomic_radius', 'Density', 'ElectronAffinity',
              'FusionHeat', 'ThermalConductivity', 'Valence')
STATISTICS = ('mean', 'wtd_mean', 'gmean', 'wtd_gmean', 'entropy', 'wtd_entropy',
              'range', 'wtd_range', 'std', 'wtd_std')
FEATURES = ('number_of_elements',) + tuple(
    f'{stat}_{prop}' for prop in PROPERTIES for stat in STATISTICS
)
ELEMENTS = tuple(('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni '
                  'Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe '
                  'Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au '
                  'Hg Tl Pb Bi Po At Rn').split())


def validate_source(source, train, materials):
    if not isinstance(source, dict) or source.get('dataset') != DATASET:
        raise ValueError('SOURCE_DATASET_MISMATCH')
    if source.get('source_url') != SOURCE_URL or source.get('download_url') != DOWNLOAD_URL:
        raise ValueError('SOURCE_URL_MISMATCH')
    if source.get('license') != 'CC BY 4.0':
        raise ValueError('SOURCE_LICENSE_MISMATCH')
    if source.get('alignment_status') != 'SOURCE_SUPPORTED':
        raise ValueError('ALIGNMENT_UNRESOLVED')
    attribution = source.get('attribution')
    if not isinstance(attribution, str) or not attribution.strip():
        raise ValueError('SOURCE_ATTRIBUTION_REQUIRED')
    try:
        retrieved = datetime.fromisoformat(source['retrieved_at_utc'].replace('Z', '+00:00'))
    except (AttributeError, KeyError, ValueError):
        raise ValueError('SOURCE_RETRIEVAL_TIME_INVALID') from None
    if retrieved.utcoffset() != timedelta(0):
        raise ValueError('SOURCE_RETRIEVAL_TIME_INVALID')
    for key, blob in (('raw', train), ('materials', materials)):
        size = source.get(f'{key}_size_bytes')
        if type(size) is not int or not blob or size != len(blob):
            raise ValueError(f'SOURCE_SIZE_MISMATCH:{key}')
        if source.get(f'{key}_sha256') != sha256(blob).hexdigest():
            raise ValueError(f'SOURCE_HASH_MISMATCH:{key}')


def parse_superconductivity(train, materials, expected_rows=RECORD_COUNT):
    """Read the two published CSVs in lockstep and preserve exact decimal feature keys."""
    a = csv.reader(io.TextIOWrapper(io.BytesIO(train), encoding='utf-8-sig', newline=''), strict=True)
    b = csv.reader(io.TextIOWrapper(io.BytesIO(materials), encoding='utf-8-sig', newline=''), strict=True)
    if tuple(next(a, ())) != FEATURES + ('critical_temp',):
        raise ValueError('TRAIN_COLUMNS_MISMATCH')
    if tuple(next(b, ())) != ELEMENTS + ('critical_temp', 'material'):
        raise ValueError('MATERIALS_COLUMNS_MISMATCH')
    result = []
    for number, (features, material) in enumerate(zip_longest(a, b), start=1):
        if features is None or material is None:
            raise ValueError(f'UNPAIRED_SOURCE_ROW:{number}')
        if len(features) != len(FEATURES) + 1 or len(material) != len(ELEMENTS) + 2:
            raise ValueError(f'COLUMN_COUNT_MISMATCH:{number}')
        try:
            values = tuple(map(Decimal, features[:-1]))
            label = Decimal(features[-1])
            material_label = Decimal(material[-2])
            amounts = tuple(map(float, material[:-2]))
            formula = material[-1].strip()
            if not formula:
                raise ValueError('missing chemical formula')
            if any(not math.isfinite(value) or value < 0 for value in amounts):
                raise ValueError('invalid elemental composition')
            if not all(value.is_finite() and math.isfinite(float(value)) for value in (*values, label)):
                raise ValueError('nonfinite feature or target')
            if not material_label.is_finite() or label != material_label:
                raise ValueError('critical temperatures differ between paired rows')
            element_count = sum(value > 0 for value in amounts)
            if not element_count or values[0] != element_count:
                raise ValueError('element counts differ between paired rows')
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise ValueError(f'INVALID_SOURCE_ROW:{number}: {exc}') from exc
        result.append({'source_row': number, 'id': f'superconductivity:{number:05d}',
                       'formula': formula, 'x': values, 'y': float(label)})
    if len(result) != expected_rows:
        raise ValueError(f'RAW_ROW_COUNT_MISMATCH: expected {expected_rows}, got {len(result)}')
    return result


def split_records(records, train_hash, seed, minimums):
    parents = list(range(len(records)))
    size = [1] * len(records)

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(a, b):
        a, b = root(a), root(b)
        if a != b:
            if size[a] < size[b]:
                a, b = b, a
            parents[b] = a
            size[a] += size[b]

    by_formula, by_features = {}, {}
    ordered = sorted(records, key=lambda row: row['source_row'])
    for i, row in enumerate(ordered):
        union(i, by_formula.setdefault(row['formula'], i))
        union(i, by_features.setdefault(row['x'], i))
    components = {}
    for i, row in enumerate(ordered):
        components.setdefault(root(i), []).append(row)
    groups = {
        ','.join(str(row['source_row']) for row in rows): rows for rows in components.values()
    }
    keys = iter(sorted(groups, key=lambda key: (sha256(f'{train_hash}:{seed}:{key}'.encode()).hexdigest(), key)))
    result = {}
    for role in (*MINIMUMS, 'unassigned'):
        members = []
        minimum = minimums.get(role, 0)
        while role == 'unassigned' or len(members) < minimum:
            key = next(keys, None)
            if key is None:
                break
            for row in groups[key]:
                members.append({'source_row': row['source_row'], 'id': row['id'], 'group': key,
                                'x': list(map(float, row['x'])), 'y': row['y']})
        if len(members) < minimum:
            raise ValueError(f'NOT_RUN_INSUFFICIENT_GROUPED_ROWS:{role}')
        result[role] = sorted(members, key=lambda row: row['source_row'])
    return result


def build_payloads(records, source, seed, minimums=MINIMUMS, reference_count=REFERENCE_COUNT):
    if seed not in SEEDS:
        raise ValueError('UNKNOWN_SEED')
    train_hash = source['raw_sha256']
    splits = split_records(records, train_hash, seed, minimums)
    if len(splits['pool']) < reference_count:
        raise ValueError('NOT_RUN_INSUFFICIENT_REFERENCE_POOL')
    original = sorted(splits['D0'] + splits['pool'], key=lambda row: row['source_row'])
    indexes = {row['source_row']: index for index, row in enumerate(original)}
    d0 = [indexes[row['source_row']] for row in splits['D0']]
    pool = [indexes[row['source_row']] for row in splits['pool']]
    rows = [{**row, 'x': row['x'].copy()} for row in original]

    references = []
    for index in random.Random(seed + 160000).sample(pool, reference_count):
        row = original[index]
        references.append({'index': index, 'id': row['id'], 'x': row['x'], 'y': row['y']})
    modified = random.Random(seed + 170000).sample(pool, len(pool) // 5)
    label_count = len(modified) // 2
    delta = 2 * (statistics.pstdev(row['y'] for row in splits['D0']) or 1)
    feature_values = [row['x'][FEATURE_INDEX] for row in splits['D0']]
    extreme = max(feature_values) + 3 * (statistics.pstdev(feature_values) or 1)
    truth = []
    for order, index in enumerate(modified):
        row = rows[index]
        if order < label_count:
            kind, previous = 'label', row['y']
            row['y'] += delta
            value = row['y']
        else:
            kind, previous = 'feature', row['x'][FEATURE_INDEX]
            row['x'][FEATURE_INDEX] = value = extreme
        if not math.isfinite(value) or value == previous:
            raise ValueError(f'INVALID_CONTROLLED_MODIFICATION:{row["source_row"]}')
        truth.append({'index': index, 'kind': kind, 'original': previous})

    counts = {role: len(members) for role, members in splits.items()}
    source_record = {
        **source, 'raw_filename': 'train.csv', 'materials_filename': 'unique_m.csv',
        'target_units': 'K',
        'alignment_evidence': {
            'repository': SOURCE_URL, 'generator': ORIGINAL_SCRIPT, 'export': ORIGINAL_EXPORT,
            'method': 'The author generates train[i,] from unique_m[i,] and exports both in that order.',
            'checks': 'Row counts, critical temperature, and number of elements were checked for each pair.',
        },
    }
    return {
        'source_record.json': source_record,
        'MEMBERS.json': {
            'source_sha256': train_hash, 'seed': seed,
            'roles': {role: [{k: row[k] for k in ('source_row', 'id', 'group')} for row in members]
                      for role, members in splits.items()},
        },
        'selection_input.json': {
            'asset_metadata': {'dataset': DATASET, 'source_sha256': train_hash},
            'task': {'type': 'regression', 'feature_count': len(FEATURES), 'feature_names': list(FEATURES)},
            'rows': rows, 'roles': {'D0': d0, 'pool': pool},
        },
        'audit_reference.json': {'scope': VERSION_SCOPE, 'records': references},
        'development_input.json': {'records': splits['dev']},
        'test_input.json': {'records': splits['test']},
        'injection_truth.json': {'scope': 'CONTROLLED_MODIFICATIONS_NOT_NATURAL_ERRORS', 'records': truth},
        'preparation.json': {
            'training': 'NOT_RUN', 'seed': seed, 'source_sha256': train_hash,
            'materials_sha256': source['materials_sha256'],
            'role_counts': counts, 'role_minimums': dict(minimums),
            'whole_group_excess': {role: counts[role] - minimums[role] for role in MINIMUMS},
            'unique_groups': {role: len({row['group'] for row in members}) for role, members in splits.items()},
            'group_definition': GROUP_DEFINITION, 'split_order': SPLIT_ORDER,
            'reference_count': reference_count, 'controlled_modifications': len(modified),
            'label_modifications': label_count, 'feature_modifications': len(modified) - label_count,
            'label_mutation_delta': delta, 'feature_mutation_index': FEATURE_INDEX,
            'feature_mutation_value': extreme,
        },
    }


def prepare(train_path, materials_path, source_path, seed, out):
    directory = Path(out).resolve()
    if directory.is_relative_to(ROOT):
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    if directory.exists():
        raise FileExistsError('OUTPUT_ALREADY_EXISTS')
    train = Path(train_path).read_bytes()
    materials = Path(materials_path).read_bytes()
    source = read(source_path)
    validate_source(source, train, materials)
    payloads = build_payloads(parse_superconductivity(train, materials), source, seed)
    preparation = payloads.pop('preparation.json')
    directory.mkdir(parents=True, exist_ok=False)
    for name, value in payloads.items():
        write(directory / name, value)
    inputs = [{'path': name, 'sha256': sha(directory / name)} for name in payloads]
    preparation['inputs'] = inputs.copy()
    write(directory / 'preparation.json', preparation)
    inputs.append({'path': 'preparation.json', 'sha256': sha(directory / 'preparation.json')})
    config = {
        'schema': 'external-experiment-v1', 'task_id': 'EXT-03', 'dataset': DATASET,
        'seed': seed, 'task_type': 'regression', 'feature_count': len(FEATURES),
        'mutation_feature_index': FEATURE_INDEX, 'models': ['ridge', 'random_forest'],
        'budgets': [128, 512], 'training': 'NOT_RUN', 'inputs': inputs,
        **{key: f'{key}.json' for key in INPUT_KEYS},
    }
    write(directory / 'config.json', config)
    return {'status': 'PREPARED', 'training': 'NOT_RUN', 'task_id': 'EXT-03', 'seed': seed,
            'role_counts': preparation['role_counts'], 'source_sha256': source['raw_sha256'],
            'config_sha256': sha(directory / 'config.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', required=True, type=Path)
    parser.add_argument('--materials', required=True, type=Path)
    parser.add_argument('--source-record', required=True, type=Path)
    parser.add_argument('--seed', required=True, type=int, choices=sorted(SEEDS))
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.train, args.materials, args.source_record, args.seed, args.out)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f'Superconductivity input preparation stopped: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
