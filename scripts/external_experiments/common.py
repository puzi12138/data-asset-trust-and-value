"""Shared immutable input and source bindings. No training or data downloads."""
from pathlib import Path, PureWindowsPath
import hashlib
import json
import os
import re

ROOT = Path(__file__).resolve().parents[2]
METHODS = ['random_group', 'completeness', 'rf_source_conformity_group', 'evidence_rf_group']
SEEDS = {1103, 2207, 3301}
INPUT_KEYS = ('selection_input', 'audit_reference', 'development_input', 'test_input', 'injection_truth')
LEGACY_CODE = [
    'scripts/experiments/resources.py', 'scripts/experiments/run_task.py',
    'scripts/experiments/check_env.py', 'scripts/experiments/verify_result.py',
    'scripts/training/supervisor.py',
    'src/data_asset_poc/__init__.py', 'src/data_asset_poc/assessment/__init__.py',
    'src/data_asset_poc/assessment/selection.py', 'src/data_asset_poc/assessment/value_methods.py',
    'src/data_asset_poc/assessment/binding.py',
]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, obj):
    with Path(path).open('x', encoding='utf8', newline='\n') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def safe(root, relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or ':' in relative or Path(relative).is_absolute()
            or PureWindowsPath(relative).drive or '..' in relative.split('/')):
        raise ValueError('INVALID_RELATIVE_PATH')
    p = (Path(root) / relative).resolve()
    p.relative_to(Path(root).resolve())
    return p


def validate_config(config, data_dir):
    if config.get('schema') != 'external-experiment-v1':
        raise ValueError('UNSUPPORTED_CONFIG_SCHEMA')
    if config.get('task_id') not in {'EXT-02', 'EXTERNAL-SMOKE'}:
        raise ValueError('ADAPTER_NOT_IMPLEMENTED')
    if config.get('task_id') == 'EXTERNAL-SMOKE' and config.get('scope') != 'SYNTHETIC_SMOKE_NON_SCIENTIFIC':
        raise ValueError('SMOKE_SCOPE_REQUIRED')
    if config.get('seed') not in SEEDS or isinstance(config.get('seed'), bool):
        raise ValueError('SEED_NOT_FROZEN')
    if config.get('budgets') != [128, 512] or config.get('models') != ['logistic', 'random_forest']:
        raise ValueError('MATRIX_NOT_FROZEN')
    if config.get('task_type') != 'classification' or config.get('mutation_feature_index') != 0:
        raise ValueError('UNSUPPORTED_TASK_OR_FEATURE')
    if config['task_id'] == 'EXT-02' and (config.get('feature_count') != 16 or config.get('dataset') != 'Letter Recognition'):
        raise ValueError('LETTER_SCHEMA_MISMATCH')
    declared = set()
    if not isinstance(config.get('inputs'), list) or not config['inputs']:
        raise ValueError('MISSING_INPUT_BINDING')
    for item in config['inputs']:
        name = item['path']
        if name in declared or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
            raise ValueError('INVALID_OR_DUPLICATE_INPUT_HASH')
        declared.add(name)
        if sha(safe(data_dir, name)) != item['sha256']:
            raise ValueError('INPUT_HASH_MISMATCH:' + name)
    for key in INPUT_KEYS:
        if config.get(key) not in declared:
            raise ValueError('REQUIRED_INPUT_UNBOUND:' + key)
    return True


def code_manifest():
    paths = sorted(set(LEGACY_CODE + [p.relative_to(ROOT).as_posix()
        for p in (ROOT / 'scripts/external_experiments').glob('*.py')]))
    return [{'path': p, 'sha256': sha(ROOT / p)} for p in paths]


def planned_arms(config):
    return [f'{model}__{suffix}' for model in config['models']
            for suffix in ['D0'] + [f'k{k}__{method}' for k in config['budgets'] for method in METHODS]]
