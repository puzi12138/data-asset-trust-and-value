"""Frozen dataset tabular study worker. Launched only by the dataset supervisor."""
from pathlib import Path
import argparse,hashlib,json,sys,time
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT), str(ROOT/'src')]
from scripts.training.guard import authorize

def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def write(p,v):
 with Path(p).open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False)
def clean(r):return {k:r[k] for k in ('id','source_row','group','x','y')}

def run(args):
 auth=authorize(args.config,args.out)
 # No ML import, data read, output creation, or model work before GO.
 import copy,math,random,statistics,warnings
 import numpy as np,psutil,sklearn
 from sklearn.ensemble import RandomForestClassifier
 from sklearn.linear_model import LogisticRegression,Ridge
 from sklearn.metrics import accuracy_score,f1_score,mean_absolute_error,mean_squared_error,r2_score
 from threadpoolctl import threadpool_limits,threadpool_info
 from data_asset_poc.assessment import selection as sel
 from data_asset_poc.assessment.value_methods import data_shapley_ridge,transform
 from data_asset_poc.assessment.binding import asset_digest
 cfg=read(args.config);timing={};t=time.perf_counter()
 # Hash scanning is byte-level integrity checking; heldout labels are not parsed.
 for item in cfg['inputs']:
  if sha(ROOT/item['path'])!=item['sha256']:raise ValueError('INPUT_HASH_MISMATCH:'+item['path'])
 timing['input_hash_scan_all_seconds']=time.perf_counter()-t
 def timed_read(key,path):
  t=time.perf_counter();d=read(ROOT/path);timing[key]=time.perf_counter()-t;return d
 data=timed_read('selection_read_seconds',cfg['selection_input']);dev=timed_read('development_read_seconds',cfg['development_input'])['records']
 refs=timed_read('audit_reference_read_seconds',cfg['audit_reference'])['records']
 rows=data['rows'];roles=data['roles'];d0=list(roles['D0']);pool=list(roles['pool']);task=data['task'];meta=data['asset_metadata']
 assert len(set(d0))==len(d0) and len(set(pool))==len(pool) and not set(d0)&set(pool)
 assert all(rows[i] is not None for i in d0+pool)
 assert not {rows[i]['group'] for i in d0}&{rows[i]['group'] for i in pool}
 if task['type']=='classification':
  declared=set(task['allowed_labels']);observed={rows[i]['y'] for i in d0}
  if observed!=declared:raise ValueError('D0_CLASSES_NOT_EQUAL_DECLARED_LABELS')
 t=time.perf_counter();evidence={};audit_rows=[]
 for r in refs:
  i=r['index'];assert i in set(pool) and rows[i]['id']==r['id']
  same=rows[i]['x']==r['x'] and rows[i]['y']==r['y'];evidence[r['id']]='VERIFIED' if same else 'CONFLICT'
  audit_rows.append({'index':i,'id':r['id'],'state':evidence[r['id']],'scope':'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'})
 timing['evidence_comparison_seconds']=time.perf_counter()-t
 t=time.perf_counter();stats=sel.fit_d0_statistics(rows,d0);pool_features=sel.features(rows,pool,stats,evidence,task)
 timing['quality_feature_extraction_seconds']=time.perf_counter()-t
 t=time.perf_counter()
 # Supervision is generated from D0 version perturbations, independently of pool truth.
 # Calculate group features in each complete D0 copy, avoiding 3x group inflation.
 base=[clean(rows[i]) for i in d0];labels=task.get('allowed_labels');ys=[float(r['y']) for r in base] if task['type']=='regression' else []
 delta=2*(statistics.pstdev(ys) or 1.) if ys else None
 vals=[float(r['x'][0]) for r in base if sel.finite(r['x'][0])];upper=max(vals)+(3*(statistics.pstdev(vals) or 1.))
 cal_x=[];cal_y=[];n_audit=min(64,len(pool));rng=random.Random(cfg['seed']+180000)
 for kind in ['source','label_changed','feature_changed']:
  dataset=copy.deepcopy(base);ev={};audited=set(rng.sample(range(len(dataset)),min(len(dataset),round(len(dataset)*n_audit/len(pool)))))
  for j,r in enumerate(dataset):
   if kind=='label_changed':r['y']=(labels[(labels.index(r['y'])+1)%len(labels)] if labels else float(r['y'])+delta)
   if kind=='feature_changed':r['x'][0]=upper
   if j in audited:ev[r['id']]='VERIFIED' if kind=='source' else 'CONFLICT'
  for f in sel.features(dataset,list(range(len(dataset))),stats,ev,task):cal_x.append([f[n] for n in sel.FEATURE_NAMES]);cal_y.append(int(kind=='source'))
 timing['rf_calibration_preparation_seconds']=time.perf_counter()-t
 t=time.perf_counter()
 with threadpool_limits(limits=2):
  detector=RandomForestClassifier(n_estimators=32,max_depth=3,random_state=cfg['seed'],n_jobs=1);detector.fit(cal_x,cal_y)
  probs=detector.predict_proba([[f[n] for n in sel.FEATURE_NAMES] for f in pool_features])[:,list(detector.classes_).index(1)]
 timing['rf_fit_predict_seconds']=time.perf_counter()-t
 scores={r['id']:float(v) for r,v in zip(pool_features,probs)}
 trees=[{'children_left':e.tree_.children_left.tolist(),'children_right':e.tree_.children_right.tolist(),'feature':e.tree_.feature.tolist(),'threshold':e.tree_.threshold.tolist(),'value':e.tree_.value.tolist()} for e in detector.estimators_]
 t=time.perf_counter();shapley=None;shapley_map=None
 if task['type']=='regression':
  with threadpool_limits(limits=2):shapley=data_shapley_ridge([rows[i] for i in d0],[rows[i] for i in pool],dev,stats,cfg['seed']+190000,permutations=8,alpha=1.)
  shapley_map={rows[i]['id']:v for i,v in zip(pool,shapley['scores'])}
 timing['shapley_all_compute_seconds']=time.perf_counter()-t if shapley else 0.
 out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
 write(out/'AUTH.json',auth);write(out/'AUDIT.json',{'scope':'same-source version compliance only','sample':audit_rows})
 write(out/'FEATURES.json',{'stats_from_D0_only':stats,'features':pool_features,'feature_names':sel.FEATURE_NAMES})
 write(out/'RF_MODEL.json',{'scope':'D0 controlled source-version modification detector; not a data-value model','calibration_rows':len(cal_y),'trees':trees,'params':detector.get_params(),'scores':scores})
 if shapley:write(out/'DATA_SHAPLEY.json',shapley)
 t=time.perf_counter();selection_timings={};selections=sel.select_all(pool_features,cfg['k'],cfg['seed'],scores,shapley_map,timing=selection_timings)
 timing['all_selection_strategies_seconds']=time.perf_counter()-t
 timing['selection_seconds_by_method']=selection_timings
 assert all(len(v)==cfg['k'] and set(v)<=set(pool) for v in selections.values())
 # Every target-method selection is frozen before the test labels are parsed.
 t=time.perf_counter();selection_payload={'version':'dataset-selections-v1','frozen_unix_time':time.time(),'frozen_monotonic':time.monotonic(),'methods':{k:{'indices':v,'ids':[rows[i]['id'] for i in v],'asset_digest':asset_digest({**meta,'records':[clean(rows[i]) for i in v]})} for k,v in selections.items()},'test_labels_parsed_before_freeze':False,'input_selection_sha256':sha(ROOT/cfg['selection_input'])}
 selection_path=out/'SELECTION_FREEZE.json';write(selection_path,selection_payload);selection_hash=sha(selection_path);timing['selection_freeze_write_seconds']=time.perf_counter()-t
 test=timed_read('test_read_after_selection_seconds',cfg['test_input'])['records']
 assert not {rows[i]['group'] for i in d0+pool}&{r['group'] for r in test}
 assert not {r['group'] for r in dev}&{r['group'] for r in test}
 t=time.perf_counter();xdev=transform(dev,stats);xtest=transform(test,stats);timing['development_and_test_preprocess_seconds']=time.perf_counter()-t
 arms=[('D0',[])]+list(selections.items());results=[]
 for method,chosen in arms:
  t=time.perf_counter();members=d0+chosen;train=[rows[i] for i in members];trainx=transform(train,stats);trainy=[r['y'] for r in train];input_preprocess=time.perf_counter()-t
  model=LogisticRegression(C=1.,solver='lbfgs',max_iter=500,tol=1e-5,random_state=cfg['seed']) if task['type']=='classification' else Ridge(alpha=1.)
  directory=out/method;directory.mkdir();start=time.perf_counter();start_wall=time.time();start_mon=time.monotonic();cp0=psutil.Process().cpu_times()
  with warnings.catch_warnings(record=True) as ws,threadpool_limits(limits=2):
   warnings.simplefilter('always');model.fit(trainx,trainy)
  fit=time.perf_counter()-start;cp1=psutil.Process().cpu_times()
  met={'method':method,'n_train':len(train),'n_added':len(chosen),'training_data_preprocess_seconds':input_preprocess,'fit_seconds':fit,'fit_started_unix':start_wall,'fit_started_monotonic':start_mon,'cpu_seconds':cp1.user+cp1.system-cp0.user-cp0.system,'warnings':[str(w.message) for w in ws],'selection_sha256':selection_hash}
  write(directory/'TRAIN_MEMBERS.json',{'indices':members,'members':[{'id':rows[i]['id'],'source_row':rows[i]['source_row'],'group':rows[i]['group']} for i in members],'asset_digest':asset_digest({**meta,'records':[clean(rows[i]) for i in members]})})
  write(directory/'MODEL.json',{'model':type(model).__name__,'params':model.get_params(),'preprocess_D0':stats,'coef':np.asarray(model.coef_).tolist(),'intercept':np.asarray(model.intercept_).tolist(),'classes':model.classes_.tolist() if hasattr(model,'classes_') else None,'n_iter':np.asarray(model.n_iter_).tolist() if getattr(model,'n_iter_',None) is not None else None})
  for split,erows,ex in [('dev',dev,xdev),('test',test,xtest)]:
   start=time.perf_counter();ey=[r['y'] for r in erows]
   with threadpool_limits(limits=2):pred=model.predict(ex);probs=model.predict_proba(ex) if task['type']=='classification' else None
   if probs is not None:
    classes=model.classes_.tolist();positions={v:i for i,v in enumerate(classes)}
    if not set(ey)<=set(classes):raise ValueError('EVALUATION_CLASS_NOT_IN_TRAIN_CLASSES')
    eps=np.finfo(probs.dtype).eps;losses=[-math.log(max(eps,min(1.-eps,float(probs[j,positions[y]])))) for j,y in enumerate(ey)]
    detail={'metric':'LogLoss','loss':statistics.fmean(losses),'accuracy':float(accuracy_score(ey,pred)),'macro_f1':float(f1_score(ey,pred,labels=classes,average='macro',zero_division=0))}
    predicted=[{'id':r['id'],'group':r['group'],'y':ey[j],'prediction':str(pred[j]),'probabilities':[float(x) for x in probs[j]],'classes':classes,'loss':losses[j]} for j,r in enumerate(erows)]
   else:
    yp=np.asarray(pred,dtype=float);yt=np.asarray(ey,dtype=float);losses=np.abs(yt-yp)
    detail={'metric':'MAE','loss':float(mean_absolute_error(yt,yp)),'rmse':float(np.sqrt(mean_squared_error(yt,yp))),'r2':float(r2_score(yt,yp))}
    predicted=[{'id':r['id'],'group':r['group'],'y':float(ey[j]),'prediction':float(pred[j]),'loss':float(losses[j])} for j,r in enumerate(erows)]
   detail.update(n=len(erows),evaluation_seconds=time.perf_counter()-start);met[split]=detail;write(directory/f'{split}_PREDICTIONS.json',predicted)
  write(directory/'METRICS.json',met);results.append(met)
  print(json.dumps({'arm':method,'status':'COMPLETE','fit_seconds':fit}),flush=True)
 # Only after all selections/fits/evaluations open planted-change labels.
 planted=timed_read('posthoc_injection_truth_read_seconds',cfg['injection_truth'])
 truth={r['index']:r['kind'] for r in planted['records']};quality=[];fmap={f['index']:f for f in pool_features}
 for method,chosen in selections.items():
  quality.append({'method':method,'controlled_source_modifications':sum(i in truth for i in chosen),'label_modifications':sum(truth.get(i)=='label' for i in chosen),'feature_modifications':sum(truth.get(i)=='feature' for i in chosen),'n':len(chosen),'groups':len({rows[i]['group'] for i in chosen}),'preselection_verified':sum(fmap[i]['source_state']=='VERIFIED' for i in chosen),'preselection_conflict':sum(fmap[i]['source_state']=='CONFLICT' for i in chosen),'preselection_unknown':sum(fmap[i]['source_state']=='UNKNOWN' for i in chosen)})
 base=results[0]['test']['loss']
 for m in results:m['delta_u']=base-m['test']['loss']
 assert sha(selection_path)==selection_hash
 # Timings include shared preprocessing once; do not claim per-method independent timings.
 write(out/'SUMMARY.json',{'status':'COMPLETE','dataset':cfg['dataset'],'seed':cfg['seed'],'task':task,'metric':'LogLoss' if task['type']=='classification' else 'MAE','target_fits':len(results),'rf_detector_fits':1,'utility_predictor':'NOT_FITTED','methods':results,'quality':quality,'timing':timing,'selection_sha256':selection_hash,'shapley_status':'COMPUTED' if shapley else 'NOT_APPLICABLE_CLASSIFICATION','environment':{'python':sys.version,'numpy':np.__version__,'sklearn':sklearn.__version__,'threadpools':threadpool_info()},'cost_boundary':'Shared I/O and features measured separately; each selection, fit and evaluation also measured; timer excludes production of source/candidate data, interpretation must specify reused vs incremental costs and denominator; do not assert fund cost threshold without complete comparable flow.'})

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--out',required=True);run(parser.parse_args())
