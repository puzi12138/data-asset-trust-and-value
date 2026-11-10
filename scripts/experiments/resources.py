"""Single-child resource supervision. Not a security boundary against the same user."""
from __future__ import annotations
from pathlib import Path
import ctypes,hashlib,json,math,os,queue,secrets,signal,subprocess,sys,threading,time
import psutil

ROOT=Path(__file__).resolve().parents[2]
WORKER=ROOT/'scripts/experiments/worker.py'
GIB=1024**3

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()

def _sandbox_paths(text,paths):
    """Reduce accidental local path disclosure; retain diagnostic error class/reason."""
    s=str(text)
    for p in sorted((str(Path(x).resolve()) for x in paths if x),key=len,reverse=True):
        s=s.replace(p,'<LOCAL_PATH>').replace(p.replace('\\','/'),'<LOCAL_PATH>')
    return s

def _linux_preexec(parent_pid,limit_seconds):
    # Called in the child before exec. POSIX CPU hard limit plus a 4-GiB virtual
    # address-space bound; monitored RSS is stricter and platform independent.
    import resource
    os.setsid()
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0:os._exit(125)  # PR_SET_PDEATHSIG
    if os.getppid()!=parent_pid:os._exit(125)
    hard=int(limit_seconds)+1
    resource.setrlimit(resource.RLIMIT_CPU,(int(limit_seconds),hard))
    resource.setrlimit(resource.RLIMIT_AS,(4*GIB,4*GIB))

def _stop(child,created,job):
    if job is not None:
        job.close();return
    try:
        p=psutil.Process(child.pid)
        if abs(p.create_time()-created)>.001:return
        if sys.platform.startswith('linux'):
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        else:
            for c in reversed(p.children(recursive=True)):
                try:c.kill()
                except psutil.Error:pass
            p.kill()
    except psutil.Error:pass

def execute(config,data_dir,out_dir,timeout,smoke=False,allow_unverified_platform=False,verify_inputs=None,max_rss_gib=1.5,min_free_gib=None):
    """Bind process before GO and sample limits until exit. Never publish local paths."""
    is_windows=sys.platform=='win32';is_linux=sys.platform.startswith('linux')
    if not is_windows and not is_linux:raise RuntimeError('UNSUPPORTED_RESOURCE_PLATFORM')
    if is_linux and not allow_unverified_platform:raise RuntimeError('LINUX_EXPERIMENTAL_REQUIRES_EXPLICIT_OPT_IN')
    from scripts.training.supervisor import Job,worker_runtime_environment
    default_floor=.25 if smoke else .75
    selected_floor=min_free_gib if min_free_gib is not None else default_floor
    if not isinstance(timeout,int) or not 1<=timeout<=300 or not math.isfinite(max_rss_gib) or not 0<max_rss_gib<=1.5 or not math.isfinite(selected_floor) or selected_floor<default_floor:
        raise ValueError('INVALID_OR_RELAXED_RESOURCE_GUARD')
    floor=int(selected_floor*GIB)
    mem=psutil.virtual_memory();start_threshold=max(int((.5 if smoke else 1.5)*GIB),floor)
    tree_limit=int(max_rss_gib*GIB)
    result={'status':'INITIAL','worker_started':False,'training_go_sent':False,
            'preflight_available_bytes':mem.available,'start_threshold_bytes':start_threshold,
            'memory_floor_bytes':floor,'worker_rss_limit_bytes':tree_limit,
            'worker_limit_seconds':timeout,'platform':'WINDOWS_JOB' if is_windows else 'LINUX_EXPERIMENTAL_UNVERIFIED',
            'parent_death_guard':'windows_job_kill_on_close' if is_windows else 'linux_pdeathsig_and_process_group',
            'peak_rss_bytes':0,'minimum_available_bytes':mem.available,'cpu_seconds':0.,'wall_seconds':0.,
            'exit_code':None,'stop_reasons':[],'log':''}
    if mem.available<start_threshold:
        result['status']='NOT_RUN_RESOURCE_PREFLIGHT';return result
    try:runtime=worker_runtime_environment(False)
    except Exception as e:
        result.update(status='NOT_RUN_DEPENDENCY_PREFLIGHT',log=type(e).__name__+':'+_sandbox_paths(e,[ROOT,Path.home(),data_dir,out_dir]));return result
    env=os.environ.copy();env.update({k:'2' for k in ['OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS']})
    env['CUDA_VISIBLE_DEVICES']='';env.pop('PYTHONHOME',None);env['PYTHONPATH']=os.pathsep.join(runtime['site_directories']);env['PYTHONNOUSERSITE']='1'
    cmd=[runtime['executable'],'-S','-u',str(WORKER),'--config',str(config),'--data-dir',str(data_dir),'--out',str(out_dir)]
    kw={}
    # os.getpid in preexec runs after fork, thus capture the real parent explicitly.
    if is_linux:
        parent_pid=os.getpid();kw['preexec_fn']=lambda:_linux_preexec(parent_pid,timeout)
    own=psutil.Process();child=None;job=None;created=None;handed=False;go=False;start=time.monotonic();q=queue.Queue();lines=[]
    try:
        child=subprocess.Popen(cmd,cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                               text=True,encoding='utf8',errors='replace',bufsize=1,**kw)
        proc=psutil.Process(child.pid);created=proc.create_time();result['worker_started']=True
        if is_windows:job=Job(child)  # assignment occurs while worker waits for challenge
        try:
            allowed=proc.cpu_affinity()[:2]
            if not allowed:raise RuntimeError('NO_ALLOWED_CPUS')
            proc.cpu_affinity(allowed)
        except (AttributeError,NotImplementedError):pass
        def drain():
            for line in iter(child.stdout.readline,''):q.put(line)
            q.put(None)
        reader=threading.Thread(target=drain,daemon=True);reader.start()
        nonce=secrets.token_hex(32)
        challenge={'kind':'COMMUNITY_CHALLENGE_V1','nonce':nonce,'pid':own.pid,'created':own.create_time(),
                   'config':str(Path(config).resolve()),'out':str(Path(out_dir).resolve()),'data_dir':str(Path(data_dir).resolve()),
                   'worker_sha256':sha(WORKER),'config_sha256':sha(config),'origins':runtime['dependency_origins']}
        child.stdin.write(json.dumps(challenge)+'\n');child.stdin.flush()
        while child.poll() is None:
            while not q.empty():
                line=q.get_nowait()
                if line is None:continue
                if len(lines)<10000:lines.append(line[:10000])
                if not handed:
                    try:ready=json.loads(line)
                    except ValueError:ready={}
                    valid=(ready.get('kind')=='COMMUNITY_READY_V1' and ready.get('nonce')==nonce and ready.get('pid')==child.pid
                           and abs(ready.get('created',0)-created)<.001 and ready.get('worker_sha256')==challenge['worker_sha256']
                           and ready.get('config_sha256')==challenge['config_sha256'])
                    if not valid:result['stop_reasons'].append('BAD_HANDSHAKE');break
                    handed=True
                    if verify_inputs:verify_inputs()
                    if sha(WORKER)!=challenge['worker_sha256'] or sha(config)!=challenge['config_sha256']:raise RuntimeError('CODE_OR_CONFIG_CHANGED_BEFORE_GO')
                    child.stdin.write(json.dumps({'kind':'COMMUNITY_GO_V1','nonce':nonce})+'\n');child.stdin.flush();child.stdin.close();go=True;result['training_go_sent']=True
            elapsed=time.monotonic()-start
            try:
                p=psutil.Process(child.pid)
                if abs(p.create_time()-created)>.001:raise psutil.NoSuchProcess(child.pid)
                processes=[p]+p.children(recursive=True);rss=0;cpu=0.
                for c in processes:
                    try:
                        if 'allowed' in locals():c.cpu_affinity(allowed)
                        rss+=c.memory_info().rss;t=c.cpu_times();cpu+=t.user+t.system
                    except psutil.NoSuchProcess:pass
                free=psutil.virtual_memory().available;result['peak_rss_bytes']=max(result['peak_rss_bytes'],rss);result['minimum_available_bytes']=min(result['minimum_available_bytes'],free);result['cpu_seconds']=max(result['cpu_seconds'],cpu)
                if rss>tree_limit:result['stop_reasons'].append('TREE_RSS_LIMIT')
                if free<floor:result['stop_reasons'].append('SYSTEM_MEMORY_FLOOR')
            except psutil.NoSuchProcess:break
            if elapsed>=timeout:result['stop_reasons'].append('WALL_TIME_LIMIT')
            if not handed and elapsed>15:result['stop_reasons'].append('HANDSHAKE_TIMEOUT')
            if result['stop_reasons']:break
            time.sleep(.1)
        if result['stop_reasons']:_stop(child,created,job);job=None
        if job:job.close();job=None
        try:child.wait(timeout=3)
        except subprocess.TimeoutExpired:_stop(child,created,None);child.wait(timeout=3)
        reader.join(timeout=2)
        while not q.empty():
            line=q.get_nowait()
            if line is not None and len(lines)<10000:lines.append(line[:10000])
        result['exit_code']=child.returncode
        result['status']='COMPLETE' if go and handed and child.returncode==0 and not result['stop_reasons'] else 'FAILED_WORKER_OR_RESOURCE'
    except Exception as e:
        result['status']='FAILED_SUPERVISOR';result['stop_reasons'].append(type(e).__name__+':'+str(e))
    finally:
        if child is not None and child.poll() is None:_stop(child,created,job);job=None
        if job:job.close()
        result['wall_seconds']=time.monotonic()-start
        result['log']=_sandbox_paths(''.join(lines),[ROOT,Path.home(),data_dir,out_dir])
        result['stop_reasons']=[_sandbox_paths(v,[ROOT,Path.home(),data_dir,out_dir]) for v in result['stop_reasons']]
    return result
