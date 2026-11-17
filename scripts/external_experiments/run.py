"""Windows shared runner: frozen code/input, existing Job supervision, no downloads."""
from pathlib import Path
import argparse
import datetime
import importlib.metadata
import json
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.external_experiments.common import (
    read, write, sha, validate_config, code_manifest, planned_arms,
)
from scripts.experiments import resources, check_env
from scripts.experiments.run_task import Budget, git_state

REQUIRED = {'numpy': '1.24.3', 'scipy': '1.13.0', 'scikit-learn': '1.4.2',
            'threadpoolctl': '3.4.0', 'psutil': '5.9.1'}


def dependency_check():
    observed = {}
    for package, expected in REQUIRED.items():
        try:
            observed[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            observed[package] = None
        if observed[package] != expected:
            raise ValueError('PINNED_DEPENDENCY_MISMATCH:' + package)
    return observed


def check_freeze(freeze_path, config_path, data_dir):
    f = read(freeze_path)
    if f.get('schema') != 'external-code-freeze-v1' or f.get('config_sha256') != sha(config_path):
        raise ValueError('CONFIG_FREEZE_MISMATCH')
    if f.get('code') != code_manifest():
        raise ValueError('CODE_FREEZE_MISMATCH')
    validate_config(read(config_path), data_dir)
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--freeze', type=Path, required=True)
    ap.add_argument('--output', type=Path)
    ap.add_argument('--freeze-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--timeout', type=int, default=300)
    a = ap.parse_args()
    if not 1 <= a.timeout <= 300:
        ap.error('timeout must be 1..300 seconds; resource floors cannot be relaxed')
    cfg = read(a.config)
    validate_config(cfg, a.data_dir)
    if a.freeze_only:
        write(a.freeze, {'schema': 'external-code-freeze-v1',
                        'frozen_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'config_sha256': sha(a.config), 'code': code_manifest(),
                        'inputs': cfg['inputs'], 'git': git_state(),
                        'scope': 'Code/input commitment; not identity or natural truth authentication.'})
        print(json.dumps({'status': 'FROZEN', 'config_sha256': sha(a.config), 'freeze_sha256': sha(a.freeze)}))
        return 0
    binding = check_freeze(a.freeze, a.config, a.data_dir)
    env = check_env.snapshot()
    admission = []
    if sys.platform != 'win32':
        admission.append('WINDOWS_ONLY')
    if env['system_memory_available_bytes'] < int(1.5 * resources.GIB):
        admission.append('START_MEMORY_BELOW_1_5_GIB')
    try:
        deps = dependency_check()
    except ValueError as exc:
        admission.append(str(exc)); deps = env['packages']
    if a.dry_run:
        print(json.dumps({'status': 'READY' if not admission else 'NOT_READY',
                          'reasons': admission, 'environment': env, 'dependencies': deps,
                          'planned_target_fits': len(planned_arms(cfg)), 'training_started': False}, indent=2))
        return 2 if admission else 0
    if a.output is None:
        ap.error('--output is required for supervised execution')
    parent = a.output.resolve()
    if parent.is_relative_to(ROOT):
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
    out = parent / run_id; out.mkdir()
    write(out / 'ENVIRONMENT.json', env)
    write(out / 'CODE_FREEZE.json', binding)
    write(out / 'CONFIG.json', cfg)
    # A single fixed parent holds this task's 1800s ledger across models/seeds.
    budget = Budget(parent, False, a.timeout, run_id)
    result = {'status': 'NOT_RUN_PREFLIGHT', 'training_go_sent': False,
              'worker_started': False, 'wall_seconds': 0., 'stop_reasons': admission}
    started = time.monotonic(); reserved = False
    try:
        if not admission:
            budget.reserve(); reserved = True
            # Existing resource implementation remains byte-for-byte unchanged.
            # One process invocation owns this binding; no concurrent worker calls.
            resources.WORKER = ROOT / ('scripts/external_experiments/regression_worker.py' if cfg['task_type']=='regression' else 'scripts/external_experiments/worker.py')
            result = resources.execute(a.config.resolve(), a.data_dir.resolve(), out / 'worker_output',
                                       a.timeout, False, False,
                                       lambda: check_freeze(a.freeze, a.config, a.data_dir), 1.5, .75)
            check_freeze(a.freeze, a.config, a.data_dir)
    except Exception as exc:
        result['status'] = 'FAILED_EXECUTION_OR_BINDING'
        result.setdefault('stop_reasons', []).append(resources._sandbox_paths(str(exc), [ROOT, Path.home(), a.data_dir, parent]))
    finally:
        if reserved:
            budget.finish(time.monotonic() - started)
        budget.close()
    (out / 'RUN.log').write_text(result.pop('log', ''), encoding='utf8')
    write(out / 'COST.json', {'monitor': result, 'cumulative_limit_seconds': 1800,
                             'budget_scope': 'one declared output parent; not enforceable across machines/directories',
                             'device': 'cpu', 'token_budget': 'NOT_APPLICABLE_TABULAR',
                             'gpu_used': False})
    worker = out / 'worker_output'
    completed = []
    for arm in planned_arms(cfg):
        p = worker / arm / 'METRICS.json'
        if p.is_file():
            completed.append({'arm': arm, 'status': read(p).get('status')})
    plan = {'expected': planned_arms(cfg), 'recorded': completed,
            'not_recorded': [x for x in planned_arms(cfg) if x not in {r['arm'] for r in completed}]}
    write(out / 'PLAN_STATUS.json', plan)
    verification = {'status': 'NOT_RUN_INCOMPLETE_WORKER'}
    if result['status'] == 'COMPLETE' and (worker / 'SUMMARY.json').is_file():
        verifier = 'regression_verify.py' if cfg['task_type']=='regression' else 'verify.py'
        p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/external_experiments' / verifier),
                            '--result', str(worker), '--config', str(a.config), '--data-dir', str(a.data_dir),
                            '--output', str(out / 'VERIFY_REPORT.json')], cwd=ROOT,
                           capture_output=True, text=True, encoding='utf8', timeout=180)
        (out / 'VERIFY.log').write_text(p.stdout + p.stderr, encoding='utf8')
        verification = read(out / 'VERIFY_REPORT.json') if (out / 'VERIFY_REPORT.json').is_file() else {
            'status': 'FAILED_NO_VERIFY_REPORT', 'exit_code': p.returncode}
    if not (out / 'VERIFY_REPORT.json').exists():
        write(out / 'VERIFY_REPORT.json', verification)
    write(out / 'RUN_MANIFEST.json', {'schema': 'external-run-v1', 'task_id': cfg['task_id'],
        'seed': cfg['seed'], 'run_id': run_id, 'status': result['status'],
        'verify_status': verification['status'], 'config_sha256': sha(a.config),
        'freeze_sha256': sha(a.freeze), 'code': binding['code'], 'inputs': cfg['inputs'],
        'scope': cfg.get('scope', 'PUBLIC_DATA_CONTROLLED_VERSION_MODIFICATION_NOT_BLIND'),
        'files': [{'path': p.relative_to(out).as_posix(), 'sha256': sha(p), 'size_bytes': p.stat().st_size}
                  for p in sorted(out.rglob('*')) if p.is_file()]})
    print(json.dumps({'run_id': run_id, 'status': result['status'], 'verify_status': verification['status'],
                      'planned_target_fits': 18, 'recorded_arms': len(completed),
                      'wall_seconds': result.get('wall_seconds', 0.)}))
    return 0 if result['status'] == 'COMPLETE' and verification['status'] == 'REPRODUCED' else 2


if __name__ == '__main__':
    sys.exit(main())
