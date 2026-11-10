"""Run one frozen task under a resource supervisor, preserving all arms and failures."""
from __future__ import annotations
from pathlib import Path
import argparse,csv,datetime,hashlib,json,math,os,re,subprocess,sys,time,uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.experiments import check_env,resources

CODE=['scripts/experiments/worker.py','src/data_asset_poc/__init__.py',
      'src/data_asset_poc/assessment/__init__.py','src/data_asset_poc/assessment/selection.py',
      'src/data_asset_poc/assessment/value_methods.py','src/data_asset_poc/assessment/binding.py']
INFRASTRUCTURE=['scripts/experiments/run_task.py','scripts/experiments/resources.py',
                'scripts/experiments/check_env.py','scripts/training/supervisor.py']
SEEDS={1103,2207,3301}

def sha(p):return resources.sha(p)
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,x):
    with Path(p).open('x',encoding='utf8',newline='\n') as f:json.dump(x,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def safe(base,rel):
    if not isinstance(rel,str) or not rel or '\\' in rel or Path(rel).is_absolute():raise ValueError('INVALID_INPUT_PATH')
    p=(Path(base)/rel).resolve();p.relative_to(Path(base).resolve());return p
def validate_inputs(config,data_dir):
    seen=set()
    if not isinstance(config.get('inputs'),list) or not config['inputs']:raise ValueError('INPUT_LIST_REQUIRED')
    for v in config['inputs']:
        path=v['path'];expected=v['sha256']
        if path in seen or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('INVALID_OR_DUPLICATE_INPUT')
        seen.add(path)
        if sha(safe(data_dir,path))!=expected:raise ValueError('INPUT_HASH_MISMATCH:'+path)
    for key in ('selection_input','audit_reference','development_input','test_input','injection_truth'):
        if config.get(key) not in seen:raise ValueError('REQUIRED_INPUT_UNDECLARED:'+key)
def resolve_config(task_id,seed):
    if task_id=='SMOKE-01' and seed==1103:return ROOT/'tests/fixtures/community_smoke/config.json'
    if not re.fullmatch('EXP-0[1-6]',task_id) or seed not in SEEDS:raise ValueError('UNKNOWN_TASK_OR_SEED')
    return ROOT/'experiments/community/configs'/task_id/(str(seed)+'.json')
def git_state():
    try:
        p=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,timeout=5,check=True)
        commit=p.stdout.strip()
        s=subprocess.run(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,capture_output=True,text=True,timeout=5,check=True)
        return {'git_commit':commit if re.fullmatch('[0-9a-f]{40}',commit) else 'UNKNOWN','git_dirty':bool(s.stdout.strip()),'untracked_files_in_dirty_check':True}
    except (OSError,subprocess.SubprocessError):return {'git_commit':'UNKNOWN','git_dirty':'UNKNOWN','untracked_files_in_dirty_check':True}
def code_manifest():return [{'path':p,'sha256':sha(ROOT/p)} for p in CODE]
def infrastructure_manifest():return [{'path':p,'sha256':sha(ROOT/p)} for p in INFRASTRUCTURE]
def _safe_error(exc,paths):return type(exc).__name__+':'+resources._sandbox_paths(str(exc),paths)

class Budget:
    """Per-output-directory serial ledger; not a global budget across machines/directories."""
    def __init__(self,root,smoke,timeout,run_id):
        self.p=Path(root)/('.smoke-budget.json' if smoke else '.community-budget.json');self.lock=self.p.with_suffix('.lock')
        self.timeout=timeout;self.run_id=run_id;self.fd=None;self.reserved=timeout+5;self.data=None
    def _save(self):
        p=self.p.with_name(self.p.name+'.'+uuid.uuid4().hex+'.tmp');p.write_text(json.dumps(self.data,indent=2),encoding='utf8');os.replace(p,self.p)
    def reserve(self):
        self.fd=os.open(str(self.lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.write(self.fd,b'Budget reservation in progress; inspect before manual recovery.\n');os.fsync(self.fd)
        self.data=read(self.p) if self.p.exists() else {'limit_seconds':1800,'spent_seconds':0.,'runs':[],'scope':'one output parent, not global'}
        if self.data.get('limit_seconds')!=1800 or any(x.get('state')=='RESERVED' for x in self.data['runs']):raise ValueError('UNRESOLVED_BUDGET_RESERVATION')
        if self.data['spent_seconds']+self.reserved>1800:raise ValueError('CUMULATIVE_LOCAL_BUDGET_EXHAUSTED')
        self.data['spent_seconds']+=self.reserved;self.data['runs'].append({'run_id':self.run_id,'state':'RESERVED','reserved_seconds':self.reserved});self._save()
    def finish(self,elapsed):
        if self.data and self.data['runs'] and self.data['runs'][-1]['run_id']==self.run_id:
            entry=self.data['runs'][-1];entry.update(state='CLOSED',actual_wall_seconds=elapsed)
            self.data['spent_seconds']-=self.reserved;self.data['spent_seconds']+=elapsed;self._save()
    def close(self):
        if self.fd is not None:
            os.close(self.fd);self.fd=None
            try:self.lock.unlink()
            except FileNotFoundError:pass

def main():
    p=argparse.ArgumentParser(description='One supervised, frozen CPU task per invocation; no downloads.')
    p.add_argument('--task-id',required=True);p.add_argument('--seed',type=int,required=True);p.add_argument('--data-dir')
    p.add_argument('--output',required=True,help='new runs are created in unique child directories here')
    p.add_argument('--max-workers',type=int,default=1);p.add_argument('--timeout',type=int,default=300)
    p.add_argument('--device',default='cpu',choices=['cpu']);p.add_argument('--dry-run',action='store_true')
    p.add_argument('--max-rss-gib',type=float,default=1.5,help='process-tree RSS ceiling, at most 1.5 GiB')
    p.add_argument('--min-free-gib',type=float,help='minimum system-available memory; cannot relax the default safety floor')
    p.add_argument('--allow-unverified-platform',action='store_true');p.add_argument('--github-user')
    a=p.parse_args();smoke=a.task_id=='SMOKE-01'
    if a.max_workers!=1 or not 1<=a.timeout<=300:p.error('Only one worker and 1..300 second timeout are supported')
    floor_default=.25 if smoke else .75
    min_free=floor_default if a.min_free_gib is None else a.min_free_gib
    if not math.isfinite(a.max_rss_gib) or not 0<a.max_rss_gib<=1.5 or not math.isfinite(min_free) or min_free<floor_default:
        p.error('Memory controls may tighten but not relax the frozen resource guards')
    if a.github_user and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?',a.github_user):p.error('Invalid optional GitHub username')
    config_path=resolve_config(a.task_id,a.seed);config=read(config_path)
    if config.get('task_id')!=a.task_id or config.get('seed')!=a.seed:raise ValueError('CONFIG_TASK_MISMATCH')
    if smoke and config.get('scope')!='SYNTHETIC_SMOKE_NON_SCIENTIFIC':raise ValueError('SMOKE_SCOPE_MISMATCH')
    data_dir=Path(a.data_dir).resolve() if a.data_dir else (ROOT/'tests/fixtures/community_smoke' if smoke else None)
    if data_dir is None:raise ValueError('--data-dir is required for scientific tasks')
    env=check_env.snapshot();platform_status=env['resource_platform']
    preflight={'task_id':a.task_id,'seed':a.seed,'scope':'SYNTHETIC_SMOKE_NON_SCIENTIFIC' if smoke else 'PUBLIC_REPRODUCTION_NOT_BLIND',
               'config_sha256':sha(config_path),'input_hashes_verified':False,'device':'cpu','max_workers':1,'timeout_seconds':a.timeout,
               'available_memory_bytes':env['system_memory_available_bytes'],'required_memory_bytes':int(max(.5 if smoke else 1.5,min_free)*resources.GIB),
               'max_rss_gib':a.max_rss_gib,'min_free_gib':min_free,
               'platform':platform_status,'training_started':False,'git':git_state()}
    if a.dry_run:
        validate_inputs(config,data_dir);preflight['input_hashes_verified']=True
        preflight['status']='DRY_RUN_READY' if preflight['available_memory_bytes']>=preflight['required_memory_bytes'] and (platform_status=='WINDOWS_JOB' or platform_status=='LINUX_EXPERIMENTAL_UNVERIFIED' and a.allow_unverified_platform) else 'DRY_RUN_NOT_READY'
        print(json.dumps(preflight,ensure_ascii=False,indent=2));return 0 if preflight['status']=='DRY_RUN_READY' else 2
    parent=Path(a.output).resolve()
    # Scientific data and result files must stay outside the source repository.
    try:parent.relative_to(ROOT)
    except ValueError:pass
    else:raise ValueError('OUTPUT_MUST_BE_OUTSIDE_REPOSITORY')
    parent.mkdir(parents=True,exist_ok=True)
    run_id=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex
    out=parent/run_id;out.mkdir(exist_ok=False)
    inner=out/'worker_output';git=git_state();code=code_manifest();infra=infrastructure_manifest();config_hash=sha(config_path);budget=Budget(parent,smoke,a.timeout,run_id)
    started=datetime.datetime.now(datetime.timezone.utc).isoformat();result=None;errors=[];summary=None;inputs_ok=False
    try:
        validate_inputs(config,data_dir);inputs_ok=True
        budget.reserve()
        result=resources.execute(config_path,data_dir,inner,a.timeout,smoke,a.allow_unverified_platform,lambda:validate_inputs(config,data_dir),a.max_rss_gib,min_free)
        (out/'RUN.log').write_text(result.pop('log',''),encoding='utf8')
        if result['status']=='COMPLETE':
            summary=read(inner/'SUMMARY.json')
            if summary['status']!='COMPLETE' or summary['seed']!=a.seed or summary['dataset']!=config['dataset']:raise ValueError('WORKER_SUMMARY_MISMATCH')
            if sha(config_path)!=config_hash or code_manifest()!=code or infrastructure_manifest()!=infra:raise ValueError('CODE_OR_CONFIG_CHANGED_DURING_RUN')
            validate_inputs(config,data_dir)
            methods=[m['method'] for m in summary['methods']]
            if methods!=['D0']+config['expected_methods']:raise ValueError('INCOMPLETE_ARMS')
            write(out/'METRICS.json',summary)
            with (out/'PREDICTIONS.csv').open('x',encoding='utf8',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['arm','split','id','group','y','prediction','probabilities','classes','loss']);w.writeheader()
                for method in methods:
                    for split in ['dev','test']:
                        for row in read(inner/method/(split+'_PREDICTIONS.json')):
                            w.writerow({'arm':method,'split':split,'id':row['id'],'group':row['group'],'y':row['y'],'prediction':row['prediction'],
                                        'probabilities':json.dumps(row.get('probabilities'),separators=(',',':')) if 'probabilities' in row else '',
                                        'classes':json.dumps(row.get('classes'),separators=(',',':')) if 'classes' in row else '', 'loss':row['loss']})
        budget.finish(result.get('wall_seconds',0.))
    except Exception as exc:
        errors.append(_safe_error(exc,[ROOT,Path.home(),data_dir,parent]))
        if result is None:result={'status':('NOT_RUN_PREFLIGHT_OR_BUDGET' if inputs_ok else 'NOT_RUN_INVALID_INPUT'),'worker_started':False,'training_go_sent':False,'wall_seconds':0.,'cpu_seconds':0.,'peak_rss_bytes':0,'worker_limit_seconds':a.timeout,'stop_reasons':errors}
        else:result['status']='FAILED_OUTPUT_CHECK'
    finally:budget.close()
    if not (out/'RUN.log').exists():(out/'RUN.log').write_text('\n'.join(errors)+'\n',encoding='utf8')
    status=result['status'];write(out/'ENVIRONMENT.json',env)
    cost={'wall_seconds':result.get('wall_seconds',0.),'cpu_seconds':result.get('cpu_seconds',0.),'peak_rss_bytes':result.get('peak_rss_bytes',0),
          'worker_limit_seconds':a.timeout,'max_workers':1,'device':'cpu','gpu_used':False,'gpu_seconds':None,'peak_gpu_memory_bytes':None,
          'cpu_utilization':'NOT_RECORDED','sample_budget_per_arm':config['k'],'token_budget':'NOT_APPLICABLE_TABULAR',
          'local_cumulative_budget_seconds':1800,'budget_scope':'per output directory; cannot enforce across machines or directories',
          'worker_monitor':result,'timing':summary.get('timing') if summary else None}
    write(out/'COST.json',cost)
    (out/'README.md').write_text('# Run result\n\n'+('Synthetic fixture only; not a scientific or independent training result.\n' if smoke else 'Public data reproduction, not a blind final evaluation.\n')+'Status: '+status+'. Verify before scientific review. Do not submit private local paths or credentials.\n',encoding='utf8')
    all_files=[]
    for f in sorted(out.rglob('*')):
        if f.is_file():all_files.append({'path':f.relative_to(out).as_posix(),'sha256':sha(f),'size_bytes':f.stat().st_size})
    typ=config['task_type'];manifest={'schema_version':'1.0','run_id':run_id,'task_id':a.task_id,'seed':a.seed,'status':status,'created_utc':started,
       'scope':'SYNTHETIC_SMOKE_NON_SCIENTIFIC' if smoke else 'PUBLIC_REPRODUCTION_NOT_BLIND','config_sha256':config_hash,
       'worker_sha256':sha(ROOT/CODE[0]),'git_commit':git['git_commit'],'git_dirty':git['git_dirty'],
       'untracked_files_in_dirty_check':True,'git_after':git_state(),'code':code,'infrastructure':infra,'inputs':config['inputs'],'files':all_files,
       'target_model':'LogisticRegression' if typ=='classification' else 'Ridge','primary_metric':'LogLoss' if typ=='classification' else 'MAE',
       'expected_methods':config['expected_methods'],'optional_contributor_github':a.github_user,'errors':errors}
    write(out/'RUN_MANIFEST.json',manifest)
    verifier=ROOT/'scripts/experiments/verify_result.py';report=out/'VERIFY_REPORT.json'
    if status=='COMPLETE' and verifier.exists():
        process=subprocess.run([sys.executable,'-B',str(verifier),'--result',str(out),'--data-dir',str(data_dir),'--output',str(report)],cwd=ROOT,capture_output=True,text=True,encoding='utf8',errors='replace')
        if not report.exists():write(report,{'status':'NEEDS_REVIEW','reason':'VERIFIER_DID_NOT_EMIT_REPORT','exit_code':process.returncode})
    else:write(report,{'status':'NEEDS_REVIEW','reason':'RUN_INCOMPLETE_OR_VERIFIER_UNAVAILABLE'})
    v=read(report);print(json.dumps({'run_id':run_id,'status':status,'verify_status':v.get('status'),'scope':manifest['scope']},ensure_ascii=False))
    return 0 if status=='COMPLETE' and v.get('status')=='REPRODUCED' else 2
if __name__=='__main__':sys.exit(main())
