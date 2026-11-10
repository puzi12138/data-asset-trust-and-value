"""Independent, read-only verification of portable tabular experiment results.

The verifier trusts only its own checkout's task configurations and explicitly
provided, hash-matching prepared data. It parses JSON and CSV; it never loads
pickle, imports submitted Python, starts a worker, or fits a model. Successful
arithmetic replay establishes consistency, not that someone really ran a fit,
that a public test set was blind, or that the scientific protocol was accepted.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import random
import re
import statistics
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
TASKS = {f'EXP-{i:02}' for i in range(1, 7)}
MAX_FILE = 100_000_000
MAX_FILES = 500
MAX_TREE = 250_000_000
HEX = re.compile(r'^[0-9a-f]{64}$')
RUN_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
FEATURES = ('present_fraction', 'finite_fraction', 'd0_squared_distance',
            'group_count', 'group_rarity', 'task_match', 'source_verified',
            'source_conflict', 'source_unknown')
REQUIRED = {'RUN_MANIFEST.json', 'METRICS.json', 'PREDICTIONS.csv', 'COST.json',
            'ENVIRONMENT.json', 'README.md', 'worker_output/SUMMARY.json', 'worker_output/SELECTION_FREEZE.json',
            'worker_output/AUDIT.json', 'worker_output/FEATURES.json', 'worker_output/RF_MODEL.json'}
CANONICAL_CODE = ('scripts/experiments/worker.py','src/data_asset_poc/__init__.py',
                  'src/data_asset_poc/assessment/__init__.py','src/data_asset_poc/assessment/selection.py',
                  'src/data_asset_poc/assessment/value_methods.py','src/data_asset_poc/assessment/binding.py')
CANONICAL_INFRA = ('scripts/experiments/run_task.py','scripts/experiments/resources.py',
                   'scripts/experiments/check_env.py','scripts/training/supervisor.py')

class InvalidResult(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise InvalidResult(message)

def path_in(root: Path, name: str) -> Path:
    require(isinstance(name, str) and bool(name) and '\\' not in name and '\x00' not in name,
            'Invalid path')
    q = PurePosixPath(name)
    require(not q.is_absolute() and all(x not in ('', '.', '..') for x in q.parts)
            and not re.match(r'^[A-Za-z]:', name), 'Unsafe path')
    root = root.resolve()
    p = root
    for part in q.parts:
        p = p / part
        require(not p.is_symlink(), 'Symlink in path')
    p.resolve().relative_to(root)
    return p

def bounded(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink(), f'Missing or invalid file: {path.name}')
    require(path.stat().st_size <= MAX_FILE, f'File too large: {path.name}')
    return path.read_bytes()

def digest(path: Path) -> str:
    return hashlib.sha256(bounded(path)).hexdigest()

def _pairs(pairs):
    d = {}
    for key, value in pairs:
        require(key not in d, f'Duplicate JSON key: {key}')
        d[key] = value
    return d

def _constant(value):
    raise InvalidResult('Non-finite JSON number: ' + value)

def read_json(path: Path):
    obj = json.loads(bounded(path).decode('utf-8-sig'), object_pairs_hook=_pairs,
                     parse_constant=_constant)
    def scan(x):
        if type(x) is float:
            require(math.isfinite(x), 'Non-finite numeric value')
        elif isinstance(x, dict):
            for v in x.values(): scan(v)
        elif isinstance(x, list):
            for v in x: scan(v)
    scan(obj)
    return obj

def number(v, where='number'):
    require(type(v) in (int, float) and math.isfinite(float(v)), 'Invalid ' + where)
    return float(v)

def close(a, b, where='value', tolerance=2e-8):
    x, y = number(a, where), number(b, where)
    require(abs(x-y) <= tolerance*max(1.0, abs(x), abs(y)),
            f'{where} does not recompute')

def data_rows(path):
    x = read_json(path)
    require(isinstance(x, dict) and isinstance(x.get('records'), list), 'Invalid data records')
    return x['records']

def computed_features(data, references):
    rows=data['rows']; d0=data['roles']['D0']; pool=data['roles']['pool']; task=data['task']
    d=len(rows[d0[0]]['x']); median=[];scale=[]
    for j in range(d):
        vals=[float(rows[i]['x'][j]) for i in d0 if rows[i]['x'][j] is not None]
        m=statistics.median(vals) if vals else 0.;filled=[float(rows[i]['x'][j]) if rows[i]['x'][j] is not None else m for i in d0]
        mean=statistics.fmean(filled);median.append(m);scale.append(math.sqrt(statistics.fmean((v-mean)**2 for v in filled)) or 1.)
    states={}
    for r in references:
        i=r['index'];require(i in pool and rows[i]['id']==r['id'] and r['id'] not in states,'Invalid source reference')
        states[r['id']]='VERIFIED' if (r['x'],r['y'])==(rows[i]['x'],rows[i]['y']) else 'CONFLICT'
    count=Counter(rows[i]['group'] for i in pool);features=[]
    for i in pool:
        r=rows[i];x=r['x']; state=states.get(r['id'],'UNKNOWN'); label=r['y']
        require(isinstance(x,list) and len(x)==d, 'Feature dimensions')
        if len(x)!=task.get('feature_count') or any(v is not None and (type(v) not in (float,int) or not math.isfinite(v)) for v in x): match='CONFLICT'
        elif label is None: match='UNKNOWN'
        elif task['type']=='regression': match='VERIFIED' if type(label) in (float,int) and math.isfinite(label) else 'CONFLICT'
        else: match='VERIFIED' if label in task.get('allowed_labels',[]) else 'CONFLICT'
        dist=statistics.fmean((((float(x[j]) if x[j] is not None else median[j])-median[j])/scale[j])**2 for j in range(d))
        f={'index':i,'id':r['id'],'group':r['group'],'present_fraction':sum(v is not None for v in x)/d,
           'finite_fraction':sum(v is not None and type(v) in (int,float) and math.isfinite(v) for v in x)/d,
           'd0_squared_distance':dist,'group_count':float(count[r['group']]),'group_rarity':1./count[r['group']],
           'task_match':{'VERIFIED':1.,'UNKNOWN':.5,'CONFLICT':0.}[match], 'task_state':match,'source_state':state,
           'source_verified':float(state=='VERIFIED'),'source_conflict':float(state=='CONFLICT'),'source_unknown':float(state=='UNKNOWN')}
        features.append(f)
    return features,states,{'median':median,'scale':scale,'feature_count':d}

def tree_score(tree,features):
    x=[struct.unpack('f',struct.pack('f',float(v)))[0] for v in features];node=0;steps=0
    left=tree['children_left'];right=tree['children_right'];feature=tree['feature'];threshold=tree['threshold'];value=tree['value']
    require(len(left)==len(right)==len(feature)==len(threshold)==len(value)>0,'Tree shape')
    while left[node]!=-1:
        f=feature[node];require(type(f) is int and 0<=f<len(x),'Tree feature')
        node=left[node] if x[f]<=number(threshold[node]) else right[node];steps+=1
        require(type(node) is int and 0<=node<len(left) and steps<=len(left),'Tree cycle or child')
    v=value[node][0];require(len(v)==2 and number(v[0])>=0 and number(v[1])>=0 and sum(v)>0,'Tree leaf')
    return v[1]/sum(v)

def group_pick(ordered,k):
    groups={}
    for row in ordered:groups.setdefault(row['group'],[]).append(row)
    out=[];depth=0
    while len(out)<k:
        old=len(out)
        for q in groups.values():
            if depth<len(q):
                out.append(q[depth]['index'])
                if len(out)==k:break
        require(len(out)>old,'Cannot fill selection');depth+=1
    return out

def choices(features,k,seed,scores,shapley):
    shuffled=list(features);random.Random(seed).shuffle(shuffled);prior={'VERIFIED':0,'UNKNOWN':1,'CONFLICT':2}
    ev=lambda r:(prior[r['source_state']],-r['task_match'],r['d0_squared_distance'],r['id'])
    rf=lambda r:(-scores[r['id']],r['id'])
    full=lambda r:(prior[r['source_state']],-r['task_match'],-scores[r['id']],r['d0_squared_distance'],r['id'])
    fixed=lambda r:.45*r['present_fraction']+.25*r['finite_fraction']+.2/(1+r['d0_squared_distance'])+.1*r['group_rarity']
    first=lambda seq:[r['index'] for r in seq[:k]]
    out={'random':first(shuffled),'random_group':group_pick(shuffled,k),'evidence_only':first(sorted(features,key=ev)),
         'evidence_group':group_pick(sorted(features,key=ev),k),'rf_source_conformity':first(sorted(features,key=rf)),
         'rf_source_conformity_group':group_pick(sorted(features,key=rf),k),'evidence_rf_group':group_pick(sorted(features,key=full),k),
         'completeness':first(sorted(features,key=lambda r:(-r['present_fraction'],r['id']))),
         'distance':first(sorted(features,key=lambda r:(r['d0_squared_distance'],r['id']))),
         'fixed_quality_score':first(sorted(features,key=lambda r:(-fixed(r),r['id'])))}
    if shapley is not None:out['mc_data_shapley']=first(sorted(features,key=lambda r:(-shapley[r['id']],r['id'])))
    return out

def verify_selection(result,cfg,paths,data,summary):
    refs=data_rows(paths[cfg['audit_reference']]); fs,states,stats=computed_features(data,refs)
    audit=read_json(path_in(result,'AUDIT.json'))['sample'];require(len(audit)==len(refs),'Audit count')
    for saved,ref in zip(audit,refs):
        require(saved['index']==ref['index'] and saved['id']==ref['id'] and saved['state']==states[ref['id']],'Audit mismatch')
    out=read_json(path_in(result,'FEATURES.json'));require(tuple(out['feature_names'])==FEATURES and len(out['features'])==len(fs),'Features shape')
    for a,b in zip(fs,out['features']):
        require(all(a[k]==b[k] for k in ('index','id','group','task_state','source_state')),'Feature state')
        for k in FEATURES:close(a[k],b[k],'Feature '+k)
    for k in ('median','scale'):
        require(len(stats[k])==len(out['stats_from_D0_only'][k]),'Feature stats dimensions')
        for a,b in zip(stats[k],out['stats_from_D0_only'][k]):close(a,b,'Feature stats')
    rf=read_json(path_in(result,'RF_MODEL.json'));trees=rf['trees'];require(len(trees)==32,'RF tree count')
    params=rf['params'];require(params.get('n_estimators')==32 and params.get('max_depth')==3 and params.get('random_state')==cfg['seed'],'RF protocol')
    scores={}
    for f in fs:
        v=statistics.fmean(tree_score(t,[f[n] for n in FEATURES]) for t in trees);close(v,rf['scores'][f['id']],'RF score',1e-7);scores[f['id']]=v
    require(set(rf['scores'])==set(scores),'RF score membership')
    shape=None
    if cfg['task_type']=='regression':
        s=read_json(path_in(result,'DATA_SHAPLEY.json'));runs=s['permutations'];pool=data['roles']['pool'];require(len(runs)==s['permutation_count']==8,'Shapley budget')
        require(s['permutation_seed']==cfg['seed']+190000 and len(s['scores'])==len(pool),'Shapley seed or shape')
        rng=random.Random(s['permutation_seed']);end=[]
        for run in runs:
            order=list(range(len(pool)));rng.shuffle(order);require(order==run['permutation'] and len(run['contributions'])==len(pool),'Shapley ordering')
            close(sum(run['contributions']),run['utility_all']-run['utility_D0'],'Shapley telescoping');end.append(run['utility_all'])
        for x in end:close(x,end[0],'Shapley final utility')
        for i in range(len(pool)):close(s['scores'][i],statistics.fmean(r['contributions'][i] for r in runs),'Shapley score')
        shape={data['rows'][i]['id']:float(v) for i,v in zip(pool,s['scores'])}
    selected=choices(fs,cfg['k'],cfg['seed'],scores,shape);saved=read_json(path_in(result,'SELECTION_FREEZE.json'))
    require(set(selected)==set(saved['methods'])==set(cfg['expected_methods']),'Selection method set')
    for name,indices in selected.items():require(indices==saved['methods'][name]['indices'],'Selection does not recompute: '+name)
    changed={x['index']:x['kind'] for x in data_rows(paths[cfg['injection_truth']])}
    require(len(changed)==len(data_rows(paths[cfg['injection_truth']])) and set(changed)<=set(data['roles']['pool']) and set(changed.values())<= {'label','feature'},'Invalid controlled-truth records')
    quality={x['method']:x for x in summary['quality']};require(set(quality)==set(selected),'Quality method set')
    index={f['index']:f for f in fs}
    for name,indices in selected.items():
        q=quality[name];require(q['n']==len(indices) and q['controlled_source_modifications']==sum(i in changed for i in indices),'Controlled defect count')
        for kind in ('label','feature'):require(q[kind+'_modifications']==sum(changed.get(i)==kind for i in indices),'Controlled defect type')
        require(q['groups']==len({data['rows'][i]['group'] for i in indices}),'Selected group count')
        for state in ('VERIFIED','UNKNOWN','CONFLICT'):require(q['preselection_'+state.lower()]==sum(index[i]['source_state']==state for i in indices),'Evidence count')
    return {'source_reference_count':len(refs),'selectors_replayed':len(selected),
            'rf_scope':'Saved tree scores recomputed; fitting origin not authenticated',
            'shapley_scope':'Stored permutation/order/mean/efficiency recomputed; coalition utility not independently refit'}

def macro_f1(ys, ps, classes):
    result=[]
    for c in classes:
        tp=sum(y==c and p==c for y,p in zip(ys,ps))
        fp=sum(y!=c and p==c for y,p in zip(ys,ps))
        fn=sum(y==c and p!=c for y,p in zip(ys,ps))
        den=2*tp+fp+fn
        result.append(2*tp/den if den else 0.0)
    return statistics.fmean(result)

def auc_binary(ys, probabilities, positive):
    positives=[v for y,v in zip(ys, probabilities) if y==positive]
    negatives=[v for y,v in zip(ys, probabilities) if y!=positive]
    require(positives and negatives, 'AUROC undefined with one class')
    ordered=sorted((v, y==positive) for y,v in zip(ys,probabilities))
    before=0; wins=0.; pos_total=0; i=0
    while i<len(ordered):
        j=i
        while j<len(ordered) and ordered[j][0]==ordered[i][0]: j+=1
        p=sum(bool(v[1]) for v in ordered[i:j]); n=(j-i)-p
        wins+=p*(before+0.5*n); before+=n; pos_total+=p; i=j
    return wins/(len(positives)*len(negatives))

def model_predict(model, row, expected_type):
    require(model.get('model') == expected_type, 'Wrong model type')
    stats=model['preprocess_D0']; med=stats['median']; scale=stats['scale']; x=row['x']
    require(isinstance(x,list) and len(x)==len(med)==len(scale) and len(x)>0, 'Model shape')
    z=[]
    for value, m, s in zip(x, med, scale):
        m=number(m); s=number(s); require(s>0, 'Invalid preprocessing scale')
        value=m if value is None else number(value)
        z.append((value-m)/s)
    coef=model['coef']; intercept=model['intercept']; classes=model.get('classes')
    if expected_type=='Ridge':
        require(classes is None and isinstance(coef,list) and len(coef)==len(z), 'Ridge shape')
        return sum(number(a)*b for a,b in zip(coef,z))+number(intercept), None
    require(isinstance(classes,list) and len(classes)>=2 and len(set(classes))==len(classes), 'Classes')
    require(isinstance(coef,list) and isinstance(intercept,list) and len(coef)==len(intercept), 'Logistic shape')
    eta=[]
    for co,bias in zip(coef,intercept):
        require(isinstance(co,list) and len(co)==len(z), 'Logistic coefficient shape')
        eta.append(sum(number(a)*b for a,b in zip(co,z))+number(bias))
    if len(classes)==2:
        require(len(eta)==1, 'Binary logistic shape')
        v=eta[0]; p=1/(1+math.exp(-v)) if v>=0 else math.exp(v)/(1+math.exp(v))
        probs=[1-p,p]
    else:
        require(len(eta)==len(classes), 'Multiclass logistic shape')
        mx=max(eta); exps=[math.exp(v-mx) for v in eta]; total=sum(exps)
        probs=[v/total for v in exps]
    return str(classes[max(range(len(classes)), key=lambda i:probs[i])]),probs

def verify_files(root, manifest):
    require(isinstance(manifest.get('files'),list) and len(manifest['files'])<=MAX_FILES, 'File manifest missing or too large')
    expected={}; total=0
    for item in manifest['files']:
        require(isinstance(item,dict) and set(('path','sha256','size_bytes'))<=set(item), 'Invalid file entry')
        n=item['path']; require(n not in expected and n not in ('RUN_MANIFEST.json','VERIFY_REPORT.json'), 'Duplicate or recursive file entry')
        require(isinstance(item['sha256'],str) and HEX.fullmatch(item['sha256']), 'Invalid file hash')
        p=path_in(root,n); raw=bounded(p); total+=len(raw)
        require(len(raw)==item['size_bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256'], 'Result hash or size mismatch: '+n)
        expected[n]=item
    require(total<=MAX_TREE, 'Result payload too large')
    actual=set()
    for p in root.rglob('*'):
        require(not p.is_symlink(), 'Symlink in result')
        if p.is_file():
            actual.add(p.relative_to(root).as_posix())
            require(len(actual)<=MAX_FILES+2, 'Too many result files')
    require(actual == set(expected)|{'RUN_MANIFEST.json'}|(actual & {'VERIFY_REPORT.json'}), 'Unexpected or unlisted result files')
    require(REQUIRED-{'RUN_MANIFEST.json'}<=set(expected), 'Required result files missing')
    return len(expected)

def manifest_and_config(result, repo):
    manifest=read_json(path_in(result,'RUN_MANIFEST.json'))
    require(isinstance(manifest,dict), 'Manifest is not an object')
    require(manifest.get('schema_version')=='1.0' and manifest.get('status')=='COMPLETE', 'Incomplete or incompatible run')
    require(isinstance(manifest.get('run_id'),str) and RUN_ID.fullmatch(manifest['run_id']), 'Invalid run id')
    task=manifest.get('task_id'); seed=manifest.get('seed')
    require(type(seed) is int and ((task in TASKS and seed in (1103,2207,3301)) or (task=='SMOKE-01' and seed==1103)), 'Unknown task or seed')
    config_path=path_in(repo,'tests/fixtures/community_smoke/config.json' if task=='SMOKE-01' else f'experiments/community/configs/{task}/{seed}.json')
    cfg=read_json(config_path)
    require((cfg.get('task_id'),cfg.get('seed'))==(task,seed), 'Canonical configuration mismatch')
    if task=='SMOKE-01':require(cfg.get('mode')=='SYNTHETIC_SMOKE','Smoke is not marked synthetic')
    require(manifest.get('config_sha256')==digest(config_path), 'Configuration hash mismatch')
    require(manifest.get('worker_sha256')==digest(path_in(repo,'scripts/experiments/worker.py')), 'Worker hash mismatch')
    code=manifest.get('code');require(isinstance(code,list) and len(code)==len(CANONICAL_CODE),'Implementation code manifest missing')
    require(all(isinstance(x,dict) and set(('path','sha256'))<=set(x) for x in code),'Invalid code entry')
    code={x['path']:x['sha256'] for x in code};require(set(code)==set(CANONICAL_CODE),'Implementation file set mismatch')
    for name in CANONICAL_CODE:require(code[name]==digest(path_in(repo,name)),'Implementation hash mismatch: '+name)
    infra=manifest.get('infrastructure');require(isinstance(infra,list) and len(infra)==len(CANONICAL_INFRA),'Infrastructure manifest missing')
    require(all(isinstance(x,dict) and set(('path','sha256'))<=set(x) for x in infra),'Invalid infrastructure entry')
    infra={x['path']:x['sha256'] for x in infra};require(set(infra)==set(CANONICAL_INFRA),'Infrastructure file set mismatch')
    for name in CANONICAL_INFRA:require(infra[name]==digest(path_in(repo,name)),'Infrastructure hash mismatch: '+name)
    require(isinstance(manifest.get('git_commit'),str) and re.fullmatch(r'[0-9a-f]{40}',manifest['git_commit']), 'Invalid commit hash')
    for name in ('target_model','primary_metric','expected_methods'):
        require(manifest.get(name)==cfg.get(name), 'Protocol mismatch: '+name)
    require(manifest.get('inputs')==cfg.get('inputs') and isinstance(cfg.get('inputs'),list), 'Input manifest mismatch')
    require(type(cfg.get('k')) is int and cfg['k']>0, 'Invalid candidate budget')
    require(isinstance(cfg['expected_methods'],list) and len(set(cfg['expected_methods']))==len(cfg['expected_methods']) and 'D0' not in cfg['expected_methods'], 'Invalid canonical arms')
    require((cfg['task_type'],cfg['target_model'],cfg['primary_metric']) in
            [('classification','LogisticRegression','LogLoss'),('regression','Ridge','MAE')], 'Unsupported task protocol')
    count=verify_files(result,manifest)
    return manifest,cfg,count

def verify_inputs(data_root, cfg):
    paths={}
    for e in cfg['inputs']:
        require(isinstance(e,dict) and set(('path','sha256'))<=set(e), 'Bad canonical input')
        require(e['path'] not in paths and isinstance(e['sha256'],str) and HEX.fullmatch(e['sha256']), 'Duplicate or invalid canonical input')
        p=path_in(data_root,e['path']);require(digest(p)==e['sha256'], 'Input hash mismatch: '+e['path'])
        paths[e['path']]=p
    for key in ('selection_input','audit_reference','development_input','test_input','injection_truth'):
        require(cfg.get(key) in paths, 'Required input not hash bound: '+key)
    return paths

def check_overlap(data,dev,test):
    rows=data['rows'];roles=data['roles'];d0=roles['D0'];pool=roles['pool']
    require(all(type(i) is int and 0<=i<len(rows) and isinstance(rows[i],dict) for i in d0+pool), 'Invalid member index')
    require(len(set(d0+pool))==len(d0)+len(pool) and bool(d0) and bool(pool), 'Duplicate or empty training role')
    role_rows={'D0':[rows[i] for i in d0], 'pool':[rows[i] for i in pool], 'dev':dev, 'test':test}
    groups={};ids={}
    for role,records in role_rows.items():
        require(records and all(isinstance(r,dict) and isinstance(r.get('id'),str) and r['id'] and r.get('group') is not None for r in records), 'Missing ID/group')
        ids[role]={r['id'] for r in records};groups[role]={str(r['group']) for r in records}
        require(len(ids[role])==len(records), 'Duplicate ID in '+role)
    for i,a in enumerate(role_rows):
        for b in list(role_rows)[i+1:]:
            require(not ids[a]&ids[b] and not groups[a]&groups[b], 'Data leakage between '+a+' and '+b)
    return rows,d0,pool

def validate_metrics_and_predictions(result,cfg,paths):
    worker_result=path_in(result,'worker_output')
    data=read_json(paths[cfg['selection_input']]);dev=data_rows(paths[cfg['development_input']]);test=data_rows(paths[cfg['test_input']])
    rows,d0,pool=check_overlap(data,dev,test)
    require(data['task']['type']==cfg['task_type'], 'Task type mismatch')
    require(data['task'].get('evaluation_metric')==cfg['primary_metric'] and data['task'].get('target_model')==cfg['target_model'],'Input task binding mismatch')
    if 'roles' in cfg:require(all(data['roles'][r]==cfg['roles'][r] for r in ('D0','pool')),'Frozen split differs from configuration')
    member_paths=[name for name in paths if PurePosixPath(name).name.lower()=='members.json']
    require(len(member_paths)==1,'Canonical member list missing')
    members=read_json(paths[member_paths[0]])['roles'];require(set(members)=={'D0','pool','dev','test'},'Canonical membership roles')
    all_members=[m['index'] for arr in members.values() for m in arr]
    require(len(all_members)==len(rows)==len(set(all_members)) and set(all_members)==set(range(len(rows))),'Canonical membership coverage')
    for role,records in [('D0',[rows[i] for i in d0]),('pool',[rows[i] for i in pool]),('dev',dev),('test',test)]:
        values=members[role];require(len(values)==len(records),'Canonical membership count')
        require(all(all(m[k]==r[k] for k in ('id','source_row','group')) for m,r in zip(values,records)),'Canonical membership mismatch: '+role)
        if role in ('D0','pool'):require([m['index'] for m in values]==data['roles'][role],'Canonical member indices')
        if 'roles' in cfg:require([m['index'] for m in values]==cfg['roles'][role],'Frozen role indices')
    require(all(rows[m['index']] is None for role in ('dev','test') for m in members[role]),'Held-out rows exposed to selector')
    summary=read_json(path_in(worker_result,'SUMMARY.json')); metrics=read_json(path_in(result,'METRICS.json'))
    require(summary==metrics and summary.get('status')=='COMPLETE' and summary.get('seed')==cfg['seed'] and summary.get('dataset')==cfg['dataset'], 'Summary mismatch')
    require(summary.get('metric')==cfg['primary_metric'], 'Wrong primary metric')
    require(summary.get('rf_detector_fits')==1 and summary.get('utility_predictor')=='NOT_FITTED','Detector or predictor status mismatch')
    require(summary.get('shapley_status')==('COMPUTED' if cfg['task_type']=='regression' else 'NOT_APPLICABLE_CLASSIFICATION'),'Shapley status mismatch')
    expected=['D0']+cfg['expected_methods']; methods=summary.get('methods')
    require(isinstance(methods,list) and len(methods)==len(expected), 'Missing/duplicate training arms')
    by={x['method']:x for x in methods};require(set(by)==set(expected), 'Wrong training arms')
    require(summary.get('target_fits')==len(expected), 'Incorrect number of fits')
    selection_path=path_in(worker_result,'SELECTION_FREEZE.json'); selections=read_json(selection_path)
    require(set(selections['methods'])==set(cfg['expected_methods']), 'Selection arms mismatch')
    require(selections.get('test_labels_parsed_before_freeze') is False and summary.get('selection_sha256')==digest(selection_path), 'Selection freeze evidence missing')
    with path_in(result,'PREDICTIONS.csv').open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f); fields=['arm','split','id','group','y','prediction','probabilities','classes','loss']
        require(reader.fieldnames==fields, 'Prediction CSV columns mismatch')
        csv_rows={}
        for v in reader:
            require(None not in v and all(isinstance(v[k],str) for k in fields), 'Malformed CSV row')
            key=(v['arm'],v['split'],v['id']);require(key not in csv_rows, 'Duplicate prediction row')
            require(len(csv_rows)<2_000_000, 'Too many prediction rows');csv_rows[key]=v
    evals={'dev':dev,'test':test}; calculated={}; n_predictions=0; seen=set()
    for name in expected:
        chosen=[] if name=='D0' else selections['methods'][name]['indices']
        require(isinstance(chosen,list) and len(set(chosen))==len(chosen) and set(chosen)<=set(pool), 'Invalid chosen members')
        if name!='D0':
            require(len(chosen)==cfg['k'] and selections['methods'][name]['ids']==[rows[i]['id'] for i in chosen], 'Budget or selected IDs mismatch')
        members=read_json(path_in(worker_result,name+'/TRAIN_MEMBERS.json')); model=read_json(path_in(worker_result,name+'/MODEL.json'))
        selected=d0+chosen
        require(members['indices']==selected and members['members']==[{k:rows[i][k] for k in ('id','source_row','group')} for i in selected], 'Train members mismatch')
        m=by[name];require(m.get('n_train')==len(selected) and m.get('n_added')==len(chosen), 'Training budget mismatch')
        require(m.get('selection_sha256')==summary['selection_sha256'], 'Selection not bound to arm')
        arm_metrics=read_json(path_in(worker_result,name+'/METRICS.json'))
        require(arm_metrics=={k:value for k,value in m.items() if k!='delta_u'},'Arm metrics differ from summary')
        params=model['params']
        if cfg['target_model']=='LogisticRegression':
            require(params.get('C')==1. and params.get('solver')=='lbfgs' and params.get('max_iter')==500 and params.get('tol')==1e-5 and params.get('random_state')==cfg['seed'] and params.get('fit_intercept') is True,'Target logistic protocol')
        else:require(params.get('alpha')==1. and params.get('fit_intercept') is True,'Target ridge protocol')
        calculated[name]={}
        for split,records in evals.items():
            require(m[split].get('metric')==cfg['primary_metric'],'Split primary metric mismatch')
            saved=read_json(path_in(worker_result,name+'/'+split+'_PREDICTIONS.json'))
            require(isinstance(saved,list) and len(saved)==len(records) and m[split]['n']==len(records), 'Evaluation count mismatch')
            ys=[];preds=[];losses=[];positive_scores=[]
            for record,out in zip(records,saved):
                require((out['id'],out['group'],out['y'])==(record['id'],record['group'],record['y']), 'Evaluation member mismatch')
                key=(name,split,record['id']);require(key in csv_rows,'Missing CSV prediction');v=csv_rows[key];seen.add(key)
                require(v['group']==str(record['group']), 'CSV group mismatch')
                pred,probs=model_predict(model,record,cfg['target_model'])
                if probs is None:
                    close(float(v['y']),float(record['y']),'CSV truth');close(float(v['prediction']),pred,'CSV prediction');close(out['prediction'],pred,'Saved prediction')
                    require(not v['probabilities'] and not v['classes'], 'Unexpected regression probabilities')
                    loss=abs(float(record['y'])-pred);ys.append(float(record['y']));preds.append(pred)
                else:
                    classes=model['classes'];require(record['y'] in classes and out['classes']==classes, 'Truth/class mismatch')
                    require(v['y']==str(record['y']) and v['prediction']==pred and out['prediction']==pred, 'Classification prediction mismatch')
                    vp=json.loads(v['probabilities'],parse_constant=_constant);vc=json.loads(v['classes'],parse_constant=_constant)
                    require(vc==classes and len(vp)==len(probs)==len(out['probabilities']), 'Probability shape')
                    for a,b,c in zip(vp,probs,out['probabilities']): close(a,b,'CSV probability');close(c,b,'Saved probability')
                    close(sum(probs),1.,'Probability sum');q=probs[classes.index(record['y'])]
                    loss=-math.log(max(2**-52,min(1.-2**-52,q)));ys.append(record['y']);preds.append(pred);positive_scores.append(probs[-1])
                close(float(v['loss']),loss,'CSV loss');close(out['loss'],loss,'Saved loss');losses.append(loss);n_predictions+=1
            loss=statistics.fmean(losses);close(m[split]['loss'],loss,'Primary metric');calculated[name][split]=loss
            if cfg['task_type']=='classification':
                close(m[split]['accuracy'],sum(str(y)==p for y,p in zip(ys,preds))/len(ys),'Accuracy')
                close(m[split]['macro_f1'],macro_f1([str(y) for y in ys],preds,[str(c) for c in model['classes']]),'Macro F1')
                if m[split].get('auroc') is not None:
                    require(len(model['classes'])==2, 'Unexpected multiclass AUROC')
                    close(m[split]['auroc'],auc_binary(ys,positive_scores,model['classes'][-1]),'AUROC')
            else:
                rmse=math.sqrt(statistics.fmean((y-p)**2 for y,p in zip(ys,preds)));mean=statistics.fmean(ys)
                sse=sum((y-p)**2 for y,p in zip(ys,preds));sst=sum((y-mean)**2 for y in ys)
                r2=1-sse/sst if sst else (1. if not sse else 0.)
                close(m[split]['rmse'],rmse,'RMSE');close(m[split]['r2'],r2,'R2')
        for field in ('median','scale'):
            # Derived using D0 only, independently of model's saved value.
            expected_stat=[]
            for j in range(len(rows[d0[0]]['x'])):
                vals=[float(rows[i]['x'][j]) for i in d0 if rows[i]['x'][j] is not None]
                median=statistics.median(vals) if vals else 0.;filled=[float(rows[i]['x'][j]) if rows[i]['x'][j] is not None else median for i in d0]
                avg=statistics.fmean(filled);scale=math.sqrt(statistics.fmean((v-avg)**2 for v in filled)) or 1.
                expected_stat.append(median if field=='median' else scale)
            got=model['preprocess_D0'][field];require(len(got)==len(expected_stat),'Preprocessing dimensions')
            for a,b in zip(got,expected_stat):close(a,b,'D0 preprocessing')
    require(seen==set(csv_rows), 'Extra CSV predictions')
    for name in expected:close(by[name]['delta_u'],calculated['D0']['test']-calculated[name]['test'],'Delta utility')
    selection_check=verify_selection(worker_result,cfg,paths,data,summary)
    if cfg['task_type']=='regression':
        for r in read_json(path_in(worker_result,'DATA_SHAPLEY.json'))['permutations']:
            close(r['utility_D0'],-calculated['D0']['dev'],'Shapley base utility')
    return {'arms':len(expected),'prediction_rows':n_predictions,'primary_metric':cfg['primary_metric'],
            'deltas':{n:calculated['D0']['test']-calculated[n]['test'] for n in expected},
            'group_separation':'PASS','selection_replay':selection_check,'scope':'Evaluations from saved coefficients and trusted labels; no model refit.'}

def verify(result: Path, data_root: Path | None, repo: Path = ROOT):
    original_result=result;result=result.resolve();repo=repo.resolve();report={'status':'SUBMITTED','schema_status':'NOT_CHECKED','data_status':'NOT_CHECKED',
        'scientific_acceptance':'NEEDS_INDEPENDENT_REVIEW','training_replayed':False,
        'limitations':['Hashes and numerical consistency do not prove execution, identity, independent factual correctness, or blind testing.',
                       'Saved model fitting and selector provenance require independent review or rerun.',
                       'Public test labels and public controlled perturbations are not an untouched blind final test.']}
    try:
        require(result.is_dir() and not original_result.is_symlink(), 'Result directory unavailable')
        manifest,cfg,count=manifest_and_config(result,repo)
        report.update(schema_status='SCHEMA_VALID',task_id=manifest['task_id'],seed=manifest['seed'],run_id=manifest['run_id'],files_verified=count,synthetic_smoke=manifest['task_id']=='SMOKE-01')
        cost=read_json(path_in(result,'COST.json'));env=read_json(path_in(result,'ENVIRONMENT.json'))
        require(isinstance(cost,dict) and isinstance(env,dict) and bool(cost) and bool(env), 'Cost or environment evidence missing')
        for field in ('wall_seconds','cpu_seconds','peak_rss_bytes','worker_limit_seconds'):
            require(number(cost[field],field)>=0,'Negative cost: '+field)
        require(cost.get('max_workers')==1 and cost.get('device')=='cpu' and cost.get('gpu_used') is False,'Resource protocol mismatch')
        require(cost.get('sample_budget_per_arm')==cfg['k'] and cost.get('token_budget')=='NOT_APPLICABLE_TABULAR','Cost budget mismatch')
        monitor=cost['worker_monitor'];require(monitor.get('status')=='COMPLETE' and monitor.get('worker_started') is True and monitor.get('training_go_sent') is True,'Supervisor receipt incomplete')
        for field in ('python_version','os','cpu_model','system_memory_total_bytes','packages','gpu'):
            require(field in env and env[field] is not None,'Missing environment field: '+field)
        if data_root is None:
            report.update(status='NEEDS_REVIEW',reason='Canonical hash-matching data not supplied; metrics and group isolation not recomputed.')
            return report
        paths=verify_inputs(data_root.resolve(),cfg);report['data_status']='HASH_MATCH'
        result_metrics=validate_metrics_and_predictions(result,cfg,paths)
        report.update(status='REPRODUCED',numerical_replay=result_metrics)
        return report
    except (InvalidResult,AssertionError,ValueError,TypeError,KeyError,IndexError,OverflowError,ZeroDivisionError,OSError,UnicodeError,RecursionError,struct.error,csv.Error) as exc:
        report.update(status='REJECTED',reason=f'{type(exc).__name__}: {exc}')
        return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--result',required=True,type=Path)
    p.add_argument('--data-root','--data-dir',dest='data_root',type=Path,help='trusted prepared input directory')
    p.add_argument('--output',type=Path,help='new report path; defaults to stdout')
    args=p.parse_args();report=verify(args.result,args.data_root)
    body=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.output:
        out=args.output
        require(not out.exists() and not out.is_symlink(), 'Report output already exists')
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf8') as f:f.write(body)
    print(body,end='')
    return 0 if report['status']=='REPRODUCED' else (2 if report['status']=='NEEDS_REVIEW' else 1)

if __name__=='__main__':
    sys.exit(main())
