"""dataset serialized CPU training supervisor with a persistent, fail-closed budget.

Only the fixed dataset worker is used for real experiments. Windows Job Object
kill-on-close prevents an orphan worker when this supervisor dies. The worker
remains blocked on its inherited stdin until bound and monitored. This is a
resource/accident guard, not a security boundary against the same user.
"""
from pathlib import Path
import argparse
import ast
import ctypes
import hashlib
import importlib.util
import json
import os
import queue
import secrets
import site
import subprocess
import sys
import threading
import time
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[2]
TRAIN = ROOT / 'runtime/training'
FREEZE = TRAIN / 'FREEZE.json'
WORKER = ROOT / 'scripts/training/worker.py'
GUARD = ROOT / 'scripts/training/guard.py'
TEST = ROOT / 'build/training_supervisor/tests'
TEST_WORKER = ROOT / 'tests/fixtures/training_dummy_worker.py'
GIB = 1024 ** 3
LIMITS = {'system_start_bytes': int(1.5 * GIB), 'system_floor_bytes': int(.75 * GIB),
          'tree_rss_bytes': int(1.5 * GIB), 'worker_wall_seconds': 300,
          'cumulative_wall_seconds': 1800, 'reservation_seconds': 305,
          'cpu_threads': 2, 'gpu_used': False, 'sampling_seconds': .1}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def norm(p):
    return os.path.normcase(os.path.realpath(str(p)))


def worker_runtime_environment(is_test):
    """Select the same active site-package directories and dependency origins.

    A Windows venv launcher may create an intermediate process.  The worker
    must remain the supervisor's direct child for the PID handshake and Job
    Object.  Launch the currently running base interpreter with site disabled,
    and explicitly expose only the active interpreter's package directories.
    The worker checks every required origin again before and after GO.
    """
    site_dirs = list(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        site_dirs.append(site.getusersitepackages())
    eligible = {norm(p) for p in site_dirs if isinstance(p, str) and Path(p).is_dir()}
    active = []
    for entry in sys.path:
        if isinstance(entry, str) and entry and norm(entry) in eligible and norm(entry) not in {norm(p) for p in active}:
            active.append(str(Path(entry).resolve()))
    dependencies = ['psutil'] if is_test else ['psutil', 'numpy', 'scipy', 'sklearn', 'joblib', 'threadpoolctl']
    origins = {}
    for name in dependencies:
        spec = importlib.util.find_spec(name)
        origin = None if spec is None else spec.origin
        if not origin or not any(Path(origin).resolve().is_relative_to(Path(p)) for p in active):
            raise ValueError('DEPENDENCY_NOT_IN_ACTIVE_SITE:' + name)
        origins[name] = str(Path(origin).resolve())
    executable = str(Path(psutil.Process().exe()).resolve())
    if norm(executable) != norm(getattr(sys, '_base_executable', sys.executable)):
        raise ValueError('INTERPRETER_PROCESS_MISMATCH')
    return {'executable': executable, 'site_directories': active, 'dependency_origins': origins,
            'parent_prefix': str(Path(sys.prefix).resolve()), 'site_disabled_in_worker': True}


def confined(path, folder):
    path = Path(path).resolve()
    path.relative_to(folder.resolve())
    return path


def write_json(path, data, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open('x', encoding='utf8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)


def static_local_imports(start):
    """Collect statically resolved project modules; this is not a sandbox.

    A project-local module (including each package __init__) imported by the
    fixed worker must be frozen, including package initializers and utility code.
    Indirect runtime module loading cannot be comprehensively established by
    AST; obvious __import__/importlib.import_module calls are refused and the
    worker remains separately reviewed. Third-party/runtime binaries are not
    hashed by this function and are instead documented by the environment.
    """
    found = set()
    work = [Path(start).resolve()]

    def add_module(parts):
        if not parts:
            return
        for search_root in (ROOT, ROOT / 'src'):
            for i in range(1, len(parts)):
                init = search_root.joinpath(*parts[:i], '__init__.py')
                if init.is_file():
                    work.append(init.resolve())
            p = search_root.joinpath(*parts)
            if p.with_suffix('.py').is_file():
                work.append(p.with_suffix('.py').resolve())
            if (p / '__init__.py').is_file():
                work.append((p / '__init__.py').resolve())

    while work:
        path = work.pop()
        path.relative_to(ROOT.resolve())
        if path in found:
            continue
        found.add(path)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        package = list(path.relative_to(ROOT).with_suffix('').parts[:-1])
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if ((isinstance(fn, ast.Name) and fn.id == '__import__') or
                    (isinstance(fn, ast.Attribute) and fn.attr == 'import_module')):
                    raise ValueError('DYNAMIC_IMPORT_NOT_ALLOWED_IN_FROZEN_PROJECT_CODE:' + str(path.relative_to(ROOT)))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add_module(alias.name.split('.'))
                    # Python loads bare names from the executing script's folder.
                    if '.' not in alias.name and (path.parent / (alias.name + '.py')).is_file():
                        work.append((path.parent / (alias.name + '.py')).resolve())
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        raise ValueError('INVALID_RELATIVE_IMPORT:' + str(path))
                    base = package[:len(package) - node.level + 1]
                else:
                    base = []
                if node.module:
                    base += node.module.split('.')
                add_module(base)
                for alias in node.names:
                    if alias.name != '*':
                        add_module(base + alias.name.split('.'))
    return found


def frozen_inputs(config):
    """Verify all declared dataset inputs and require the actual implementation."""
    data = json.loads(FREEZE.read_text(encoding='utf-8-sig'))
    matches = [v for v in data['configs'] if norm(ROOT / v['path']) == norm(config)]
    if len(matches) != 1 or sha(config) != matches[0]['sha256']:
        raise ValueError('CONFIG_NOT_IN_FREEZE_OR_HASH_MISMATCH')
    code = data['code']
    by_path = {norm(ROOT / v['path']): v['sha256'] for v in code}
    if len(by_path) != len(code):
        raise ValueError('FREEZE_DUPLICATE_CODE_PATH')
    if not all(norm(p) in by_path for p in [WORKER, GUARD, Path(__file__), ROOT/'scripts/training/__init__.py']):
        raise ValueError('FREEZE_MISSING_SUPERVISOR_GUARD_OR_WORKER')
    for entry in data['configs'] + code:
        p = confined(ROOT / entry['path'], ROOT)
        if sha(p) != entry['sha256']:
            raise ValueError('FREEZE_HASH_MISMATCH:' + entry['path'])
    payload = json.loads(Path(config).read_text(encoding='utf-8-sig'))
    if payload.get('worker_sha256') != sha(WORKER):
        raise ValueError('CONFIG_WORKER_HASH_MISMATCH')
    required = static_local_imports(WORKER)
    missing = sorted(str(p.relative_to(ROOT)) for p in required if norm(p) not in by_path)
    if missing:
        raise ValueError('FREEZE_MISSING_STATIC_PROJECT_IMPORTS:' + ','.join(missing))
    inputs = payload.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('CONFIG_INPUT_HASHES_REQUIRED')
    seen = set()
    for entry in inputs:
        p = confined(ROOT / entry['path'], ROOT)
        if norm(p) in seen:
            raise ValueError('CONFIG_DUPLICATE_INPUT:' + entry['path'])
        seen.add(norm(p))
        if sha(p) != entry['sha256']:
            raise ValueError('CONFIG_INPUT_HASH_MISMATCH:' + entry['path'])
    return sha(FREEZE)


class Job:
    """Use a Windows Job Object with KILL_ON_JOB_CLOSE, fail closed elsewhere."""
    def __init__(self, child):
        if os.name != 'nt':
            raise RuntimeError('TRAINING_WINDOWS_JOB_NOT_AVAILABLE_ON_THIS_HOST')
        from ctypes import wintypes as w
        size_t = ctypes.c_size_t
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', size_t), ('MaximumWorkingSetSize', size_t),
                        ('ActiveProcessLimit', w.DWORD), ('Affinity', size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ['ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount']]
        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', size_t),
                        ('JobMemoryLimit', size_t), ('PeakProcessMemoryUsed', size_t), ('PeakJobMemoryUsed', size_t)]
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]; k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]; k.SetInformationJobObject.restype = w.BOOL
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]; k.AssignProcessToJobObject.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]; k.CloseHandle.restype = w.BOOL
        self.k = k; self.handle = k.CreateJobObjectW(None, None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        try:
            limit = Extended(); limit.BasicLimitInformation.LimitFlags = 0x00002000
            if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not k.AssignProcessToJobObject(self.handle, w.HANDLE(int(child._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close(); raise

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle); self.handle = None


def stop_owned(child, created):
    """Terminate only this Popen child and the verified descendants it owns."""
    try:
        proc = psutil.Process(child.pid)
        if abs(proc.create_time() - created) > .001: return False
        descendants = [(c, c.create_time()) for c in proc.children(recursive=True)]
        for p, stamp in reversed(descendants):
            try:
                if abs(p.create_time() - stamp) < .001: p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        proc.terminate()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def run(config, out, receipt, *, self_test=None):
    is_test = self_test is not None
    folder = TEST if is_test else TRAIN
    config = confined(config, folder); out = confined(out, folder); receipt = confined(receipt, folder)
    if not is_test:
        out.relative_to((TRAIN / 'runs').resolve())
    if out.exists() or receipt.exists(): raise FileExistsError('NEW_OUTPUT_AND_RECEIPT_REQUIRED')
    if is_test:
        if self_test not in ['ok', 'timeout', 'memory', 'forged', 'crash', 'low_memory']:
            raise ValueError('UNKNOWN_TEST_MODE')
        worker = TEST_WORKER
        freeze_hash = 'SELF_TEST_NOT_SCIENCE'
        if json.loads(config.read_text(encoding='utf8'))['worker_sha256'] != sha(worker):
            raise ValueError('TEST_WORKER_HASH_MISMATCH')
        limits = dict(LIMITS)
        limits['worker_wall_seconds'] = .7 if self_test == 'timeout' else 20 if self_test == 'crash' else 3
        if self_test == 'memory': limits['tree_rss_bytes'] = 1
    else:
        worker = WORKER
        # Keep an explicit stop receipt when any frozen input is missing or
        # altered.
        try:
            freeze_hash = frozen_inputs(config)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            result = {'scope': 'DATASET_REAL_TRAINING', 'status': 'NOT_RUN_FROZEN_INPUT_FAILURE',
                      'worker_started': False, 'training_go_sent': False, 'simulation': False,
                      'config_path': str(config), 'worker_path': str(worker), 'limits': dict(LIMITS),
                      'error': type(exc).__name__ + ': ' + str(exc)}
            write_json(receipt, result, exclusive=True)
            return result
        limits = dict(LIMITS)
    result = {'scope': 'DUMMY_SELF_TEST_SIMULATED_LIMITS' if is_test else 'DATASET_REAL_TRAINING',
              'status': 'INITIAL', 'worker_started': False, 'training_go_sent': False, 'samples': [],
              'config_path': str(config), 'config_sha256': sha(config), 'worker_path': str(worker),
              'worker_sha256': sha(worker), 'freeze_sha256': freeze_hash, 'limits': limits,
              'runtime_semantics': '300s maximum per worker, one worker can fit several frozen models',
              'simulation': bool(is_test)}
    available = psutil.virtual_memory().available
    if is_test and self_test == 'low_memory': available = 0
    result['preflight_available_bytes'] = available
    if available < limits['system_start_bytes']:
        result.update(status='NOT_RUN_RESOURCE_PREFLIGHT', additional_available_bytes_required=limits['system_start_bytes']-available)
        write_json(receipt, result, exclusive=True); return result
    try:
        runtime = worker_runtime_environment(is_test)
    except (OSError, ValueError, ImportError, AttributeError) as exc:
        result.update(status='NOT_RUN_DEPENDENCY_PREFLIGHT', error=type(exc).__name__ + ': ' + str(exc))
        write_json(receipt, result, exclusive=True); return result
    result['worker_runtime'] = runtime
    ledger = (folder / 'COMPUTE_BUDGET.json') if not is_test else receipt.parent / 'COMPUTE_BUDGET.json'
    lock = ledger.with_suffix('.lock'); lock_fd = None; p = None; job = None; start = None; reserved = False
    sampler = queue.Queue(); lines = []; stop_reasons = []; handed = False; go = False; exhausted = False
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            result['status'] = 'NOT_RUN_LEDGER_LOCK_HELD'; return result
        own = psutil.Process()
        os.write(lock_fd, json.dumps({'pid': own.pid, 'create_time': own.create_time(),
                                      'note': 'Do not auto-remove after crash; inspect owned worker and retained reservation.'}).encode()); os.fsync(lock_fd)
        if ledger.exists():
            budget = json.loads(ledger.read_text(encoding='utf8'))
            if budget['freeze_sha256'] != freeze_hash or budget['total_seconds'] != limits['cumulative_wall_seconds']:
                raise ValueError('PERSISTENT_BUDGET_FREEZE_MISMATCH')
            if any(r['state'] == 'RESERVED' for r in budget['runs']):
                result['status'] = 'NOT_RUN_UNRESOLVED_RESERVATION'; return result
        else:
            budget = {'schema': 1, 'freeze_sha256': freeze_hash, 'total_seconds': limits['cumulative_wall_seconds'],
                      'spent_seconds': 0., 'runs': [], 'scope': result['scope']}
        reserve = limits['reservation_seconds']
        if budget['spent_seconds'] + reserve > budget['total_seconds']:
            result.update(status='NOT_RUN_CUMULATIVE_BUDGET', budget_spent_seconds=budget['spent_seconds']); exhausted = True; return result
        run_id = uuid.uuid4().hex; start = time.monotonic()
        entry = {'run_id': run_id, 'state': 'RESERVED', 'reserved_seconds': reserve, 'config_sha256': result['config_sha256'],
                 'supervisor_pid': own.pid, 'supervisor_create_time': own.create_time(), 'worker_pid': None, 'worker_create_time': None}
        budget['spent_seconds'] += reserve; budget['runs'].append(entry); write_json(ledger, budget); reserved = True
        env = os.environ.copy(); env.update({k: '2' for k in ['OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS']})
        env['CUDA_VISIBLE_DEVICES'] = ''
        env.pop('PYTHONHOME', None)
        env['PYTHONPATH'] = os.pathsep.join(runtime['site_directories'])
        env['PYTHONNOUSERSITE'] = '1'
        cmd = [runtime['executable'], '-S', '-u', str(worker), '--config', str(config), '--out', str(out)]
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf8', errors='replace', bufsize=1)
        process = psutil.Process(p.pid); created = process.create_time()
        result.update(worker_started=True, worker_pid=p.pid, worker_create_time=created)
        entry.update(worker_pid=p.pid, worker_create_time=created); write_json(ledger, budget)
        job = Job(p)
        allowed = process.cpu_affinity()[:2]
        if not allowed: raise RuntimeError('NO_CPU_AFFINITY_AVAILABLE')
        process.cpu_affinity(allowed); result['cpu_affinity'] = allowed
        def drain():
            try:
                for line in iter(p.stdout.readline, ''): sampler.put(line)
            finally: sampler.put(None)
        reader = threading.Thread(target=drain, daemon=True); reader.start()
        nonce = secrets.token_hex(32)
        challenge = {'kind': 'TRAINING_CHALLENGE_V1', 'nonce': nonce, 'supervisor_pid': own.pid,
                     'supervisor_create_time': own.create_time(), 'supervisor_exe': own.exe(),
                     'dependency_origins': runtime['dependency_origins'],
                     'supervisor_sha256': sha(Path(__file__)), 'worker_path': str(worker), 'worker_sha256': sha(worker),
                     'config_path': str(config), 'config_sha256': sha(config), 'out_dir': str(out)}
        p.stdin.write(json.dumps(challenge) + '\n'); p.stdin.flush()
        while p.poll() is None:
            while not sampler.empty():
                line = sampler.get_nowait()
                if line is None: continue
                if len(lines) < 5000: lines.append(line[:10000])
                if not handed:
                    try: ready = json.loads(line)
                    except ValueError: ready = {}
                    if (ready.get('kind') != 'TRAINING_READY_V1' or ready.get('nonce') != nonce or
                        ready.get('worker_pid') != p.pid or abs(ready.get('worker_create_time', 0)-created) > .001 or
                        ready.get('worker_sha256') != challenge['worker_sha256'] or ready.get('config_sha256') != challenge['config_sha256'] or
                        ready.get('dependency_origins') != challenge['dependency_origins']):
                        stop_reasons.append('BAD_HANDSHAKE'); break
                    handed = True
                    # Validate frozen inputs again immediately before issuing GO.
                    if not is_test and frozen_inputs(config) != freeze_hash: raise ValueError('FREEZE_CHANGED_BEFORE_GO')
                    if sha(worker) != challenge['worker_sha256'] or sha(config) != challenge['config_sha256']:
                        stop_reasons.append('INPUT_CHANGED_BEFORE_GO'); break
                    p.stdin.write(json.dumps({'kind':'TRAINING_GO_V1','nonce':nonce})+'\n'); p.stdin.flush(); p.stdin.close(); go = True
                    result['training_go_sent'] = not is_test
            elapsed = time.monotonic() - start
            try:
                if abs(process.create_time() - created) > .001: raise psutil.NoSuchProcess(p.pid)
                procs = [process] + process.children(recursive=True)
                rss = 0
                for child in procs:
                    try:
                        child.cpu_affinity(allowed); rss += child.memory_info().rss
                    except psutil.NoSuchProcess: pass
                free = psutil.virtual_memory().available
                result['samples'].append({'elapsed_seconds': elapsed, 'tree_rss_bytes': rss,
                                          'available_bytes': free, 'pids': [x.pid for x in procs]})
                if rss > limits['tree_rss_bytes']: stop_reasons.append('TREE_RSS_LIMIT')
                if free < limits['system_floor_bytes']: stop_reasons.append('SYSTEM_MEMORY_FLOOR')
            except psutil.NoSuchProcess:
                break
            if elapsed >= limits['worker_wall_seconds']: stop_reasons.append('WORKER_WALL_TIME_LIMIT')
            if not handed and elapsed > 15: stop_reasons.append('HANDSHAKE_TIMEOUT')
            if stop_reasons: break
            time.sleep(limits['sampling_seconds'])
        if stop_reasons:
            result['owned_stop_attempted'] = stop_owned(p, created)
        if job: job.close(); job = None
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stop_owned(p, created); p.wait(timeout=3)
        reader.join(timeout=2)
        while not sampler.empty():
            line = sampler.get_nowait()
            if line is not None and len(lines) < 5000: lines.append(line[:10000])
        result.update(status='STOPPED_RESOURCE_OR_HANDSHAKE' if stop_reasons else 'COMPLETED' if p.returncode == 0 and handed and go else 'FAILED_WORKER',
                      stop_reasons=stop_reasons, exit_code=p.returncode, handshake_verified=handed, stdout=''.join(lines),
                      peak_sampled_tree_rss=max((s['tree_rss_bytes'] for s in result['samples']),default=0))
    except BaseException as exc:
        result.update(status='FAILED_SUPERVISOR', error=type(exc).__name__+': '+str(exc))
        if p and p.poll() is None:
            try: stop_owned(p, created)
            except (NameError, psutil.Error): pass
        if job: job.close(); job = None
        if p:
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired: pass
    finally:
        if job: job.close()
        if reserved:
            elapsed = time.monotonic() - start
            # If a supervisor crashes before this point, its full reservation
            # remains charged; stale lock is NOT removed automatically.
            budget['spent_seconds'] += elapsed - reserve
            entry.update(state='COMMITTED', elapsed_seconds=elapsed, status=result['status'])
            write_json(ledger, budget)
            result.update(elapsed_seconds=elapsed, budget_spent_seconds=budget['spent_seconds'],
                          budget_remaining_seconds=max(0, budget['total_seconds']-budget['spent_seconds']))
        if lock_fd is not None:
            os.close(lock_fd)
            try: lock.unlink()
            except FileNotFoundError: pass
        if not receipt.exists(): write_json(receipt, result, exclusive=True)
    return result


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('--config', type=Path, required=True); a.add_argument('--out', type=Path, required=True)
    a.add_argument('--receipt', type=Path, required=True); a.add_argument('--self-test', choices=['ok','timeout','memory','forged','crash','low_memory'])
    x = a.parse_args(); result = run(x.config, x.out, x.receipt, self_test=x.self_test)
    print(json.dumps({'status':result['status'], 'worker_started':result['worker_started'],
                      'training_go_sent':result['training_go_sent'], 'receipt':str(x.receipt)}))
    sys.exit(0 if result['status']=='COMPLETED' else 2)
