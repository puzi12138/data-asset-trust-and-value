"""Safety tests of supervisor mechanics with owned dummy processes only."""
from pathlib import Path
import hashlib,importlib.util,json,os,psutil,subprocess,sys,time,unittest,uuid
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'build/training_supervisor';TEST=BASE/'tests';SUP=ROOT/'scripts/training/supervisor.py';DUMMY=ROOT/'tests/fixtures/training_dummy_worker.py'
EXE=sys.executable
DIRECT=getattr(sys,'_base_executable',sys.executable)
DIRECT_ENV=dict(os.environ,PYTHONPATH=str(Path(psutil.__file__).resolve().parent.parent),PYTHONNOUSERSITE='1')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class Safety(unittest.TestCase):
 def setUp(self):
  self.d=TEST/uuid.uuid4().hex;self.d.mkdir(parents=True);self.config=self.d/'config.json';self.receipt_no=0
 def config_for(self,mode):
  self.config.write_text(json.dumps({'mode':mode,'worker_sha256':sha(DUMMY)}),encoding='utf8')
 def command(self,mode,receipt=None):
  self.receipt_no+=1;self.config_for('ok' if mode in ['ok','low_memory'] else 'forged' if mode=='forged' else 'sleep')
  return [EXE,str(SUP),'--config',str(self.config),'--out',str(self.d/f'out{self.receipt_no}'),'--receipt',str(receipt or self.d/f'receipt{self.receipt_no}.json'),'--self-test',mode]
 def invoke(self,mode):
  p=subprocess.run(self.command(mode),capture_output=True,text=True,timeout=15)
  return p,json.loads((self.d/f'receipt{self.receipt_no}.json').read_text())
 def test_direct_forged_environment_rejected(self):
  self.config_for('ok');env=dict(os.environ,FAKE_SUPERVISOR_PID=str(os.getpid()))
  env.update(DIRECT_ENV)
  p=subprocess.run([DIRECT,'-S',str(DUMMY),'--config',str(self.config),'--out',str(self.d/'out')],input='{}\n',env=env,capture_output=True,text=True,timeout=8)
  self.assertNotEqual(p.returncode,0);self.assertIn('TRAINING_SUPERVISOR_AUTHORIZATION_FAILED',p.stderr)
 def test_spoofed_pipe_parent_rejected(self):
  self.config_for('ok');c={'kind':'TRAINING_CHALLENGE_V1','nonce':'a'*64,'supervisor_pid':os.getpid(),'supervisor_create_time':psutil.Process().create_time(),'supervisor_exe':psutil.Process().exe()}
  p=subprocess.run([DIRECT,'-S',str(DUMMY),'--config',str(self.config),'--out',str(self.d/'out')],input=json.dumps(c)+'\n',env=DIRECT_ENV,capture_output=True,text=True,timeout=8)
  self.assertNotEqual(p.returncode,0);self.assertIn('parent_command_not_supervisor',p.stderr)
 def test_spoofed_supervisor_path_as_argument_rejected(self):
  self.config_for('ok');fake=self.d/'fake_parent.py'
  fake.write_text('import sys,os,json,psutil,subprocess\nfrom pathlib import Path\np=psutil.Process();c={"kind":"TRAINING_CHALLENGE_V1","nonce":"b"*64,"supervisor_pid":p.pid,"supervisor_create_time":p.create_time(),"supervisor_exe":p.exe()}\ne=dict(os.environ,PYTHONPATH=str(Path(psutil.__file__).resolve().parent.parent),PYTHONNOUSERSITE="1")\nr=subprocess.run([getattr(sys,"_base_executable",sys.executable),"-S",sys.argv[2],"--config",sys.argv[3],"--out",sys.argv[4]],input=json.dumps(c)+"\\n",env=e,capture_output=True,text=True)\nprint(r.stderr)\nsys.exit(0 if "parent_command_not_supervisor" in r.stderr else 1)\n',encoding='utf8')
  p=subprocess.run([EXE,str(fake),str(SUP),str(DUMMY),str(self.config),str(self.d/'out')],capture_output=True,text=True,timeout=8)
  self.assertEqual(p.returncode,0,p.stdout+p.stderr)
 def test_low_memory_start_refused(self):
  _,r=self.invoke('low_memory');self.assertEqual(r['status'],'NOT_RUN_RESOURCE_PREFLIGHT');self.assertFalse(r['worker_started']);self.assertFalse((self.d/'COMPUTE_BUDGET.json').exists())
 def test_bad_handshake_refused(self):
  _,r=self.invoke('forged');self.assertIn('BAD_HANDSHAKE',r['stop_reasons']);self.assertFalse(r['training_go_sent']);self.assertTrue(r['simulation'])
 def test_timeout_stops_own_worker(self):
  _,r=self.invoke('timeout');self.assertIn('WORKER_WALL_TIME_LIMIT',r['stop_reasons']);self.assertTrue(r['handshake_verified']);self.assertLess(r['elapsed_seconds'],8)
 def test_memory_stop_does_not_touch_sentinel(self):
  sentinel=subprocess.Popen([EXE,'-c','import time;time.sleep(15)'])
  try:
   _,r=self.invoke('memory');self.assertIn('TREE_RSS_LIMIT',r['stop_reasons']);self.assertIsNone(sentinel.poll());self.assertTrue(r['simulation']);self.assertLessEqual(len(r['cpu_affinity']),2)
  finally:sentinel.terminate();sentinel.wait(timeout=3)
 def test_two_runs_accumulate_without_reset(self):
  _,r1=self.invoke('ok');self.assertEqual(r1['status'],'COMPLETED');_,r2=self.invoke('ok');self.assertEqual(r2['status'],'COMPLETED')
  budget=json.loads((self.d/'COMPUTE_BUDGET.json').read_text());self.assertEqual(len(budget['runs']),2);self.assertGreater(budget['spent_seconds'],r1['budget_spent_seconds']);self.assertTrue(all(r['state']=='COMMITTED' for r in budget['runs']));self.assertFalse((self.d/'COMPUTE_BUDGET.lock').exists())
 def test_worker_psutil_origin_matches_current_environment(self):
  _,r=self.invoke('ok');self.assertEqual(r['status'],'COMPLETED')
  ready=next(json.loads(line) for line in r['stdout'].splitlines() if json.loads(line).get('kind')=='TRAINING_READY_V1')
  self.assertEqual(os.path.normcase(os.path.realpath(ready['dependency_origins']['psutil'])),os.path.normcase(os.path.realpath(psutil.__file__)))
  self.assertEqual(ready['dependency_origins'],r['worker_runtime']['dependency_origins'])
 def test_total_budget_denies_insufficient_reservation(self):
  (self.d/'COMPUTE_BUDGET.json').write_text(json.dumps({'schema':1,'freeze_sha256':'SELF_TEST_NOT_SCIENCE','total_seconds':1800,'spent_seconds':1495.1,'runs':[]}))
  _,r=self.invoke('ok');self.assertEqual(r['status'],'NOT_RUN_CUMULATIVE_BUDGET');self.assertFalse(r['worker_started'])
 def test_crash_retains_reservation_and_job_kills_worker(self):
  cmd=self.command('crash');proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);ledger=self.d/'COMPUTE_BUDGET.json';worker_pid=None;worker_created=None;supervisor_pid=None
  try:
   deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    try:
     launcher=psutil.Process(proc.pid);owned=[launcher]+launcher.children(recursive=True)
     by_command={}
     for child in owned:
      try:
       for argument in child.cmdline():
        normalized=os.path.normcase(os.path.realpath(argument))
        if normalized==os.path.normcase(os.path.realpath(SUP)):by_command['supervisor']=child
        if normalized==os.path.normcase(os.path.realpath(DUMMY)):by_command['worker']=child
      except (psutil.NoSuchProcess,psutil.AccessDenied):pass
     if 'worker' in by_command and 'supervisor' in by_command:
      worker_pid=by_command['worker'].pid;worker_created=by_command['worker'].create_time();supervisor_pid=by_command['supervisor'].pid;break
    except psutil.NoSuchProcess:pass
    time.sleep(.05)
   self.assertIsNotNone(worker_pid);self.assertIsNotNone(supervisor_pid);time.sleep(.5)
   data=json.loads(ledger.read_text());entry=data['runs'][0]
   self.assertEqual((entry['supervisor_pid'],entry['worker_pid']),(supervisor_pid,worker_pid))
   psutil.Process(supervisor_pid).kill();proc.wait(timeout=3)
   self.assertTrue((self.d/'COMPUTE_BUDGET.lock').exists());data=json.loads(ledger.read_text());self.assertEqual(data['runs'][0]['state'],'RESERVED');self.assertEqual(data['spent_seconds'],305)
   deadline=time.monotonic()+5
   while time.monotonic()<deadline:
    try:
     q=psutil.Process(worker_pid)
     if abs(q.create_time()-worker_created)>.001 or not q.is_running():break
    except psutil.NoSuchProcess:break
    time.sleep(.05)
   else:self.fail('owned dummy worker survived supervisor crash')
   _,r=self.invoke('ok');self.assertEqual(r['status'],'NOT_RUN_LEDGER_LOCK_HELD');self.assertFalse(r['worker_started'])
  finally:
   if proc.poll() is None:proc.kill();proc.wait(timeout=3)
   if proc.stdout:proc.stdout.close()
   if proc.stderr:proc.stderr.close()
class FreezeLogicWithoutWorkers(unittest.TestCase):
 """Filesystem fixtures and a low-memory gate only; no process is launched."""
 def setUp(self):
  self.d=TEST/('frozen_'+uuid.uuid4().hex);self.d.mkdir(parents=True)
  spec=importlib.util.spec_from_file_location('training_supervisor_logic',SUP);self.s=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.s)
  self.r=self.d/'fake_project';self.mod=self.r/'scripts/training';self.mod.mkdir(parents=True)
  self.train=self.r/'runtime/training';self.train.mkdir(parents=True)
  self.paths={}
  for name,src in {'__init__.py':'','supervisor.py':'pass\n','guard.py':'pass\n','worker.py':'from scripts.training import feature\n','feature.py':'from . import nested\n','nested.py':'V=1\n'}.items():
   self.paths[name]=self.mod/name;self.paths[name].write_text(src,encoding='utf8')
  self.inp=self.train/'input.json';self.inp.write_text('{"sample":1}',encoding='utf8')
  self.config=self.train/'config.json';self.config.write_text(json.dumps({'worker_sha256':sha(self.paths['worker.py']),'inputs':[{'path':self.inp.relative_to(self.r).as_posix(),'sha256':sha(self.inp)}]}))
  self.freeze=self.train/'FREEZE.json';self.record={
   'configs':[{'path':self.config.relative_to(self.r).as_posix(),'sha256':sha(self.config)}],
   'code':[{'path':p.relative_to(self.r).as_posix(),'sha256':sha(p)} for p in self.paths.values()]};self._save()
  self.patches=[patch.object(self.s,k,v) for k,v in {'ROOT':self.r,'TRAIN':self.train,'FREEZE':self.freeze,'WORKER':self.paths['worker.py'],'GUARD':self.paths['guard.py'],'__file__':str(self.paths['supervisor.py'])}.items()]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
 def _save(self):self.freeze.write_text(json.dumps(self.record),encoding='utf8')
 def test_complete_static_freeze_passes(self):self.assertEqual(self.s.frozen_inputs(self.config),sha(self.freeze))
 def test_transitive_project_code_must_be_frozen(self):
  self.record['code']=[x for x in self.record['code'] if not x['path'].endswith('nested.py')];self._save()
  with self.assertRaisesRegex(ValueError,'FREEZE_MISSING_STATIC_PROJECT_IMPORTS'):self.s.frozen_inputs(self.config)
 def test_input_tampering_detected(self):
  self.inp.write_text('{"sample":2}',encoding='utf8')
  with self.assertRaisesRegex(ValueError,'CONFIG_INPUT_HASH_MISMATCH'):self.s.frozen_inputs(self.config)
 def test_dynamic_import_rejected(self):
  self.paths['worker.py'].write_text("__import__('secrets')\n",encoding='utf8')
  self.config.write_text(json.dumps({'worker_sha256':sha(self.paths['worker.py']),'inputs':[{'path':self.inp.relative_to(self.r).as_posix(),'sha256':sha(self.inp)}]}))
  self.record['configs'][0]['sha256']=sha(self.config)
  next(x for x in self.record['code'] if x['path'].endswith('worker.py'))['sha256']=sha(self.paths['worker.py']);self._save()
  with self.assertRaisesRegex(ValueError,'DYNAMIC_IMPORT_NOT_ALLOWED'):self.s.frozen_inputs(self.config)
 def test_no_input_list_denied(self):
  self.config.write_text(json.dumps({'worker_sha256':sha(self.paths['worker.py'])}));self.record['configs'][0]['sha256']=sha(self.config);self._save()
  with self.assertRaisesRegex(ValueError,'CONFIG_INPUT_HASHES_REQUIRED'):self.s.frozen_inputs(self.config)
 def test_production_freeze_failure_has_receipt_no_worker(self):
  self.inp.write_text('{"sample":2}',encoding='utf8');receipt=self.train/'failure_receipt.json'
  with patch.object(self.s.subprocess,'Popen',side_effect=AssertionError('worker must not start')):
   result=self.s.run(self.config,self.train/'runs'/'new',receipt)
  self.assertEqual(result['status'],'NOT_RUN_FROZEN_INPUT_FAILURE');self.assertFalse(result['worker_started']);self.assertTrue(receipt.is_file())

class LowMemoryWithoutWorker(unittest.TestCase):
 def test_does_not_start_process(self):
  d=TEST/('low_memory_'+uuid.uuid4().hex);d.mkdir(parents=True);cfg=d/'config.json';cfg.write_text(json.dumps({'worker_sha256':sha(DUMMY)}))
  spec=importlib.util.spec_from_file_location('training_supervisor_memory',SUP);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
  with patch.object(s.subprocess,'Popen',side_effect=AssertionError('worker must not start')):
   r=s.run(cfg,d/'out',d/'receipt.json',self_test='low_memory')
  self.assertEqual(r['status'],'NOT_RUN_RESOURCE_PREFLIGHT');self.assertFalse(r['worker_started']);self.assertFalse((d/'out').exists())

class EnvironmentWithoutWorker(unittest.TestCase):
 def test_worker_resolves_full_model_dependencies_from_same_environment(self):
  spec=importlib.util.spec_from_file_location('training_supervisor_dependency_test',SUP);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
  try:runtime=s.worker_runtime_environment(False)
  except ValueError as exc:
   if str(exc).startswith('DEPENDENCY_NOT_IN_ACTIVE_SITE:'):self.skipTest(str(exc))
   raise
  script='import importlib.util,json; names='+repr(list(runtime['dependency_origins']))+'; print(json.dumps({n:importlib.util.find_spec(n).origin for n in names}))'
  environment=dict(os.environ,PYTHONPATH=os.pathsep.join(runtime['site_directories']),PYTHONNOUSERSITE='1')
  environment.pop('PYTHONHOME',None)
  p=subprocess.run([runtime['executable'],'-S','-c',script],env=environment,capture_output=True,text=True,timeout=15)
  self.assertEqual(p.returncode,0,p.stderr)
  child=json.loads(p.stdout)
  self.assertEqual({k:os.path.normcase(os.path.realpath(v)) for k,v in child.items()},
                   {k:os.path.normcase(os.path.realpath(v)) for k,v in runtime['dependency_origins'].items()})
 def test_dependency_preflight_failure_blocks_start(self):
  d=TEST/('bad_environment_'+uuid.uuid4().hex);d.mkdir(parents=True);cfg=d/'config.json';cfg.write_text(json.dumps({'worker_sha256':sha(DUMMY)}))
  spec=importlib.util.spec_from_file_location('training_supervisor_environment',SUP);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
  with patch.object(s.psutil,'virtual_memory',return_value=type('Memory',(),{'available':3*1024**3})()), patch.object(s,'worker_runtime_environment',side_effect=ValueError('missing dependency')), patch.object(s.subprocess,'Popen',side_effect=AssertionError('worker must not start')):
   r=s.run(cfg,d/'out',d/'receipt.json',self_test='ok')
  self.assertEqual(r['status'],'NOT_RUN_DEPENDENCY_PREFLIGHT');self.assertFalse(r['worker_started']);self.assertTrue((d/'receipt.json').exists())
 def test_worker_rejects_dependency_origin_mismatch(self):
  spec=importlib.util.spec_from_file_location('training_guard_environment',ROOT/'scripts/training/guard.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
  with self.assertRaisesRegex(ValueError,'dependency_origin_mismatch'):
   g.check_dependency_origins({'psutil':str(ROOT/'missing.py')})

class PackagedImportClosure(unittest.TestCase):
 def test_real_worker_closure_includes_modules_under_src(self):
  spec=importlib.util.spec_from_file_location('training_supervisor_closure',SUP)
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  actual=module.static_local_imports(ROOT/'scripts/training/worker.py')
  expected={ROOT/'scripts/training/guard.py',ROOT/'src/data_asset_poc/assessment/selection.py',
            ROOT/'src/data_asset_poc/assessment/value_methods.py',ROOT/'src/data_asset_poc/assessment/binding.py',
            ROOT/'src/data_asset_poc/__init__.py',ROOT/'src/data_asset_poc/assessment/__init__.py'}
  self.assertTrue(expected.issubset(actual), sorted(str(p) for p in expected-actual))

if __name__=='__main__':unittest.main(verbosity=2)
