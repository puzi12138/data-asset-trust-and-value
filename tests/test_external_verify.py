"""Tests use hand-constructed model artifacts; they do not fit any model."""
import json
import math
from pathlib import Path
import random
import statistics
import tempfile
import unittest

from scripts.external_experiments import verify as verifier
from scripts.experiments import verify_result as reference


def leaf(weights):
    return {'children_left':[-1], 'children_right':[-1], 'feature':[-2],
            'threshold':[-2.0], 'value':[[weights]]}


class ArithmeticTests(unittest.TestCase):
    def test_multiclass_softmax(self):
        model={'model':'LogisticRegression','preprocess_D0':{'median':[0.0],'scale':[1.0]},
               'classes':['A','B','C'],'coef':[[0.0],[0.0],[0.0]],
               'intercept':[0.0,math.log(2.0),math.log(3.0)]}
        label,p = verifier.model_predict(model,{'x':[12.0]})
        self.assertEqual(label,'C')
        for a,b in zip(p,[1/6,2/6,3/6]):self.assertAlmostEqual(a,b)

    def test_binary_sigmoid_and_clip(self):
        model={'model':'LogisticRegression','preprocess_D0':{'median':[0.0],'scale':[1.0]},
               'classes':['A','B'],'coef':[[0.0]],'intercept':[1000.0]}
        label,p=verifier.model_predict(model,{'x':[0.0]})
        self.assertEqual(label,'B');self.assertGreater(p[0],0);self.assertAlmostEqual(sum(p),1)

    def test_forest_averages_leaf_distributions(self):
        model={'model':'RandomForestClassifier','preprocess_D0':{'median':[0.0],'scale':[1.0]},
               'classes':['A','B','C'],'trees':[leaf([4,1,0]),leaf([0,1,1])]}
        label,p=verifier.model_predict(model,{'x':[0]})
        self.assertEqual(label,'A')
        for a,b in zip(p,[.4,.35,.25]):self.assertAlmostEqual(a,b)

    def test_sklearn_tree_compares_float32(self):
        t={'children_left':[1,-1,-1],'children_right':[2,-1,-1],'feature':[0,-2,-2],
           'threshold':[1.00000001,-2,-2], 'value':[[[1,1]],[[4,0]],[[0,4]]]}
        self.assertEqual(verifier._tree_probs(t,[1+2**-25],2),[1.0,0.0])

    def test_tree_cycle_is_rejected(self):
        t={'children_left':[0],'children_right':[0],'feature':[0], 'threshold':[1.0], 'value':[[[1,1]]]}
        with self.assertRaises(reference.InvalidResult):verifier._tree_probs(t,[0],2)

    def test_negative_probability_rejected(self):
        with self.assertRaises(reference.InvalidResult):verifier._safe_probability([1,-0.001])

    def test_distance_uses_published_order_at_ulp_boundary(self):
        x=[math.sqrt(v) for v in [6035.360357946183,.8246855972165168,182911.25097857325,1033.636773449006,.21891905131684236,19173.693518591786,7.562252457365337e-07,.00013808669300913745,483.91597186643673,8.41212717726361,5577.025433764007,.0008452570124464157,2903.9062444329047,45.102889975510166,1.9495697375276504e-06,.0033732396883457434]]
        terms=[v**2 for v in x];naive=0.0
        for term in terms:naive+=term
        naive/=16
        self.assertNotEqual(naive,statistics.fmean(terms))
        self.assertEqual(verifier._protocol_squared_distance(x,{'median':[0.0]*16,'scale':[1.0]*16}),naive)

    def test_prepared_matches_naive_26class_models_exactly(self):
        rng=random.Random(8712);classes=[chr(i) for i in range(65,91)]
        stats={'median':[rng.uniform(-2,2) for _ in range(16)],'scale':[rng.uniform(.2,3) for _ in range(16)]}
        logistic={'model':'LogisticRegression','preprocess_D0':stats,'classes':classes,
                  'coef':[[rng.uniform(-3,3) for _ in range(16)] for _ in classes],
                  'intercept':[rng.uniform(-2,2) for _ in classes]}
        trees=[]
        for t in range(19):
            a=[rng.randint(0,8) for _ in classes];b=[rng.randint(0,8) for _ in classes]
            trees.append({'children_left':[1,-1,-1],'children_right':[2,-1,-1],
                          'feature':[t%16,-2,-2],'threshold':[rng.uniform(-1,1),-2,-2],
                          'value':[[a],[a],[b]]})
        forest={'model':'RandomForestClassifier','preprocess_D0':stats,'classes':classes,'trees':trees}
        rows=[{'x':[rng.uniform(-3,3) if rng.random()>.1 else None for _ in range(16)]} for _ in range(70)]
        for model in (logistic,forest):
            prepared=verifier._make_predictor(model)
            for row in rows:self.assertEqual(prepared(row),verifier.model_predict(model,row))
            # A second pass checks that cached leaves do not change the result.
            for row in rows:self.assertEqual(prepared(row),verifier.model_predict(model,row))

    def test_prepared_rejects_invalid_traversed_tree(self):
        t={'children_left':[0],'children_right':[0],'feature':[0],'threshold':[1.0],'value':[[[1,1]]]}
        model={'model':'RandomForestClassifier','preprocess_D0':{'median':[0.0],'scale':[1.0]},'classes':['A','B'],'trees':[t]}
        with self.assertRaises(reference.InvalidResult):verifier._make_predictor(model)({'x':[0]})

    def test_group_leakage_rejected(self):
        rows=[{'id':'a','group':'g'},{'id':'b','group':'h'}]
        data={'rows':rows,'roles':{'D0':[0],'pool':[1]}}
        with self.assertRaises(reference.InvalidResult):
            verifier._separate(data,[{'id':'c','group':'g'}],[{'id':'d','group':'i'}])


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='external-verifier-');cls.root=Path(cls.temp.name)
        cls.data=cls.root/'inputs';cls.data.mkdir();cls.out=cls.root/'result';cls.out.mkdir()
        def dump(path,obj):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(obj,ensure_ascii=False,allow_nan=False),encoding='utf8')
        cls.dump=staticmethod(dump)
        def row(i):return {'id':str(i),'source_row':i,'group':str(i),'x':[float(i%3)]+[0.0]*15,'y':'A' if i%2==0 else 'B'}
        rows=[row(i) for i in range(514)];d0=[0,1];pool=list(range(2,514));dev=[row(1000),row(1001)];test=[row(2000),row(2001)]
        raw_sha='b'*64;seed=1103
        data={'rows':rows,'roles':{'D0':d0,'pool':pool},'task':{'type':'classification','feature_count':16,'allowed_labels':['A','B']},'asset_metadata':{'dataset':'Letter Recognition','source_sha256':raw_sha}}
        refs=[{'index':i,'id':rows[i]['id'],'x':rows[i]['x'],'y':rows[i]['y']} for i in random.Random(seed+160000).sample(pool,256)]
        members={'source_sha256':raw_sha,'seed':seed,'roles':{role:[{k:r[k] for k in ('source_row','id','group')} for r in rs] for role,rs in [('D0',[rows[i] for i in d0]),('pool',[rows[i] for i in pool]),('dev',dev),('test',test)]}}
        contents={'selection_input.json':data,'audit_reference.json':{'records':refs},'development_input.json':{'records':dev},'test_input.json':{'records':test},'injection_truth.json':{'records':[]},'MEMBERS.json':members,'source_record.json':{'raw_sha256':raw_sha}}
        for n,obj in contents.items():dump(cls.data/n,obj)
        cfg={'schema':'external-experiment-v1','task_id':'EXTERNAL-SMOKE','scope':'SYNTHETIC_SMOKE_NON_SCIENTIFIC','feature_count':16,'mutation_feature_index':0,'dataset':'Letter Recognition','task_type':'classification','seed':seed,'budgets':[128,512],'models':['logistic','random_forest']}
        for key in ('selection_input','audit_reference','development_input','test_input','injection_truth'):cfg[key]=key+'.json'
        cfg['inputs']=[{'path':n,'sha256':verifier.digest(cls.data/n)} for n in contents];dump(cls.data/'config.json',cfg)
        cls.config=cls.data/'config.json'
        features,states,stats=reference.computed_features(data,refs)
        dump(cls.out/'AUDIT.json',{'sample':[{'index':r['index'],'id':r['id'],'state':states[r['id']]} for r in refs]})
        dump(cls.out/'FEATURES.json',{'feature_names':list(reference.FEATURES),'features':features,'stats_from_D0_only':stats})
        scores={r['id']:.5 for r in features}
        rng=random.Random(seed+180000); n=min(len(d0),round(len(d0)*256/len(pool)))
        samples={kind:rng.sample(range(len(d0)),n) for kind in ('source','label_changed','feature_changed')}
        dump(cls.out/'RF_MODEL.json',{'params':{'n_estimators':32,'max_depth':3,'random_state':seed,'n_jobs':1},'classes':[0,1],'calibration_rows':6,'calibration_sample_indices':samples,'calibration_mutation_feature_upper':2.5,'trees':[leaf([1,1]) for _ in range(32)],'scores':scores})
        selections={}
        for k in (128,512):
            chosen=reference.choices(features,k,seed,scores,None)
            for method in verifier.METHODS:
                inds=chosen[method];selections[f'k{k}__{method}']={'indices':inds,'ids':[rows[i]['id'] for i in inds]}
        freeze={'test_labels_parsed_before_freeze':False,'development_labels_parsed_before_freeze':False,'injection_truth_parsed_before_freeze':False,'config_sha256':verifier.digest(cls.config),'frozen_monotonic':1.0,'methods':selections}
        dump(cls.out/'SELECTION_FREEZE.json',freeze);selection_sha=verifier.digest(cls.out/'SELECTION_FREEZE.json')
        dump(cls.out/'HELDOUT_ACCESS.json',{'selection_sha256':selection_sha,'heldout_read_started_monotonic':1.5})
        summaries=[]
        for model_name in verifier.MODELS:
            for method,k in [('D0',0)]+[(m,k) for k in (128,512) for m in verifier.METHODS]:
                arm=model_name+'__'+('D0' if k==0 else f'k{k}__{method}');folder=cls.out/arm;folder.mkdir()
                chosen=[] if not k else selections[f'k{k}__{method}']['indices'];indices=d0+chosen
                dump(folder/'TRAIN_MEMBERS.json',{'indices':indices,'members':[{key:rows[i][key] for key in ('id','source_row','group')} for i in indices]})
                if model_name=='logistic':
                    model={'model':'LogisticRegression','params':{'C':1.0,'solver':'lbfgs','max_iter':500,'tol':1e-5,'random_state':seed,'fit_intercept':True},'classes':['A','B'],'preprocess_D0':stats,'coef':[[0.0]*16],'intercept':[0.0]}
                else:
                    model={'model':'RandomForestClassifier','params':{'n_estimators':100,'max_depth':12,'random_state':seed,'n_jobs':1},'classes':['A','B'],'preprocess_D0':stats,'trees':[leaf([1,1]) for _ in range(100)]}
                dump(folder/'MODEL.json',model)
                met={'arm':arm,'model':model_name,'method':method,'k':k,'status':'COMPLETE','n_train':len(indices),'n_added':k,'selection_sha256':selection_sha,'fit_started_monotonic':2.0}
                for split,records in [('dev',dev),('test',test)]:
                    preds=[{'id':r['id'],'group':r['group'],'y':r['y'],'prediction':'A','probabilities':[.5,.5],'classes':['A','B'],'loss':-math.log(.5)} for r in records]
                    dump(folder/(split+'_PREDICTIONS.json'),preds)
                    met[split]={'metric':'LogLoss','loss':-math.log(.5),'accuracy':.5,'macro_f1':1/3,'n':2,'evaluation_started_monotonic':3.0}
                dump(folder/'METRICS.json',met);summaries.append(met)
        dump(cls.out/'SUMMARY.json',{'status':'COMPLETE','task_id':'EXTERNAL-SMOKE','dataset':'Letter Recognition','seed':seed,'methods':summaries,'selection_sha256':selection_sha,'target_fit_attempts':18,'detector_fit_attempts':1})

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_all_18_arms_and_72_predictions_replay(self):
        r=verifier.verify(self.out,self.config,self.data)
        self.assertEqual(r['status'],'REPRODUCED',r.get('reason'));self.assertEqual(r['arms'],18);self.assertEqual(r['prediction_rows'],72)

    def test_tampered_prediction_is_rejected(self):
        p=self.out/'logistic__D0/test_PREDICTIONS.json';original=p.read_bytes()
        try:
            obj=json.loads(original);obj[0]['probabilities']=[0.6,0.4];self.dump(p,obj)
            r=verifier.verify(self.out,self.config,self.data);self.assertEqual(r['status'],'REJECTED')
        finally:p.write_bytes(original)

    def test_changed_input_is_rejected_before_replay(self):
        p=self.data/'source_record.json';original=p.read_bytes()
        try:
            self.dump(p,{'raw_sha256':'a'*64})
            r=verifier.verify(self.out,self.config,self.data);self.assertEqual(r['status'],'REJECTED');self.assertIn('Input SHA mismatch',r['reason'])
        finally:p.write_bytes(original)

    def test_nonfrozen_selection_rejected(self):
        p=self.out/'SELECTION_FREEZE.json';original=p.read_bytes()
        try:
            obj=json.loads(original);obj['test_labels_parsed_before_freeze']=True;self.dump(p,obj)
            r=verifier.verify(self.out,self.config,self.data);self.assertEqual(r['status'],'REJECTED');self.assertIn('Config/freeze binding',r['reason'])
        finally:p.write_bytes(original)


if __name__=='__main__':unittest.main()
