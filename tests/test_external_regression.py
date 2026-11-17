"""Regression contract and saved arithmetic tests; never fit a model."""
import copy,json,math,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from scripts.external_experiments import common,regression_contract as contract,regression_worker as worker,regression_verify as verifier
from data_asset_poc.assessment.selection import group_cap
from scripts.experiments.verify_result import InvalidResult

class RegressionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name)/'smoke'
  subprocess.run([sys.executable,'-B',str(ROOT/'scripts/external_experiments/prepare_regression_smoke.py'),'--out',str(cls.folder)],check=True,capture_output=True,text=True)
  def read(name):return json.loads((cls.folder/name).read_text('utf8'))
  cls.cfg=read('config.json');cls.data=read('selection_input.json');cls.refs=read('audit_reference.json')['records'];cls.dev=read('development_input.json')['records'];cls.test=read('test_input.json')['records'];cls.members=read('MEMBERS.json');cls.source=read('source_record.json')
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def test_smoke_config_and_18_arms(self):
  self.assertTrue(common.validate_config(self.cfg,self.folder));self.assertEqual(len(common.planned_arms(self.cfg)),18)
  self.assertEqual(set(common.planned_arms(self.cfg)),{worker._arm_name(*x) for x in worker._plan()})
 def test_schema_dimension_and_feature_mutation_bound(self):
  for key,value in [('feature_count',11),('mutation_feature_index',0),('models',['logistic','random_forest']),('scope','UNDECLARED')]:
   with self.subTest(key=key),self.assertRaises(ValueError):common.validate_config({**self.cfg,key:value},self.folder)
 def test_roles_and_reference(self):
  rows,d0,pool,task,evidence,audit=contract.validate_selection(self.data,self.refs,self.cfg)
  self.assertEqual(len(evidence),256);self.assertEqual(len(audit),256);self.assertEqual(len(d0),456);self.assertEqual(len(pool),936)
  self.assertTrue(any(v=='CONFLICT' for v in evidence.values()))
  contract.validate_roles(self.data,self.dev,self.test,self.cfg,self.members)
 def test_source_binding_rejects_wrong_identity(self):
  contract.validate_source_binding(self.data,self.members,self.source,self.cfg)
  with self.assertRaisesRegex(ValueError,'SOURCE'):contract.validate_source_binding(self.data,self.members,{**self.source,'raw_sha256':'0'*64},self.cfg)
 def test_feature_leakage_and_order_rejected(self):
  d=copy.deepcopy(self.data);d['task']['feature_names'][8]='casual'
  with self.assertRaisesRegex(ValueError,'ALLOWLIST'):contract.validate_selection(d,self.refs,self.cfg)
 def test_duplicate_hour_rejected(self):
  rows=copy.deepcopy(self.data['rows'][:2]);rows[1]=copy.deepcopy(rows[0]);rows[1]['id']='distinct'
  with self.assertRaisesRegex(ValueError,'IDENTITY'):contract.validate_bike_rows(rows)
 def test_group_day_leakage_rejected(self):
  dev=copy.deepcopy(self.dev);dev[0]['group']=self.data['rows'][0]['group']
  members=copy.deepcopy(self.members);members['roles']['dev'][0]['group']=dev[0]['group']
  with self.assertRaisesRegex(ValueError,'LEAKAGE'):contract.validate_roles(self.data,dev,self.test,self.cfg,members)
 def test_date_partition_error_rejected(self):
  m=copy.deepcopy(self.members);m['roles']['buffer'][0]['group']='1900-01-01'
  with self.assertRaisesRegex(ValueError,'PARTITION'):contract.validate_roles(self.data,self.dev,self.test,self.cfg,m)
 def test_round_robin_allows_same_day_next_round(self):
  rows=[{'id':'A08','group':'A'},{'id':'A09','group':'A'},{'id':'B08','group':'B'}]
  self.assertEqual([r['id'] for r in group_cap(rows,3)],['A08','B08','A09'])
 def test_worker_refuses_no_go(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'no-output';p=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/external_experiments/regression_worker.py'),'--config','absent','--data-dir',tmp,'--out',str(out)],input='',capture_output=True,text=True,timeout=15)
   self.assertNotEqual(p.returncode,0);self.assertFalse(out.exists())
 def test_ridge_arithmetic(self):
  model={'model':'Ridge','params':{'alpha':1.,'fit_intercept':True},'preprocess_D0':{'median':[1.,3.],'scale':[2.,1.]},'coef':[2.,3.],'intercept':4.}
  f=verifier.make_predictor(model,'ridge',1103);self.assertAlmostEqual(f({'x':[3.,5.]}),12.)
 def test_forest_arithmetic_and_bad_tree(self):
  leaf={'children_left':[-1],'children_right':[-1],'feature':[-2],'threshold':[-2.],'value':[[[4.]]]} 
  model={'model':'RandomForestRegressor','params':{'n_estimators':100,'max_depth':12,'random_state':1103,'n_jobs':1},'preprocess_D0':{'median':[0.],'scale':[1.]},'trees':[leaf]*100}
  self.assertEqual(verifier.make_predictor(model,'random_forest',1103)({'x':[1.]}),4.)
  bad=copy.deepcopy(model);bad['trees'][0]['children_right'][0]=0
  with self.assertRaises(InvalidResult):verifier.make_predictor(bad,'random_forest',1103)({'x':[1.]})
 def test_metrics_and_constant_r2(self):
  m=verifier.metrics([1.,3.],[2.,1.]);self.assertEqual(m['loss'],1.5);self.assertAlmostEqual(m['rmse'],math.sqrt(2.5));self.assertEqual(m['r2'],-1.5)
  self.assertEqual(verifier.metrics([2.,2.],[2.,2.])['r2'],1.)
  self.assertEqual(verifier.metrics([2.,2.],[3.,3.])['r2'],0.)
 def test_reordered_reference_rejected(self):
  with self.assertRaisesRegex(ValueError,'REFERENCE|SAMPLE'):contract.validate_selection(self.data,list(reversed(self.refs)),self.cfg)

if __name__=='__main__':unittest.main()
