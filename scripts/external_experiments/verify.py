"""Replay external-experiment predictions and selections without fitting a model.

Reads only JSON under explicitly named inputs/result roots, and uses the trusted
in-repository arithmetic verifier, not submitted Python or serialized objects.
Successful replay is numerical consistency, not proof of training identity,
blind evaluation, natural label correctness, or scientific acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.experiments import verify_result as reference

METHODS = ('random_group', 'completeness', 'rf_source_conformity_group', 'evidence_rf_group')
MODELS = ('logistic', 'random_forest')
BUDGETS = (128, 512)
SEEDS = (1103, 2207, 3301)
require = reference.require
close = reference.close
number = reference.number
read = reference.read_json
within = reference.path_in
digest = reference.digest


def _safe_probability(values):
    require(isinstance(values, list) and len(values) >= 2, 'Probability shape')
    values = [number(v, 'probability') for v in values]
    require(all(0.0 <= v <= 1.0 for v in values), 'Probability outside [0,1]')
    vals = [max(1e-12, v) for v in values]
    total = sum(vals)
    return [v / total for v in vals]


def _tree_probs(tree, values, n_classes):
    """Evaluate a saved sklearn classification tree; sklearn uses float32 X."""
    x = [struct.unpack('<f', struct.pack('<f', number(v)))[0] for v in values]
    left = tree['children_left']; right = tree['children_right']
    feature = tree['feature']; threshold = tree['threshold']; value = tree['value']
    require(len(left) == len(right) == len(feature) == len(threshold) == len(value) > 0, 'Tree arrays')
    node = 0
    for _ in range(len(left) + 1):
        require(type(node) is int and 0 <= node < len(left), 'Tree child index')
        if left[node] == -1:
            require(right[node] == -1, 'Malformed tree leaf')
            vals = value[node]
            require(isinstance(vals, list) and len(vals) == 1 and len(vals[0]) == n_classes, 'Tree class shape')
            counts = [number(v, 'leaf class weight') for v in vals[0]]
            require(all(v >= 0 for v in counts) and sum(counts) > 0, 'Tree leaf weights')
            return [v / sum(counts) for v in counts]
        col = feature[node]
        require(type(col) is int and 0 <= col < len(x), 'Tree feature index')
        node = left[node] if x[col] <= number(threshold[node]) else right[node]
    raise reference.InvalidResult('Tree cycle')


def model_predict(model, row):
    stats = model['preprocess_D0']; med = stats['median']; scale = stats['scale']
    require(isinstance(row['x'], list) and len(row['x']) == len(med) == len(scale) > 0, 'Feature dimension')
    z = []
    for v, m, s in zip(row['x'], med, scale):
        m = number(m); s = number(s); require(s > 0, 'Preprocess scale')
        z.append(((m if v is None else number(v)) - m) / s)
    classes = model['classes']
    require(isinstance(classes, list) and len(classes) >= 2 and len(set(classes)) == len(classes), 'Class list')
    if model['model'] == 'LogisticRegression':
        coef = model['coef']; intercept = model['intercept']
        require(len(coef) == len(intercept), 'Coefficient intercept shape')
        require(all(isinstance(c, list) and len(c) == len(z) for c in coef), 'Coefficient feature shape')
        eta = [sum(number(a) * b for a, b in zip(c, z)) + number(i) for c, i in zip(coef, intercept)]
        if len(classes) == 2:
            require(len(eta) == 1, 'Binary logistic shape')
            e = eta[0]; p = 1/(1+math.exp(-e)) if e >= 0 else math.exp(e)/(1+math.exp(e))
            raw = [1-p, p]
        else:
            require(len(eta) == len(classes), 'Multiclass logistic shape')
            exp = [math.exp(e - max(eta)) for e in eta]; total = sum(exp)
            raw = [e/total for e in exp]
    elif model['model'] == 'RandomForestClassifier':
        trees = model['trees']; require(isinstance(trees, list) and len(trees) > 0, 'Empty forest')
        contributions = [_tree_probs(t, z, len(classes)) for t in trees]
        raw = [statistics.fmean(t[i] for t in contributions) for i in range(len(classes))]
    else:
        raise reference.InvalidResult('Unsupported target model')
    predicted = classes[max(range(len(raw)), key=lambda i: raw[i])]
    return predicted, _safe_probability(raw)


def _make_predictor(model):
    """Prepare a read-only predictor once per saved arm, never fit a model.

    Forest leaf distributions are checked on first visit and reused for that
    saved tree; every evaluation still checks the row and traversed path.
    Cache lifetime is local to this loaded model and never spans model arms.
    """
    stats = model['preprocess_D0']; med = stats['median']; scale = stats['scale']
    require(isinstance(med, list) and isinstance(scale, list) and len(med) == len(scale) > 0, 'Feature dimension')
    med = tuple(number(v) for v in med); scale = tuple(number(v) for v in scale)
    require(all(v > 0 for v in scale), 'Preprocess scale')
    classes = model['classes']
    require(isinstance(classes, list) and len(classes) >= 2 and len(set(classes)) == len(classes), 'Class list')
    dim = len(med); n_classes = len(classes)

    if model['model'] == 'LogisticRegression':
        raw_coef = model['coef']; raw_intercept = model['intercept']
        require(len(raw_coef) == len(raw_intercept), 'Coefficient intercept shape')
        require(all(isinstance(c,list) and len(c) == dim for c in raw_coef), 'Coefficient feature shape')
        coef = tuple(tuple(number(x) for x in row) for row in raw_coef)
        intercept = tuple(number(x) for x in raw_intercept)
        require(len(coef) == (1 if n_classes == 2 else n_classes), 'Logistic class shape')
        def probabilities(z):
            eta = [sum(a*b for a,b in zip(c,z))+bias for c,bias in zip(coef,intercept)]
            if n_classes == 2:
                e = eta[0]; p = 1/(1+math.exp(-e)) if e >= 0 else math.exp(e)/(1+math.exp(e))
                return [1-p,p]
            exps=[math.exp(e-max(eta)) for e in eta]; total=sum(exps)
            return [e/total for e in exps]
    elif model['model'] == 'RandomForestClassifier':
        trees=model['trees'];require(isinstance(trees,list) and len(trees)>0, 'Empty forest')
        prepared=[]
        for tree in trees:
            left=tree['children_left'];right=tree['children_right'];feature=tree['feature'];threshold=tree['threshold'];value=tree['value']
            require(len(left)==len(right)==len(feature)==len(threshold)==len(value)>0,'Tree arrays')
            prepared.append((left,right,feature,threshold,value,[None]*len(left)))
        def tree_proba(entry,x):
            left,right,feature,threshold,value,cache=entry
            node=0
            for _ in range(len(left)+1):
                require(type(node) is int and 0<=node<len(left),'Tree child index')
                part=cache[node]
                if part is None:
                    if left[node]==-1:
                        require(right[node]==-1,'Malformed tree leaf')
                        values=value[node]
                        require(isinstance(values,list) and len(values)==1 and len(values[0])==n_classes,'Tree class shape')
                        counts=[number(v,'leaf class weight') for v in values[0]]
                        total=sum(counts)
                        require(all(v>=0 for v in counts) and total>0,'Tree leaf weights')
                        part=(-1,tuple(v/total for v in counts))
                    else:
                        col=feature[node];require(type(col) is int and 0<=col<len(x),'Tree feature index')
                        part=(col,number(threshold[node]),left[node],right[node])
                    cache[node]=part
                if part[0]==-1:return part[1]
                node=part[2] if x[part[0]]<=part[1] else part[3]
            raise reference.InvalidResult('Tree cycle')
        def probabilities(z):
            fmt='<'+str(len(z))+'f'
            x=struct.unpack(fmt,struct.pack(fmt,*z))
            contributions=[tree_proba(tree,x) for tree in prepared]
            return [statistics.fmean(t[i] for t in contributions) for i in range(n_classes)]
    else:
        raise reference.InvalidResult('Unsupported target model')

    def predict(row):
        values=row['x'];require(isinstance(values,list) and len(values)==dim,'Feature dimension')
        z=[((m if v is None else number(v))-m)/s for v,m,s in zip(values,med,scale)]
        raw=probabilities(z)
        predicted=classes[max(range(n_classes),key=lambda i:raw[i])]
        return predicted,_safe_probability(raw)
    return predict


def _stats(rows, indices):
    require(bool(indices), 'Empty D0')
    medians = []; scales = []
    for j in range(len(rows[indices[0]]['x'])):
        values = [number(rows[i]['x'][j]) for i in indices if rows[i]['x'][j] is not None]
        median = statistics.median(values) if values else 0.0
        filled = [number(rows[i]['x'][j]) if rows[i]['x'][j] is not None else median for i in indices]
        mean = statistics.fmean(filled)
        medians.append(median); scales.append(math.sqrt(statistics.fmean((v-mean)**2 for v in filled)) or 1.0)
    return {'median': medians, 'scale': scales, 'feature_count': len(medians)}


def _same_stats(a, b):
    for key in ('median', 'scale'):
        require(len(a[key]) == len(b[key]), 'Statistic dimension')
        for av, bv in zip(a[key], b[key]): close(av, bv, 'D0 statistic')
    require(b['feature_count'] == a['feature_count'], 'D0 feature count')


def _protocol_squared_distance(x, stats):
    """Recompute using the published column order and IEEE double accumulation."""
    medians = stats['median']; scales = stats['scale']
    require(len(x) == len(medians) == len(scales) and bool(medians), 'Distance feature dimension')
    total = 0.0
    for value, median, scale in zip(x, medians, scales):
        median = number(median); scale = number(scale)
        require(scale > 0, 'Distance scale')
        v = median if value is None else number(value)
        total += ((v - median)/scale)**2
    return total / len(medians)


def _separate(data, dev, test):
    rows = data['rows']; roles = data['roles']; d0 = roles['D0']; pool = roles['pool']
    require(isinstance(rows, list) and all(type(i) is int and 0 <= i < len(rows) for i in d0 + pool), 'Input index')
    require(len(set(d0 + pool)) == len(d0) + len(pool) == len(rows), 'Input role coverage')
    role_rows = {'D0':[rows[i] for i in d0], 'pool':[rows[i] for i in pool], 'dev':dev, 'test':test}
    for name, records in role_rows.items():
        require(isinstance(records, list) and bool(records), 'Empty ' + name)
        require(all(isinstance(r, dict) and isinstance(r.get('id'), str) and r['id'] and r.get('group') is not None for r in records), 'Missing member ID/group')
        require(len({r['id'] for r in records}) == len(records), 'Duplicate ID in ' + name)
    for i, a in enumerate(role_rows):
        for b in list(role_rows)[i+1:]:
            require(not ({r['id'] for r in role_rows[a]} & {r['id'] for r in role_rows[b]}), 'ID leakage '+a+'/'+b)
            require(not ({r['group'] for r in role_rows[a]} & {r['group'] for r in role_rows[b]}), 'Group leakage '+a+'/'+b)
    return rows, d0, pool


def _inputs(config, data_dir):
    seen = {}
    for item in config['inputs']:
        name = item['path']; expected = item['sha256']
        require(isinstance(name, str) and name not in seen, 'Duplicate input')
        require(isinstance(expected, str) and reference.HEX.fullmatch(expected), 'Invalid input SHA')
        path = within(data_dir, name); require(digest(path) == expected, 'Input SHA mismatch: '+name)
        seen[name] = path
    for key in ('selection_input', 'audit_reference', 'development_input', 'test_input', 'injection_truth'):
        require(config[key] in seen, 'Unbound input '+key)
    source = [p for name,p in seen.items() if Path(name).name.lower() == 'source_record.json']
    require(len(source) == 1, 'Bound source record missing')
    return seen, source[0]


def _verify_selections(result, cfg, data, refs, selections):
    fs, states, stats = reference.computed_features(data, refs)
    # Ranking is sensitive to final-bit ties. `reference.computed_features`
    # uses fsum whereas the frozen selector specifies ordered accumulation.
    # Recompute that distance independently using the published operation order.
    for f in fs:
        f['d0_squared_distance'] = _protocol_squared_distance(data['rows'][f['index']]['x'], stats)
    audit = read(within(result, 'AUDIT.json'))['sample']
    require(len(audit) == len(refs), 'Evidence audit size')
    for saved, ref in zip(audit, refs):
        require(saved['index'] == ref['index'] and saved['id'] == ref['id'] and saved['state'] == states[ref['id']], 'Evidence audit mismatch')
    feature_out = read(within(result, 'FEATURES.json'))
    require(tuple(feature_out['feature_names']) == reference.FEATURES and len(feature_out['features']) == len(fs), 'Feature shape')
    _same_stats(stats, feature_out['stats_from_D0_only'])
    for a,b in zip(fs, feature_out['features']):
        require(all(a[k] == b[k] for k in ('index','id','group','task_state','source_state')), 'Feature membership/state')
        for key in reference.FEATURES: close(a[key], b[key], 'Feature '+key)
    detector = read(within(result, 'RF_MODEL.json'))
    params = detector['params']; trees = detector['trees']
    require(len(trees) == 32 and params.get('n_estimators') == 32 and params.get('max_depth') == 3 and params.get('random_state') == cfg['seed'] and params.get('n_jobs') == 1, 'Detector protocol')
    d0 = data['roles']['D0']; pool = data['roles']['pool']; rows = data['rows']
    require(detector['classes'] == [0,1] and detector['calibration_rows'] == 3*len(d0), 'Detector classes or calibration rows')
    n = min(len(d0), round(len(d0)*256/len(pool))); rng = random.Random(cfg['seed']+180000)
    wanted = {kind:rng.sample(range(len(d0)),n) for kind in ('source','label_changed','feature_changed')}
    require(detector['calibration_sample_indices'] == wanted, 'Detector calibration sample')
    mutation_column = cfg.get('mutation_feature_index', 0)
    xs = [number(rows[i]['x'][mutation_column]) for i in d0 if rows[i]['x'][mutation_column] is not None]
    require(bool(xs), 'No D0 mutation feature')
    close(detector['calibration_mutation_feature_upper'],max(xs)+3*(statistics.pstdev(xs) or 1.),'Detector mutation threshold')
    scores = {}
    for feature in fs:
        score = statistics.fmean(reference.tree_score(tree, [feature[n] for n in reference.FEATURES]) for tree in trees)
        saved = detector['scores'][feature['id']]
        close(score, saved, 'Detector score', 1e-12)
        # Sort using the saved, independently checked double; a 1-ULP
        # summation-order change must not arbitrarily resolve an actual tie.
        scores[feature['id']] = float(saved)
    require(set(scores) == set(detector['scores']), 'Detector score membership')
    require(set(selections['methods']) == {f'k{k}__{m}' for k in BUDGETS for m in METHODS}, 'Selection arm set')
    for k in BUDGETS:
        expected = reference.choices(fs, k, cfg['seed'], scores, None)
        for method in METHODS:
            name = f'k{k}__{method}'; actual = selections['methods'][name]
            require(actual['indices'] == expected[method], 'Selection mismatch: '+name)
            require(actual['ids'] == [data['rows'][i]['id'] for i in actual['indices']], 'Selection IDs mismatch')
    return stats


def _target_params(model, which, seed):
    p = model['params']
    if which == 'logistic':
        require(model['model'] == 'LogisticRegression' and p.get('C') == 1.0 and p.get('solver') == 'lbfgs' and p.get('max_iter') == 500 and p.get('tol') == 1e-5 and p.get('random_state') == seed and p.get('fit_intercept') is True, 'Logistic parameters')
    else:
        require(model['model'] == 'RandomForestClassifier' and p.get('n_estimators') == 100 and p.get('max_depth') == 12 and p.get('random_state') == seed and p.get('n_jobs') == 1 and len(model['trees']) == 100, 'Forest parameters')


def verify(result, config_path, data_dir):
    report = {'status':'REJECTED', 'training_replayed':False, 'scientific_acceptance':'NEEDS_INDEPENDENT_REVIEW',
              'scope':'Saved predictions and selectors are recomputed; no model fit, identity authentication, natural-label verification or blind-test proof.'}
    try:
        result = Path(result).resolve(strict=True); data_dir = Path(data_dir).resolve(strict=True); config_path = Path(config_path).resolve(strict=True)
        require(result.is_dir() and data_dir.is_dir(), 'Input roots are not directories')
        cfg = read(config_path)
        require(cfg['schema'] == 'external-experiment-v1' and cfg['task_id'] in ('EXT-02','EXTERNAL-SMOKE') and cfg['task_type'] == 'classification', 'Config identity')
        if cfg['task_id'] == 'EXTERNAL-SMOKE':
            require(cfg.get('scope') == 'SYNTHETIC_SMOKE_NON_SCIENTIFIC', 'Synthetic smoke scope')
        require(cfg['seed'] in SEEDS and cfg['budgets'] == list(BUDGETS) and cfg['models'] == list(MODELS), 'Frozen matrix')
        require(cfg['feature_count'] == 16 and cfg['mutation_feature_index'] == 0, 'Feature protocol')
        paths, source_path = _inputs(cfg, data_dir)
        data = read(paths[cfg['selection_input']]); dev = read(paths[cfg['development_input']])['records']; test = read(paths[cfg['test_input']])['records']
        refs = read(paths[cfg['audit_reference']])['records']; source = read(source_path)
        require(data['task']['type'] == 'classification' and data['task']['feature_count'] == 16 and len(refs) == 256, 'Task/reference count')
        if cfg['task_id'] == 'EXT-02':
            require(data['task']['allowed_labels'] == [chr(i) for i in range(65,91)], 'Letter class set')
        require(isinstance(source['raw_sha256'],str) and reference.HEX.fullmatch(source['raw_sha256']), 'Invalid source digest')
        require(data['asset_metadata']['source_sha256'] == source['raw_sha256'], 'Source data SHA mismatch')
        rows,d0,pool = _separate(data,dev,test)
        require(len({r['index'] for r in refs}) == len(refs) and all(r['index'] in set(pool) and rows[r['index']]['id'] == r['id'] for r in refs), 'Reference membership')
        require([r['index'] for r in refs] == random.Random(cfg['seed']+160000).sample(pool,256), 'Reference sample order')
        member_inputs = [p for name,p in paths.items() if Path(name).name.lower() == 'members.json']
        require(len(member_inputs) == 1, 'Bound member list missing')
        frozen_members = read(member_inputs[0])
        require(frozen_members['source_sha256'] == source['raw_sha256'] and frozen_members['seed'] == cfg['seed'], 'Member source/seed')
        for role, records in (('D0',[rows[i] for i in d0]),('pool',[rows[i] for i in pool]),('dev',dev),('test',test)):
            require(frozen_members['roles'][role] == [{key:r[key] for key in ('source_row','id','group')} for r in records], 'Frozen members mismatch '+role)
        freeze_path = within(result, 'SELECTION_FREEZE.json'); freeze = read(freeze_path); summary = read(within(result, 'SUMMARY.json'))
        require(freeze['test_labels_parsed_before_freeze'] is False and freeze['config_sha256'] == digest(config_path), 'Config/freeze binding')
        require(freeze['development_labels_parsed_before_freeze'] is False and freeze['injection_truth_parsed_before_freeze'] is False, 'Development or injection truth read before freeze')
        require(summary['selection_sha256'] == digest(freeze_path), 'Summary/freeze binding')
        access = read(within(result,'HELDOUT_ACCESS.json'))
        require(access['selection_sha256'] == digest(freeze_path) and number(access['heldout_read_started_monotonic']) >= number(freeze['frozen_monotonic']), 'Heldout access before selection freeze')
        require((summary['task_id'],summary['dataset'],summary['seed']) == (cfg['task_id'],cfg['dataset'],cfg['seed']), 'Summary identity')
        require(summary['status'] in ('COMPLETE','COMPLETE_WITH_FAILED_ARMS'), 'Incomplete run')
        require(summary['target_fit_attempts'] == 18 and summary['detector_fit_attempts'] == 1, 'Fit attempt counts')
        stats = _verify_selections(result,cfg,data,refs,freeze)
        planned = [(model,'D0',0) for model in MODELS] + [(model,m,k) for model in MODELS for k in BUDGETS for m in METHODS]
        expected = {f'{model}__D0' if method == 'D0' else f'{model}__k{k}__{method}' for model,method,k in planned}
        arms = summary['methods']; require(isinstance(arms,list) and len(arms) == len(expected), 'Missing summary arm')
        by_arm = {a['arm']:a for a in arms}; require(len(by_arm) == len(arms) and set(by_arm) == expected, 'Arm names')
        counts = 0; failures = []; losses = {}
        for model_name,method,k in planned:
            arm = f'{model_name}__D0' if method == 'D0' else f'{model_name}__k{k}__{method}'
            root = within(result,arm); m = read(within(root,'METRICS.json')); members = read(within(root,'TRAIN_MEMBERS.json'))
            require(m == by_arm[arm], 'Arm summary differs: '+arm)
            require(m['arm'] == arm and m['model'] == model_name and m['method'] == method and m['k'] == k, 'Arm identity')
            require(m['selection_sha256'] == digest(freeze_path) and number(m['fit_started_monotonic']) >= number(access['heldout_read_started_monotonic']), 'Recorded fit before heldout access')
            require(m['status'] in ('COMPLETE','FAILED_CONVERGENCE','FAILED_ARM'), 'Unsupported arm status')
            if m['status'] in ('FAILED_CONVERGENCE','FAILED_ARM'): failures.append(arm)
            chosen = [] if k == 0 else freeze['methods'][f'k{k}__{method}']['indices']
            require(len(chosen) == k and len(set(chosen)) == k and set(chosen) <= set(pool), 'Selection budget')
            indices = d0+chosen
            require(members['indices'] == indices and members['members'] == [{key:rows[i][key] for key in ('id','source_row','group')} for i in indices], 'Train member mismatch')
            require(m['n_train'] == len(indices) and m['n_added'] == k, 'Train size')
            if m['status'] == 'FAILED_ARM':
                require(isinstance(m.get('error'),str) and bool(m['error']), 'Missing failed arm error')
                continue
            model = read(within(root,'MODEL.json'))
            _target_params(model,model_name,cfg['seed']); _same_stats(stats,model['preprocess_D0'])
            require(model['classes'] == data['task']['allowed_labels'], 'Model classes')
            predict = _make_predictor(model)
            losses[arm] = {}
            for split, records in (('dev',dev),('test',test)):
                predictions = read(within(root,split+'_PREDICTIONS.json')); detail = m[split]
                require(detail['metric'] == 'LogLoss' and detail['n'] == len(records) == len(predictions), 'Evaluation count')
                require(number(detail['evaluation_started_monotonic']) >= number(m['fit_started_monotonic']), 'Recorded evaluation before fit')
                ys=[]; ps=[]; ls=[]
                for record,prediction in zip(records,predictions):
                    require((prediction['id'],prediction['group'],prediction['y']) == (record['id'],record['group'],record['y']), 'Evaluation identity')
                    label, probs = predict(record)
                    require(prediction['prediction'] == label and prediction['classes'] == model['classes'], 'Predicted class')
                    require(len(prediction['probabilities']) == len(probs), 'Probability classes')
                    for actual,wanted in zip(prediction['probabilities'],probs): close(actual,wanted,'Probability',1e-7)
                    y=record['y']; require(y in model['classes'], 'Unknown evaluation class')
                    loss = -math.log(probs[model['classes'].index(y)])
                    close(prediction['loss'],loss,'Per-row loss',1e-7)
                    ys.append(y);ps.append(label);ls.append(loss);counts+=1
                close(detail['loss'],statistics.fmean(ls),'LogLoss',1e-7)
                close(detail['accuracy'],sum(a==b for a,b in zip(ys,ps))/len(ys),'Accuracy')
                close(detail['macro_f1'],reference.macro_f1(ys,ps,model['classes']),'Macro-F1')
                losses[arm][split]=statistics.fmean(ls)
        require(bool(failures) == (summary['status'] == 'COMPLETE_WITH_FAILED_ARMS'), 'Convergence state mismatch')
        report.update(status='REPRODUCED_WITH_FAILED_ARMS' if failures else 'REPRODUCED', task_id=cfg['task_id'], seed=cfg['seed'], synthetic_smoke=cfg['task_id']=='EXTERNAL-SMOKE', arms=len(expected), prediction_rows=counts, failed_arms=failures, selectors_replayed=8, source_sha256=data['asset_metadata']['source_sha256'], config_sha256=digest(config_path), selection_sha256=digest(freeze_path), group_separation='PASS', losses=losses, freeze_check_scope='Recorded monotonic timestamps and hashes checked; prior parsing cannot be independently proven by timestamp')
    except (reference.InvalidResult, AssertionError, ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError, OSError, UnicodeError, RecursionError, struct.error) as exc:
        report['reason'] = type(exc).__name__+': '+str(exc)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--result',required=True,type=Path);p.add_argument('--config',required=True,type=Path)
    p.add_argument('--data-dir',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args = p.parse_args(); out = args.output.resolve()
    for root in (args.result.resolve(), args.data_dir.resolve()):
        try: out.relative_to(root)
        except ValueError: pass
        else: raise ValueError('Output must be outside result and data roots')
    if out.exists() or out.is_symlink(): raise FileExistsError('Refuse to overwrite a previous report')
    report=verify(args.result,args.config,args.data_dir);body=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf8',newline='\n') as stream:stream.write(body)
    print(body,end='')
    return 0 if report['status']=='REPRODUCED' else (2 if report['status']=='REPRODUCED_WITH_FAILED_ARMS' else 1)


if __name__ == '__main__':
    sys.exit(main())
