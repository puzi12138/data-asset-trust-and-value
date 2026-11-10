"""Two-way pipe handshake between the fixed dataset supervisor and its worker.

This prevents accidental direct launch and verifies the live parent process and
frozen bytes. It is not a privilege boundary against another process running
with the same Windows user rights.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys
import psutil

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = (ROOT / 'scripts/training/supervisor.py').resolve()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def norm(p):
    return os.path.normcase(os.path.realpath(str(p)))


def check_dependency_origins(expected):
    """Fail closed if the worker resolves a dependency differently from its parent."""
    if not isinstance(expected, dict) or not expected or 'psutil' not in expected:
        raise ValueError('dependency_origin_manifest_required')
    actual = {}
    for name, origin in expected.items():
        if not isinstance(name, str) or not name.isidentifier() or not isinstance(origin, str):
            raise ValueError('invalid_dependency_origin_manifest')
        spec = importlib.util.find_spec(name)
        found = spec.origin if spec is not None else None
        if not found or norm(found) != norm(origin):
            raise ValueError('dependency_origin_mismatch:' + name)
        actual[name] = str(Path(found).resolve())
    return actual


def authorize(config_path, out_dir):
    """Check source/parent/challenge and wait for explicit GO before training.

    Call immediately after parsing --config/--out, before importing ML
    libraries, creating output directories, or performing any training work.
    Returns a small receipt dictionary; raises RuntimeError on failure.
    """
    config = Path(config_path).resolve()
    out = Path(out_dir).resolve()
    worker = Path(sys.argv[0]).resolve()
    try:
        raw = sys.stdin.readline(65537)
        if not raw or len(raw) > 65536:
            raise ValueError('missing_pipe_challenge')
        c = json.loads(raw)
        if c.get('kind') != 'TRAINING_CHALLENGE_V1' or not isinstance(c.get('nonce'), str) or len(c['nonce']) != 64:
            raise ValueError('invalid_challenge')
        parent = psutil.Process(os.getppid())
        if parent.pid != c['supervisor_pid']:
            raise ValueError('parent_pid_mismatch')
        if abs(parent.create_time() - c['supervisor_create_time']) > .001:
            raise ValueError('parent_creation_time_mismatch')
        if norm(parent.exe()) != norm(c['supervisor_exe']) or norm(parent.exe()) != norm(psutil.Process().exe()):
            raise ValueError('parent_executable_mismatch')
        cmd = parent.cmdline()
        positions = [i for i, arg in enumerate(cmd) if norm(arg) == norm(SUPERVISOR)]
        if len(positions) != 1 or positions[0] < 1 or any(x not in ('-B', '-u') for x in cmd[1:positions[0]]):
            raise ValueError('parent_command_not_supervisor')
        for flag, expected in [('--config', config), ('--out', out)]:
            indices = [i for i, arg in enumerate(cmd) if arg == flag]
            if len(indices) != 1 or indices[0] + 1 >= len(cmd) or norm(cmd[indices[0] + 1]) != norm(expected):
                raise ValueError('parent_command_argument_mismatch:' + flag)
        if sha(SUPERVISOR) != c['supervisor_sha256']:
            raise ValueError('supervisor_hash_mismatch')
        if norm(worker) != norm(c['worker_path']) or sha(worker) != c['worker_sha256']:
            raise ValueError('worker_hash_or_path_mismatch')
        if norm(config) != norm(c['config_path']) or sha(config) != c['config_sha256']:
            raise ValueError('config_hash_or_path_mismatch')
        if norm(out) != norm(c['out_dir']) or out.exists():
            raise ValueError('output_mismatch_or_exists')
        parsed = json.loads(config.read_text(encoding='utf-8-sig'))
        if parsed.get('worker_sha256') != c['worker_sha256']:
            raise ValueError('frozen_worker_hash_mismatch')
        origins = check_dependency_origins(c.get('dependency_origins'))
        print(json.dumps({'kind': 'TRAINING_READY_V1', 'nonce': c['nonce'],
                          'worker_pid': os.getpid(), 'worker_create_time': psutil.Process().create_time(),
                          'worker_sha256': c['worker_sha256'], 'config_sha256': c['config_sha256'],
                          'dependency_origins': origins}), flush=True)
        go = json.loads(sys.stdin.readline(65537))
        if go.get('kind') != 'TRAINING_GO_V1' or go.get('nonce') != c['nonce']:
            raise ValueError('missing_or_bad_go')
        # Close the race window between READY and GO as far as byte hashing can;
        # same-user writers still are not an OS-enforced security boundary.
        if sha(worker) != c['worker_sha256'] or sha(config) != c['config_sha256']:
            raise ValueError('bytes_changed_after_challenge')
        check_dependency_origins(c['dependency_origins'])
        if not parent.is_running() or abs(parent.create_time() - c['supervisor_create_time']) > .001:
            raise ValueError('parent_lost_before_go')
        return {'handshake': 'PASS', 'supervisor_pid': parent.pid,
                'config_sha256': c['config_sha256'], 'worker_sha256': c['worker_sha256'],
                'dependency_origins': origins}
    except Exception as exc:
        raise RuntimeError('TRAINING_SUPERVISOR_AUTHORIZATION_FAILED: ' + str(exc)) from exc
