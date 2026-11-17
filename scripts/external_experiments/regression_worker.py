"""External regression experiment worker; a monitored GO precedes data and ML.

This worker implements the fixed EXT-03/04 regression matrix or strictly synthetic smoke.  It
uses existing evidence/selection logic but does not alter the historical worker.
All outputs are new files in an exclusive worker directory, never pickle data.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
METHODS = ('random_group', 'completeness', 'rf_source_conformity_group', 'evidence_rf_group')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    with Path(path).open('x', encoding='utf8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def safe(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or Path(relative).is_absolute():
        raise ValueError('INVALID_INPUT_PATH')
    candidate = (Path(root) / relative).resolve()
    candidate.relative_to(Path(root).resolve())
    return candidate


def authorize(config_path, out_dir, data_dir):
    """Respond to the existing supervisor challenge, then wait for matching GO."""
    c = json.loads(sys.stdin.readline(65537))
    parent = psutil.Process(os.getppid())
    nonce = c.get('nonce')
    if c.get('kind') != 'COMMUNITY_CHALLENGE_V1' or not isinstance(nonce, str) or len(nonce) != 64:
        raise RuntimeError('BAD_CHALLENGE')
    if c.get('pid') != parent.pid or abs(c.get('created', 0) - parent.create_time()) > .001:
        raise RuntimeError('BAD_PARENT')
    if any(str(Path(c[k]).resolve()) != str(Path(v).resolve()) for k, v in
           [('config', config_path), ('out', out_dir), ('data_dir', data_dir)]):
        raise RuntimeError('BAD_ARGUMENT_BINDING')
    if c.get('worker_sha256') != sha(__file__) or c.get('config_sha256') != sha(config_path) or Path(out_dir).exists():
        raise RuntimeError('CHANGED_WORKER_CONFIG_OR_OUTPUT')
    for name, expected in c.get('origins', {}).items():
        spec = importlib.util.find_spec(name)
        if not spec or not spec.origin or os.path.normcase(os.path.realpath(spec.origin)) != os.path.normcase(os.path.realpath(expected)):
            raise RuntimeError('DEPENDENCY_ORIGIN_MISMATCH:' + name)
    print(json.dumps({'kind': 'COMMUNITY_READY_V1', 'nonce': nonce, 'pid': os.getpid(),
                      'created': psutil.Process().create_time(), 'worker_sha256': sha(__file__),
                      'config_sha256': sha(config_path)}), flush=True)
    go = json.loads(sys.stdin.readline(65537))
    if go.get('kind') != 'COMMUNITY_GO_V1' or go.get('nonce') != nonce:
        raise RuntimeError('NO_GO')
    if sha(__file__) != c['worker_sha256'] or sha(config_path) != c['config_sha256'] or not parent.is_running():
        raise RuntimeError('CHANGED_BEFORE_GO')
    for name, expected in c.get('origins', {}).items():
        spec = importlib.util.find_spec(name)
        if not spec or not spec.origin or os.path.normcase(os.path.realpath(spec.origin)) != os.path.normcase(os.path.realpath(expected)):
            raise RuntimeError('DEPENDENCY_ORIGIN_MISMATCH_AFTER_GO:' + name)
    return {'handshake': 'PASS', 'config_sha256': c['config_sha256'], 'worker_sha256': c['worker_sha256']}


def normalized_probabilities(values):
    """Published metric: clip below 1e-12, then normalize across classes."""
    values = [float(v) for v in values]
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('INVALID_PROBABILITIES')
    clipped = [max(1e-12, v) for v in values]
    total = math.fsum(clipped)
    return [v / total for v in clipped]


from scripts.external_experiments.regression_contract import validate_selection, validate_roles, validate_source_binding


def _tree_json(tree):
    t = tree.tree_
    return {'children_left': t.children_left.tolist(), 'children_right': t.children_right.tolist(),
            'feature': t.feature.tolist(), 'threshold': t.threshold.tolist(), 'value': t.value.tolist()}


def _member(r):
    return {'id': r['id'], 'source_row': r['source_row'], 'group': r['group']}


def _plan():
    return [(model, method, k) for model in ('ridge', 'random_forest')
            for method, k in [('D0', 0)] + [(m, k) for k in (128, 512) for m in METHODS]]


def _arm_name(model, method, k):
    return model + '__' + ('D0' if k == 0 else 'k' + str(k) + '__' + method)


def run(args):
    auth = authorize(args.config, args.out, args.data_dir)
    # No ML imports, dataset/config parsing, or output creation happen before GO.
    import copy
    import random
    import statistics
    import warnings
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from threadpoolctl import threadpool_limits
    from data_asset_poc.assessment import selection as sel
    from data_asset_poc.assessment.value_methods import transform
    from scripts.external_experiments.common import validate_config

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'AUTH.json', auth)
    write(out / 'PLAN.json', {'planned_arms': [_arm_name(*a) for a in _plan()],
                              'planned_target_fits': 18, 'planned_detector_fits': 1})
    timing = {}
    try:
        cfg = read(args.config)
        validate_config(cfg, args.data_dir)
        if cfg.get('task_id') not in ('EXT-03','EXT-04','REGRESSION-SMOKE'):
            raise ValueError('UNSUPPORTED_REGRESSION_TASK')
        config_hash = sha(args.config)
        t = time.perf_counter()
        data = read(safe(args.data_dir, cfg['selection_input']))
        refs = read(safe(args.data_dir, cfg['audit_reference']))['records']
        members_file = next(x['path'] for x in cfg['inputs'] if Path(x['path']).name.lower() == 'members.json')
        source_file = next(x['path'] for x in cfg['inputs'] if Path(x['path']).name.lower() == 'source_record.json')
        members = read(safe(args.data_dir, members_file))
        source_record = read(safe(args.data_dir, source_file))
        validate_source_binding(data,members,source_record,cfg)
        rows, d0, pool, task, evidence, audit = validate_selection(data, refs, cfg)
        timing['selection_and_reference_read_validate_seconds'] = time.perf_counter() - t
        write(out / 'AUDIT.json', {'scope': 'same-source version compliance only', 'sample': audit})
        t = time.perf_counter()
        stats = sel.fit_d0_statistics(rows, d0)
        features = sel.features(rows, pool, stats, evidence, task)
        timing['quality_feature_extraction_seconds'] = time.perf_counter() - t
        write(out / 'FEATURES.json', {'stats_from_D0_only': stats, 'features': features,
                                      'feature_names': list(sel.FEATURE_NAMES)})
        # Build the three D0 calibration copies using the same sequential RNG.
        t = time.perf_counter()
        base = [{k: rows[i][k] for k in ('id', 'source_row', 'group', 'x', 'y')} for i in d0]
        col=cfg['mutation_feature_index']
        vals = [float(r['x'][col]) for r in base if sel.finite(r['x'][col])]
        if not vals:
            raise ValueError('NO_D0_MUTATION_FEATURE_VALUES')
        upper = max(vals) + 3 * (statistics.pstdev(vals) or 1.)
        label_delta=2*(statistics.pstdev(float(r['y']) for r in base) or 1.)
        n = min(len(base), round(len(base) * 256 / len(pool)))
        rng = random.Random(cfg['seed'] + 180000)
        cal_x, cal_y, cal_samples = [], [], {}
        for kind in ('source', 'label_changed', 'feature_changed'):
            dataset = copy.deepcopy(base)
            audited = rng.sample(range(len(dataset)), n)
            ev = {}
            for j, r in enumerate(dataset):
                if kind == 'label_changed':
                    r['y'] = float(r['y']) + label_delta
                elif kind == 'feature_changed':
                    r['x'][col] = upper
                if j in audited:
                    ev[r['id']] = 'VERIFIED' if kind == 'source' else 'CONFLICT'
            cal_samples[kind] = audited
            for f in sel.features(dataset, list(range(len(dataset))), stats, ev, task):
                cal_x.append([f[name] for name in sel.FEATURE_NAMES])
                cal_y.append(int(kind == 'source'))
        timing['rf_calibration_preparation_seconds'] = time.perf_counter() - t
        t = time.perf_counter()
        with threadpool_limits(limits=2):
            detector = RandomForestClassifier(n_estimators=32, max_depth=3, random_state=cfg['seed'], n_jobs=1)
            detector.fit(cal_x, cal_y)
            probs = detector.predict_proba([[f[n] for n in sel.FEATURE_NAMES] for f in features])[:, list(detector.classes_).index(1)]
        timing['rf_fit_predict_seconds'] = time.perf_counter() - t
        scores = {f['id']: float(p) for f, p in zip(features, probs)}
        write(out / 'RF_MODEL.json', {'scope': 'D0 controlled source-version detector; not a value model',
              'calibration_rows': len(cal_y), 'calibration_sample_indices': cal_samples,
              'calibration_mutation_feature_upper': upper, 'calibration_label_delta': label_delta, 'classes': detector.classes_.tolist(),
              'params': detector.get_params(), 'trees': [_tree_json(e) for e in detector.estimators_], 'scores': scores})

        t = time.perf_counter()
        chosen = {}
        per_method_time = {}
        for k in cfg['budgets']:
            per_k = {}
            all_selections = sel.select_all(features, k, cfg['seed'], scores, timing=per_k)
            for method in METHODS:
                v = all_selections[method]
                if len(v) != k or len(set(v)) != k or not set(v) <= set(pool):
                    raise ValueError('BAD_SELECTION:' + method)
                chosen[f'k{k}__{method}'] = {'indices': v, 'ids': [rows[i]['id'] for i in v]}
                per_method_time[f'k{k}__{method}'] = per_k[method]
        timing['all_selection_strategies_seconds'] = time.perf_counter() - t
        timing['selection_seconds_by_method'] = per_method_time
        freeze = {'version': 'external-selections-v1', 'config_sha256': config_hash,
                  'frozen_unix_time': time.time(), 'frozen_monotonic': time.monotonic(),
                  'methods': chosen, 'test_labels_parsed_before_freeze': False,
                  'development_labels_parsed_before_freeze': False,
                  'injection_truth_parsed_before_freeze': False}
        write(out / 'SELECTION_FREEZE.json', freeze)
        selection_hash = sha(out / 'SELECTION_FREEZE.json')

        # Held-out labels are first parsed only after all selection files are closed.
        t = time.perf_counter()
        write(out / 'HELDOUT_ACCESS.json', {'selection_sha256': selection_hash,
              'heldout_read_started_unix': time.time(), 'heldout_read_started_monotonic': time.monotonic(),
              'scope': 'self-recorded software ordering, not a security or provenance guarantee'})
        dev = read(safe(args.data_dir, cfg['development_input']))['records']
        test = read(safe(args.data_dir, cfg['test_input']))['records']
        timing['heldout_read_seconds'] = time.perf_counter() - t
        validate_roles(data,dev,test,cfg,members)
        t = time.perf_counter()
        xdev, xtest = transform(dev, stats), transform(test, stats)
        timing['heldout_preprocess_seconds'] = time.perf_counter() - t
        results = []
        target_fit_attempts = 0
        for model_name, method, k in _plan():
            arm = _arm_name(model_name, method, k)
            directory = out / arm
            directory.mkdir(exist_ok=False)
            indices = d0 + ([] if k == 0 else chosen[f'k{k}__{method}']['indices'])
            write(directory / 'TRAIN_MEMBERS.json', {'indices': indices, 'members': [_member(rows[i]) for i in indices]})
            m = {'arm': arm, 'model': model_name, 'method': method, 'k': k,
                 'n_train': len(indices), 'n_added': k, 'selection_sha256': selection_hash,
                 'status': 'STARTED', 'warnings': []}
            t = time.perf_counter()
            xtrain = transform([rows[i] for i in indices], stats)
            ytrain = [rows[i]['y'] for i in indices]
            m['training_data_preprocess_seconds'] = time.perf_counter() - t
            model = (Ridge(alpha=1.) if model_name == 'ridge' else
                     RandomForestRegressor(n_estimators=100, max_depth=12, random_state=cfg['seed'], n_jobs=1))
            m['fit_started_unix'], m['fit_started_monotonic'] = time.time(), time.monotonic()
            write(directory / 'FIT_STARTED.json', {'fit_started_unix': m['fit_started_unix'],
                                                    'fit_started_monotonic': m['fit_started_monotonic']})
            t = time.perf_counter()
            target_fit_attempts += 1
            try:
                with warnings.catch_warnings(record=True) as ws, threadpool_limits(limits=2):
                    warnings.simplefilter('always')
                    model.fit(xtrain, ytrain)
                m['fit_seconds'] = time.perf_counter() - t
                m['warnings'] = [str(w.message) for w in ws]
                m['status'] = 'FAILED_CONVERGENCE' if any(issubclass(w.category, ConvergenceWarning) for w in ws) else 'COMPLETE'
                model_data = {'model': type(model).__name__, 'params': model.get_params(),
                              'preprocess_D0': stats}
                if model_name == 'ridge':
                    model_data.update(coef=np.asarray(model.coef_).tolist(), intercept=float(model.intercept_))
                else:
                    model_data['trees'] = [_tree_json(e) for e in model.estimators_]
                write(directory / 'MODEL.json', model_data)
                for split, erows, ex in [('dev', dev, xdev), ('test', test, xtest)]:
                    before = time.perf_counter()
                    evaluation_started_monotonic = time.monotonic()
                    with threadpool_limits(limits=2):
                        estimated = model.predict(ex)
                    truth = [float(r['y']) for r in erows]
                    predictions = [{'id':r['id'],'group':r['group'],'y':float(r['y']),
                                    'prediction':float(pred),'absolute_error':abs(float(r['y'])-float(pred))}
                                   for r,pred in zip(erows,estimated)]
                    m[split] = {'metric':'MAE', 'loss':float(mean_absolute_error(truth,estimated)),
                                'rmse':float(mean_squared_error(truth,estimated)**.5),
                                'r2':float(r2_score(truth,estimated)), 'n':len(erows),
                                'evaluation_started_monotonic':evaluation_started_monotonic,
                                'evaluation_seconds':time.perf_counter()-before}
                    write(directory/(split+'_PREDICTIONS.json'),predictions)
            except Exception as exc:
                m['fit_seconds'] = time.perf_counter() - t
                m['status'] = 'FAILED_ARM'
                m['error'] = type(exc).__name__ + ':' + str(exc)
            write(directory / 'METRICS.json', m)
            results.append(m)
            print(json.dumps({'arm': arm, 'status': m['status'], 'fit_seconds': m['fit_seconds']}), flush=True)
        # Truth is read only posthoc, after every planned model arm.
        t = time.perf_counter()
        truth = read(safe(args.data_dir, cfg['injection_truth']))['records']
        truth_map = {r['index']: r['kind'] for r in truth}
        posthoc = []
        for key, choice in chosen.items():
            posthoc.append({'selection': key, 'n': len(choice['indices']),
                            'controlled_source_modifications': sum(i in truth_map for i in choice['indices']),
                            'label_modifications': sum(truth_map.get(i) == 'label' for i in choice['indices']),
                            'feature_modifications': sum(truth_map.get(i) == 'feature' for i in choice['indices'])})
        timing['posthoc_truth_read_and_summary_seconds'] = time.perf_counter() - t
        write(out / 'QUALITY_POSTHOC.json', {'scope': 'controlled source modifications only', 'selections': posthoc})
        summary = {'status': 'COMPLETE' if all(x['status'] == 'COMPLETE' for x in results) else 'COMPLETE_WITH_FAILED_ARMS',
                   'task_id': cfg['task_id'], 'dataset': cfg['dataset'], 'seed': cfg['seed'],
                   'methods': results, 'timing': timing, 'selection_sha256': selection_hash,
                   'target_fit_attempts': target_fit_attempts, 'detector_fit_attempts': 1}
        write(out / 'SUMMARY.json', summary)
        return 0
    except Exception as exc:
        write(out / 'ERROR.json', {'status': 'FAILED_WORKER', 'error': type(exc).__name__ + ':' + str(exc),
                                  'timestamp_unix': time.time(), 'timing': timing})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--out', required=True)
    sys.exit(run(parser.parse_args()))
