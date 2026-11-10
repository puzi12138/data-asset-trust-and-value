"""Adversarial tests: no model fitting and no access to real training data."""
from pathlib import Path
import importlib.util
import json
import math
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('verify_community',ROOT/'scripts/experiments/verify_result.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.result=self.root/'result'
        self.repo.mkdir();self.result.mkdir()
        cfg={'task_id':'EXP-01','dataset':'fixture','seed':1103,'k':1,'task_type':'classification',
             'target_model':'LogisticRegression','primary_metric':'LogLoss','expected_methods':['random'],'inputs':[]}
        config=self.repo/'experiments/community/configs/EXP-01/1103.json';config.parent.mkdir(parents=True);config.write_text(json.dumps(cfg),encoding='utf8')
        for n in v.CANONICAL_CODE+v.CANONICAL_INFRA:
            p=self.repo/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture '+n,encoding='utf8')
        self.manifest={'schema_version':'1.0','run_id':'run-1','task_id':'EXP-01','seed':1103,'status':'COMPLETE','git_commit':'a'*40,
                       'config_sha256':v.digest(config),'worker_sha256':v.digest(self.repo/v.CANONICAL_CODE[0]),
                       'code':[{'path':n,'sha256':v.digest(self.repo/n)} for n in v.CANONICAL_CODE],
                       'infrastructure':[{'path':n,'sha256':v.digest(self.repo/n)} for n in v.CANONICAL_INFRA],
                       'inputs':[],'target_model':'LogisticRegression','primary_metric':'LogLoss','expected_methods':['random'],'files':[]}
        cost={'wall_seconds':1.,'cpu_seconds':.2,'peak_rss_bytes':1024,'worker_limit_seconds':5,'max_workers':1,'device':'cpu',
              'gpu_used':False,'sample_budget_per_arm':1,'token_budget':'NOT_APPLICABLE_TABULAR',
              'worker_monitor':{'status':'COMPLETE','worker_started':True,'training_go_sent':True}}
        env={'python_version':'3.10','os':'test','cpu_model':'test','system_memory_total_bytes':1024,'packages':{},'gpu':{}}
        for name in v.REQUIRED-{'RUN_MANIFEST.json'}:
            p=self.result/name;p.parent.mkdir(parents=True,exist_ok=True)
            val=cost if name=='COST.json' else env if name=='ENVIRONMENT.json' else {}
            p.write_text(json.dumps(val),encoding='utf8')
        self.refinalize()
    def tearDown(self):self.tmp.cleanup()
    def refinalize(self):
        self.manifest['files']=[{'path':p.relative_to(self.result).as_posix(),'size_bytes':p.stat().st_size,'sha256':v.digest(p)}
                                for p in sorted(self.result.rglob('*')) if p.is_file() and p.name not in ('RUN_MANIFEST.json','VERIFY_REPORT.json')]
        (self.result/'RUN_MANIFEST.json').write_text(json.dumps(self.manifest),encoding='utf8')
    def test_missing_trusted_data_needs_review(self):
        result=v.verify(self.result,None,self.repo)
        self.assertEqual(result['schema_status'],'SCHEMA_VALID');self.assertEqual(result['status'],'NEEDS_REVIEW')
    def test_unknown_task_rejected(self):
        self.manifest['task_id']='EXP-99';self.refinalize()
        self.assertEqual(v.verify(self.result,None,self.repo)['status'],'REJECTED')
    def test_code_tamper_rejected(self):
        self.manifest['code'][1]['sha256']='0'*64;self.refinalize()
        self.assertIn('Implementation hash mismatch',v.verify(self.result,None,self.repo)['reason'])
    def test_result_hash_tamper_rejected(self):
        (self.result/'METRICS.json').write_text('{"forged":1}',encoding='utf8')
        self.assertIn('hash or size',v.verify(self.result,None,self.repo)['reason'])
    def test_extra_unlisted_file_rejected(self):
        (self.result/'extra.txt').write_text('unlisted',encoding='utf8')
        self.assertIn('unlisted',v.verify(self.result,None,self.repo)['reason'])
    def test_path_traversal_rejected(self):
        for name in ('../outside','C:/secret','a/../../outside','a\\b','/etc/passwd'):
            with self.subTest(name=name),self.assertRaises((ValueError,v.InvalidResult)):
                v.path_in(self.result,name)
    def test_duplicate_json_key_rejected(self):
        p=self.root/'x.json';p.write_text('{"a":1,"a":2}',encoding='utf8')
        with self.assertRaises(v.InvalidResult):v.read_json(p)
    def test_nonfinite_json_rejected(self):
        p=self.root/'x.json';p.write_text('{"x":NaN}',encoding='utf8')
        with self.assertRaises(v.InvalidResult):v.read_json(p)
    def test_group_leakage_rejected_even_with_distinct_ids(self):
        row=lambda name,group:{'id':name,'group':group,'x':[1.],'y':'0'}
        data={'rows':[row('a','A'),row('b','B')],'roles':{'D0':[0],'pool':[1]}}
        with self.assertRaisesRegex(v.InvalidResult,'leakage'):v.check_overlap(data,[row('c','A')],[row('d','D')])
    def test_known_linear_outputs(self):
        model={'model':'LogisticRegression','preprocess_D0':{'median':[0.],'scale':[2.]},'coef':[[2.]],'intercept':[0.],'classes':['0','1']}
        pred,probs=v.model_predict(model,{'x':[1.]},'LogisticRegression');self.assertEqual(pred,'1');self.assertAlmostEqual(probs[1],1/(1+math.exp(-1)))
        model={'model':'Ridge','preprocess_D0':{'median':[0.],'scale':[2.]},'coef':[2.],'intercept':1.,'classes':None}
        self.assertEqual(v.model_predict(model,{'x':[1.]},'Ridge'),(2.,None))
    def test_macro_f1_and_tied_auc(self):
        self.assertAlmostEqual(v.macro_f1(['0','0','1'],['0','1','1'],['0','1']),(2/3+2/3)/2)
        self.assertAlmostEqual(v.auc_binary(['0','1','0','1'],[.1,.8,.8,.8],'1'),.75)
    def test_smoke_is_explicitly_marked(self):
        self.manifest['task_id']='SMOKE-01';self.refinalize()
        self.assertEqual(v.verify(self.result,None,self.repo)['status'],'REJECTED')
    def test_nonfinite_float_rejected(self):
        with self.assertRaises(v.InvalidResult):v.close(float('nan'),0.)

if __name__=='__main__':unittest.main()
