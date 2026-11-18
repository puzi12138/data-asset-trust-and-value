"""External classification experiment worker; a monitored GO precedes data and ML.

This worker implements the fixed Covertype and Letter Recognition matrices
using the existing evidence/selection logic.
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
from scripts.external_experiments.common import CLASSIFICATION_DATASETS, CLASSIFICATION_TASKS, validate_config

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


def _validate_selection_input(data, reference, seed, task_id='EXTERNAL-SMOKE'):
    rows, roles, task = data['rows'], data['roles'], data['task']
    d0, pool = list(roles['D0']), list(roles['pool'])
    if task_id not in CLASSIFICATION_TASKS:
        raise ValueError('UNSUPPORTED_CLASSIFICATION_TASK')
    _, dimension, allowed = CLASSIFICATION_DATASETS.get(task_id, (None, 16, None))
    if task.get('type') != 'classification' or task.get('feature_count') != dimension:
        raise ValueError('CLASSIFICATION_DIMENSION_MISMATCH')
    labels = task.get('allowed_labels')
    if not isinstance(labels, list) or labels != sorted(set(labels)) or len(labels) < 2:
        raise ValueError('BAD_LABELS')
    if allowed is not None and labels != list(allowed):
        raise ValueError('CLASSIFICATION_LABEL_SET_MISMATCH')
    if not d0 or len(pool) < 512 or len(reference) != 256:
        raise ValueError('NOT_RUN_INSUFFICIENT_POOL_OR_REFERENCE')
    if any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(rows) for i in d0 + pool):
        raise ValueError('BAD_ROW_INDEX')
    if len(set(d0 + pool)) != len(d0) + len(pool) or set(d0 + pool) != set(range(len(rows))):
        raise ValueError('D0_POOL_INDICES_NOT_DISJOINT_AND_COMPLETE')
    if len({r['id'] for r in rows}) != len(rows) or len({r['source_row'] for r in rows}) != len(rows):
        raise ValueError('DUPLICATE_MEMBER')
    if [r['source_row'] for r in rows] != sorted(r['source_row'] for r in rows):
        raise ValueError('ROWS_NOT_SOURCE_SORTED')
    if [rows[i]['source_row'] for i in pool] != sorted(rows[i]['source_row'] for i in pool):
        raise ValueError('POOL_NOT_SOURCE_SORTED')
    if {rows[i]['group'] for i in d0} & {rows[i]['group'] for i in pool}:
        raise ValueError('D0_POOL_GROUP_LEAKAGE')
    if {rows[i]['y'] for i in d0} != set(labels):
        raise ValueError('D0_CLASSES_NOT_EQUAL_DECLARED_LABELS')
    for r in rows:
        if not isinstance(r['x'], list) or len(r['x']) != task['feature_count']:
            raise ValueError('BAD_FEATURE_VECTOR')
        if any(x is not None and (isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)) for x in r['x']):
            raise ValueError('BAD_FEATURE_VALUE')
        if r['y'] not in labels:
            raise ValueError('BAD_CANDIDATE_LABEL')
    pool_set = set(pool)
    if len({r['index'] for r in reference}) != 256:
        raise ValueError('DUPLICATE_AUDIT_REFERENCE')
    evidence, audit = {}, []
    for r in reference:
        i = r['index']
        if i not in pool_set or rows[i]['id'] != r['id']:
            raise ValueError('BAD_AUDIT_BINDING')
        state = 'VERIFIED' if rows[i]['x'] == r['x'] and rows[i]['y'] == r['y'] else 'CONFLICT'
        evidence[r['id']] = state
        audit.append({'index': i, 'id': r['id'], 'state': state,
                      'scope': 'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'})
    # Independently confirm published reference sampling from stable pool order.
    import random
    expected = random.Random(seed + 160000).sample(pool, 256)
    if [r['index'] for r in reference] != expected:
        raise ValueError('AUDIT_SAMPLE_NOT_PROTOCOL_ORDER')
    return rows, d0, pool, task, evidence, audit


def _tree_json(tree):
    t = tree.tree_
    return {'children_left': t.children_left.tolist(), 'children_right': t.children_right.tolist(),
            'feature': t.feature.tolist(), 'threshold': t.threshold.tolist(), 'value': t.value.tolist()}


def _member(r):
    return {'id': r['id'], 'source_row': r['source_row'], 'group': r['group']}


def _plan():
    return [(model, method, k) for model in ('logistic', 'random_forest')
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
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.metrics import accuracy_score, f1_score
    from threadpoolctl import threadpool_limits
    from data_asset_poc.assessment import selection as sel
    from data_asset_poc.assessment.value_methods import transform
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'AUTH.json', auth)
    write(out / 'PLAN.json', {'planned_arms': [_arm_name(*a) for a in _plan()],
                              'planned_target_fits': 18, 'planned_detector_fits': 1})
    timing = {}
    try:
        cfg = read(args.config)
        validate_config(cfg, args.data_dir)
        if cfg['task_id'] not in CLASSIFICATION_TASKS:
            raise ValueError('UNSUPPORTED_TASK_OR_MATRIX')
        config_hash = sha(args.config)
        t = time.perf_counter()
        data = read(safe(args.data_dir, cfg['selection_input']))
        refs = read(safe(args.data_dir, cfg['audit_reference']))['records']
        rows, d0, pool, task, evidence, audit = _validate_selection_input(
            data, refs, cfg['seed'], cfg['task_id'])
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
        vals = [float(r['x'][0]) for r in base if sel.finite(r['x'][0])]
        if not vals:
            raise ValueError('NO_D0_MUTATION_FEATURE_VALUES')
        upper = max(vals) + 3 * (statistics.pstdev(vals) or 1.)
        labels = task['allowed_labels']
        n = min(len(base), round(len(base) * 256 / len(pool)))
        rng = random.Random(cfg['seed'] + 180000)
        cal_x, cal_y, cal_samples = [], [], {}
        for kind in ('source', 'label_changed', 'feature_changed'):
            dataset = copy.deepcopy(base)
            audited = rng.sample(range(len(dataset)), n)
            ev = {}
            for j, r in enumerate(dataset):
                if kind == 'label_changed':
                    r['y'] = labels[(labels.index(r['y']) + 1) % len(labels)]
                elif kind == 'feature_changed':
                    r['x'][0] = upper
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
              'calibration_mutation_feature_upper': upper, 'classes': detector.classes_.tolist(),
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
        assigned_groups = {rows[i]['group'] for i in d0 + pool}
        dev_groups, test_groups = {r['group'] for r in dev}, {r['group'] for r in test}
        if assigned_groups & dev_groups or assigned_groups & test_groups or dev_groups & test_groups:
            raise ValueError('HELDOUT_GROUP_LEAKAGE')
        if not dev or not test or any(r['y'] not in labels for r in dev + test):
            raise ValueError('EVALUATION_CLASS_NOT_IN_D0')
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
            model = (LogisticRegression(C=1., solver='lbfgs', max_iter=500, tol=1e-5, random_state=cfg['seed'])
                     if model_name == 'logistic' else RandomForestClassifier(n_estimators=100, max_depth=12, random_state=cfg['seed'], n_jobs=1))
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
                              'preprocess_D0': stats, 'classes': model.classes_.tolist()}
                if model_name == 'logistic':
                    model_data.update(coef=np.asarray(model.coef_).tolist(), intercept=np.asarray(model.intercept_).tolist(),
                                      n_iter=np.asarray(model.n_iter_).tolist())
                else:
                    model_data['trees'] = [_tree_json(e) for e in model.estimators_]
                write(directory / 'MODEL.json', model_data)
                for split, erows, ex in [('dev', dev, xdev), ('test', test, xtest)]:
                    before = time.perf_counter()
                    evaluation_started_monotonic = time.monotonic()
                    with threadpool_limits(limits=2):
                        predicted_labels = model.predict(ex)
                        raw_probs = model.predict_proba(ex)
                    classes = model.classes_.tolist()
                    positions = {label: i for i, label in enumerate(classes)}
                    predictions = []
                    for j, record in enumerate(erows):
                        prob = normalized_probabilities(raw_probs[j])
                        y = record['y']
                        if y not in positions:
                            raise ValueError('EVALUATION_CLASS_NOT_IN_TRAIN_CLASSES')
                        predictions.append({'id': record['id'], 'group': record['group'], 'y': y,
                                            'prediction': str(predicted_labels[j]), 'probabilities': prob,
                                            'classes': classes, 'loss': -math.log(prob[positions[y]])})
                    truth = [r['y'] for r in erows]
                    m[split] = {'metric': 'LogLoss', 'loss': statistics.fmean(r['loss'] for r in predictions),
                                'accuracy': float(accuracy_score(truth, predicted_labels)),
                                'macro_f1': float(f1_score(truth, predicted_labels, labels=classes, average='macro', zero_division=0)),
                                'n': len(erows), 'evaluation_started_monotonic': evaluation_started_monotonic,
                                'evaluation_seconds': time.perf_counter() - before}
                    write(directory / (split + '_PREDICTIONS.json'), predictions)
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
