"""dataset dataset quality audit: separate measurement, evidence and use conditions.

This module never promotes a computable descriptive statistic into a claim of
overall quality or factual correctness.  It does not train models.  The caller
supplies an explicitly scoped asset, schema, task and provenance observations;
the result is an auditable assessment rather than an authorization decision.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import re
import statistics
import time
from typing import Any, Mapping

from data_asset_poc.assessment.binding import asset_digest, json_digest

MEASURED = 'MEASURED'
NOT_COMPUTABLE = 'NOT_COMPUTABLE'
INSUFFICIENT_REFERENCE = 'INSUFFICIENT_REFERENCE'
VERIFIED, CONFLICT, UNKNOWN = 'VERIFIED', 'CONFLICT', 'UNKNOWN'

_METRICS = {
    'classification': {'logloss', 'macro_f1', 'accuracy', 'auroc'},
    'regression': {'mae', 'rmse', 'r2'},
}


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: Any) -> bool:
    if not _numeric(value):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _matches_type(value: Any, declared: str) -> bool:
    if value is None:
        return True  # Missingness is a separate measurement.
    if declared == 'numeric':
        return _finite(value)
    if declared == 'string':
        return isinstance(value, str)
    if declared == 'boolean':
        return type(value) is bool
    if declared == 'integer':
        return type(value) is int
    return False


def _key(value: Any) -> str | None:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        return None


def _quantile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def _evidence(states: list[str]) -> dict[str, Any]:
    """Fixed-denominator count of statuses for ONE explicitly scoped claim."""
    if not states:
        return {'unit_count': 0, 'verified': 0, 'conflict': 0, 'unknown': 0,
                'verified_fraction': None, 'conflict_fraction': None,
                'unknown_fraction': None, 'coverage_fraction': None,
                'logical_truth_interval': None, 'interval_kind': 'NOT_COMPUTABLE'}
    count = Counter(states)
    n = len(states)
    v, c, u = count[VERIFIED], count[CONFLICT], count[UNKNOWN]
    assert v + c + u == n
    return {'unit_count': n, 'verified': v, 'conflict': c, 'unknown': u,
            'verified_fraction': v/n, 'conflict_fraction': c/n,
            'unknown_fraction': u/n, 'coverage_fraction': (v+c)/n,
            'logical_truth_interval': [v/n, (v+u)/n],
            'interval_kind': 'LOGICAL_BOUND_NOT_STATISTICAL_CI'}


def _combined(states: list[str]) -> str:
    if CONFLICT in states:
        return CONFLICT
    if not states or UNKNOWN in states:
        return UNKNOWN
    return VERIFIED


def _claim(state: str, description: str, reference: str | None = None) -> dict[str, Any]:
    if state not in (VERIFIED, CONFLICT, UNKNOWN):
        raise ValueError('unsupported evidence state')
    return {'evidence_state': state, 'claim': description, 'reference': reference}


def _external_claims(source_evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Carry explicitly scoped external provenance attestations.

    A descriptor alone never authenticates a publisher, licence or original
    file. VERIFIED requires a cited assessment supplied by the caller; this
    function checks that the assessment contains an evidence handle, but does
    not independently authenticate third-party testimony. The audit runner
    independently checks historical receipt and snapshot hashes before making
    such assessments. This representation is never a decision authorization.
    """
    claims = {
        'institution_identified': 'Publishing institution is identified from the referenced source',
        'official_publication_address': 'Publication address is documented as the official repository endpoint',
        'license_identified': 'A licence is stated in the referenced release record',
        'upstream_version_traceable': 'An upstream release version is identified, not merely a derived local snapshot',
        'source_file_matches_release': 'Original source file matches a publisher-stated checksum',
    }
    results = {}
    evidence = source_evidence or {}
    for key, statement in claims.items():
        item = evidence.get(key)
        if item is None:
            results[key] = _claim(UNKNOWN, statement)
            continue
        if not isinstance(item, dict):
            raise ValueError(f'provenance assessment for {key} must be a mapping')
        state = item.get('state', UNKNOWN)
        reference = item.get('reference')
        if state not in (VERIFIED, CONFLICT, UNKNOWN):
            raise ValueError('invalid provenance evidence state')
        # Citation is required for both a support and a contradiction; a bare
        # boolean, hash or provider assertion alone is not enough.
        if state != UNKNOWN and (not isinstance(reference, str) or not reference.strip()):
            state = UNKNOWN
        results[key] = _claim(state, statement, reference)
        results[key]['scope_note'] = str(item.get('scope_note', 'external assessment, not factual data certification'))
    return results


def _dimension(claim: str, measurement: str, state: str, metric: Mapping[str, Any],
               units: list[str], scope: str, seconds: float, details: Any = None) -> dict[str, Any]:
    return {'object': 'dataset', 'claim': claim, 'measurement_status': measurement,
            'evidence_state': state, 'metrics': dict(metric), 'evidence': _evidence(units),
            'aggregation': 'explicit claim-specific unit counts; no cross-dimension weighted credit score',
            'scope': scope, 'cost_seconds': seconds, 'details': details}


def _task_evidence(rows: list[dict], schema: Mapping[str, Any], task: Mapping[str, Any],
                   target_reference: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compare an explicit task to schema, outcome, metric and model needs.

    Dataset compatibility is distinct from predictive benefit.  Target
    representativeness remains UNKNOWN without a separately supplied target
    population and a frozen claim/acceptance criterion. No D0 range gate is
    called task relevance.
    """
    out = {}
    def compare(key, left, right, label):
        state = UNKNOWN if left is None or right is None else (VERIFIED if left == right else CONFLICT)
        out[key] = _claim(state, label)

    compare('task_type', schema.get('task_type'), task.get('task_type'), 'Dataset and target task types match')
    compare('label_kind', schema.get('label_kind'), task.get('label_kind'), 'Dataset and task label kinds match')
    compare('target_semantics', schema.get('target_id'), task.get('target_id'), 'Declared target variable matches the requested target')

    # Label-kind declarations do not themselves prove that row values conform.
    label_kind = task.get('label_kind')
    label_states = []
    for row in rows:
        y = row.get('y')
        if y is None or label_kind not in ('continuous', 'categorical'):
            label_states.append(UNKNOWN)
        elif label_kind == 'continuous':
            label_states.append(VERIFIED if _finite(y) else CONFLICT)
        else:
            is_label = isinstance(y, str) and bool(y) or _finite(y)
            label_states.append(VERIFIED if is_label else CONFLICT)
    out['observed_label_type'] = _claim(_combined(label_states), 'Observed non-missing labels satisfy the specified label type')
    out['observed_label_type']['evidence'] = _evidence(label_states)

    names = schema.get('feature_names')
    required = task.get('required_features')
    if not isinstance(names, list) or not isinstance(required, list):
        state = UNKNOWN
        missing = None
    else:
        missing = sorted(set(required) - set(names))
        state = CONFLICT if missing else VERIFIED
    out['required_features'] = _claim(state, 'Required feature identifiers are present')
    out['required_features']['missing'] = missing

    kind = task.get('task_type')
    metric = task.get('metric')
    if kind not in _METRICS or not isinstance(metric, str) or not metric:
        state = UNKNOWN
    else:
        state = VERIFIED if metric.lower() in _METRICS[kind] else CONFLICT
    out['evaluation_metric'] = _claim(state, 'Requested metric is structurally applicable to the task type')
    out['evaluation_metric']['does_not_prove_metric_computable'] = True

    model = task.get('model')
    prep = task.get('preprocessing')
    if not isinstance(model, dict) or not isinstance(model.get('family'), str) or not model['family'].strip():
        out['model_input'] = _claim(UNKNOWN, 'Specified model accepts data types and missingness treatment')
    elif type(model.get('requires_numeric')) is not bool or type(model.get('handles_missing')) is not bool:
        out['model_input'] = _claim(UNKNOWN, 'Specified model accepts data types and missingness treatment')
    else:
        p = len(names) if isinstance(names, list) else None
        types_known = isinstance(schema.get('feature_types'), list) and len(schema['feature_types']) == p
        wrong_type = types_known and model['requires_numeric'] and any(x != 'numeric' for x in schema['feature_types'])
        missing_values = any(v is None for row in rows for v in row.get('x', []))
        missing_compatible = model['handles_missing'] or not missing_values or (
            isinstance(prep, dict) and prep.get('missing') in {'median_train_only', 'mean_train_only', 'explicit_model_handling'})
        if wrong_type or not missing_compatible:
            state = CONFLICT
        elif not types_known:
            state = UNKNOWN
        else:
            state = VERIFIED
        out['model_input'] = _claim(state, 'Declared feature types and missingness treatment satisfy stated model input requirements')

    constraints = task.get('constraints')
    if not isinstance(constraints, dict) or type(constraints.get('min_rows')) is not int or constraints['min_rows'] < 0:
        state = UNKNOWN
    else:
        state = VERIFIED if len(rows) >= constraints['min_rows'] else CONFLICT
    out['sample_constraint'] = _claim(state, 'The stated minimum number of rows is met')

    # A reference may describe target data, but mere existence is not proof of
    # representativeness. Only explicit comparable scalar distributions and a
    # declared tolerance generate a narrow, reproducible matching claim.
    target = target_reference or {}
    ref_id, ref_vals, column, tolerance = (target.get(k) for k in ('id', 'values', 'feature', 'ks_max'))
    if (not isinstance(ref_id, str) or not ref_id.strip() or not isinstance(ref_vals, list)
        or not ref_vals or not isinstance(column, str) or not isinstance(names, list)
        or column not in names or not _finite(tolerance) or not 0 <= tolerance <= 1
        or any(not _finite(x) for x in ref_vals)):
        out['target_distribution'] = _claim(UNKNOWN, 'A stated target feature distribution satisfies a predeclared distance criterion')
        out['target_distribution']['measurement_status'] = INSUFFICIENT_REFERENCE
        out['target_distribution']['ks_distance'] = None
    else:
        j = names.index(column)
        vals = [r['x'][j] for r in rows if isinstance(r.get('x'), list) and len(r['x']) > j and _finite(r['x'][j])]
        if not vals:
            out['target_distribution'] = _claim(UNKNOWN, 'A stated target feature distribution satisfies a predeclared distance criterion', ref_id)
            out['target_distribution']['measurement_status'] = NOT_COMPUTABLE
            out['target_distribution']['ks_distance'] = None
        else:
            # Two-sample empirical KS distance; no sampling-test p-value or
            # population-representativeness claim is made.
            a, b = sorted(vals), sorted(ref_vals)
            ia = ib = 0; distance = 0.0
            for x in sorted(set(a+b)):
                while ia < len(a) and a[ia] <= x: ia += 1
                while ib < len(b) and b[ib] <= x: ib += 1
                distance = max(distance, abs(ia/len(a)-ib/len(b)))
            out['target_distribution'] = _claim(VERIFIED if distance <= tolerance else CONFLICT,
                'One specified feature satisfies a stated empirical KS distance limit', ref_id)
            out['target_distribution'].update(measurement_status=MEASURED, ks_distance=distance,
                                              threshold=tolerance, feature=column,
                                              scope_note='One feature of a supplied reference only; not population representativeness or utility.')
    return out


def audit_dataset(asset: Mapping[str, Any], schema: Mapping[str, Any], task: Mapping[str, Any],
                  *, expected_asset_digest: str | None = None,
                  file_integrity: Mapping[str, Any] | None = None,
                  source_evidence: Mapping[str, Any] | None = None,
                  external_reference: Mapping[str, Any] | None = None,
                  target_reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate seven asset dimensions without reading controlled truth labels.

    ``asset`` conforms to binding.asset_digest's exact schema; a user-supplied
    expected hash must originate from an earlier frozen artifact. Each claim's
    evidence denominator is clearly identified and never turned into one
    absolute asset score. ``file_integrity`` represents a caller-side byte
    comparison; it does not establish original publication or content truth.
    """
    started = time.perf_counter(); rows = asset.get('records', [])
    if not isinstance(rows, list):
        raise ValueError('asset records must be a list')
    n = len(rows); d = schema.get('feature_names'); p = len(d) if isinstance(d, list) else 0
    by_dimension: dict[str, Any] = {}

    t = time.perf_counter(); sources = _external_claims(source_evidence)
    states = [v['evidence_state'] for v in sources.values()]
    by_dimension['source'] = _dimension('All five named provenance claims are supported',
        MEASURED if any(s != UNKNOWN for s in states) else INSUFFICIENT_REFERENCE,
        _combined(states), {'claims_evaluated': len(states)}, states,
        'Five distinct provenance claims; fractions are over claims, not rows. External attestations are caller-assessed.',
        time.perf_counter()-t, sources)

    t = time.perf_counter()
    try:
        current_digest = asset_digest(dict(asset)); digest_measurement = MEASURED; digest_error = None
    except (ValueError, TypeError, KeyError) as exc:
        current_digest = None; digest_measurement = NOT_COMPUTABLE; digest_error = type(exc).__name__ + ': ' + str(exc)
    binding_state = UNKNOWN if expected_asset_digest is None or current_digest is None else (VERIFIED if current_digest == expected_asset_digest else CONFLICT)
    schema_binding = UNKNOWN if asset.get('schema_sha256') is None else (VERIFIED if asset['schema_sha256'] == json_digest(dict(schema)) else CONFLICT)
    task_binding = UNKNOWN if asset.get('task_sha256') is None else (VERIFIED if asset['task_sha256'] == json_digest(dict(task)) else CONFLICT)
    finfo = file_integrity or {}
    if (finfo.get('comparison_performed') is True
        and all(isinstance(finfo.get(k), str) and re.fullmatch(r'[0-9a-f]{64}',finfo[k])
                for k in ('expected_sha256','actual_sha256'))):
        file_state = VERIFIED if finfo['expected_sha256'] == finfo['actual_sha256'] else CONFLICT
    else:
        file_state = UNKNOWN
    # An inventory of bytes is not an observation of transport completeness.
    int_claims = {'asset_snapshot_match': _claim(binding_state, 'Canonical asset digest matches an earlier frozen asset snapshot'),
                  'schema_binding': _claim(schema_binding, 'Asset schema digest matches supplied schema'),
                  'task_binding': _claim(task_binding, 'Asset task digest matches supplied task'),
                  'file_snapshot_match': _claim(file_state, 'File bytes match the referenced frozen file hash', finfo.get('reference')),
                  'transport_complete': _claim(UNKNOWN, 'Original transport was complete')}
    int_states = [v['evidence_state'] for v in int_claims.values()]
    by_dimension['integrity'] = _dimension('Five named integrity assertions are supported',
        digest_measurement, _combined(int_states), {'canonical_asset_sha256': current_digest,
        'digest_error': digest_error}, int_states,
        'Fractions over distinct claims; snapshot match proves byte/record binding only.', time.perf_counter()-t, int_claims)

    t = time.perf_counter(); schema_states = []; nulls = absent = nonfinite = wrong = 0; completeness = []
    declared_types = schema.get('feature_types')
    types_known = (isinstance(declared_types,list) and len(declared_types)==p
                   and all(x in ('numeric','string','boolean','integer') for x in declared_types))
    for row in rows:
        x = row.get('x')
        if not isinstance(x, list) or not p:
            schema_states.append(UNKNOWN if not p else CONFLICT)
            continue
        length_ok = len(x) == p
        absent += max(0,p-len(x)); nulls += sum(v is None for v in x[:p])
        row_nonfinite = sum(_numeric(v) and not _finite(v) for v in x[:p])
        nonfinite += row_nonfinite
        row_wrong = sum(v is not None and not _matches_type(v,declared_types[j])
                        and not (_numeric(v) and not _finite(v)) for j,v in enumerate(x[:p])) if types_known else 0
        wrong += row_wrong
        if not length_ok or row_nonfinite or row_wrong:
            schema_states.append(CONFLICT)
        else:
            schema_states.append(VERIFIED if types_known else UNKNOWN)
        completeness.append(sum(v is not None for v in x[:p])/p)
    valid_rows = sum(s == VERIFIED for s in schema_states)
    total_slots = n*p
    present_slots = total_slots-absent-nulls
    metrics = {'feature_count': p, 'schema_conformity_fraction': valid_rows/n if n else None,
               'missing_value_cells': nulls, 'absent_field_slots': absent,
               'missing_cell_fraction': nulls/total_slots if total_slots else None,
               'type_mismatch_cells': wrong, 'nonfinite_cells': nonfinite,
               'present_cell_type_conformity_fraction': (present_slots-wrong-nonfinite)/present_slots if present_slots and types_known else None,
               'type_evaluation_coverage_fraction': present_slots/total_slots if total_slots and types_known else None,
               'row_completeness_mean': statistics.fmean(completeness) if completeness else None,
               'row_completeness_median': statistics.median(completeness) if completeness else None,
               'row_completeness_p05': _quantile(completeness,.05),
               'missing_row_fraction': sum(v < 1 for v in completeness)/len(completeness) if completeness else None,
               'format_list_fraction': sum(isinstance(r.get('x'),list) for r in rows)/n if n else None,
               'domain_constraint_violations': 'NOT_EVALUATED_NO_DECLARED_CONSTRAINTS'}
    by_dimension['structure'] = _dimension('Each record satisfies the declared feature count and types of present feature values',
        MEASURED if n and p else NOT_COMPUTABLE, _combined(schema_states), metrics, schema_states,
        'A missing value is separately counted; it is not automatically a schema type or length conflict.', time.perf_counter()-t)

    t = time.perf_counter(); by_x = defaultdict(list); by_full = Counter(); invalid_keys = 0
    for row in rows:
        key = _key(row.get('x')); full = _key([row.get('x'),row.get('y')])
        if key is None or full is None:
            invalid_keys += 1; continue
        by_x[key].append(row.get('y')); by_full[full] += 1
    duplicates = [ys for ys in by_x.values() if len(ys) > 1]
    disagreements = [ys for ys in duplicates if len({_key(y) for y in ys}) > 1]
    classes = Counter(str(r.get('y')) for r in rows) if schema.get('task_type') == 'classification' else None
    target_values = [float(r['y']) for r in rows if _finite(r.get('y'))] if schema.get('task_type') == 'regression' else []
    content_metrics = {'exact_feature_duplicate_groups': len(duplicates),
                       'duplicate_feature_different_target_groups': len(disagreements),
                       'different_targets_are_not_automatic_errors': True}
    by_dimension['content'] = _dimension('Each record label matches independent factual truth',
        INSUFFICIENT_REFERENCE, UNKNOWN, content_metrics, [UNKNOWN]*n,
        'No independent factual oracle was supplied; conflicting outcomes at matching features can be valid.', time.perf_counter()-t)

    t = time.perf_counter(); dist_metrics = {'class_counts': dict(classes) if classes is not None else None,
        'target_median': statistics.median(target_values) if target_values else None,
        'target_p05': _quantile(target_values,.05), 'target_p95': _quantile(target_values,.95),
        'unique_feature_fraction': len(by_x)/(n-invalid_keys) if n > invalid_keys else None,
        'feature_duplicate_fraction': (n-invalid_keys-len(by_x))/(n-invalid_keys) if n > invalid_keys else None,
        'rows_with_unrepresentable_key': invalid_keys}
    feature_distributions = []
    for j,name in enumerate(d if isinstance(d,list) else []):
        observed = [float(r['x'][j]) for r in rows if isinstance(r.get('x'),list)
                    and len(r['x']) > j and _finite(r['x'][j])]
        feature_distributions.append({'feature':name,'finite_n':len(observed),
            'mean':statistics.fmean(observed) if observed else None,
            'median':statistics.median(observed) if observed else None,
            'p05':_quantile(observed,.05),'p95':_quantile(observed,.95),
            'minimum':min(observed) if observed else None,'maximum':max(observed) if observed else None,
            'distinct_count':len(set(observed))})
    dist_metrics['feature_distributions'] = feature_distributions
    by_dimension['diversity'] = _dimension('Sample adequately represents a defined target population',
        MEASURED if n > invalid_keys else NOT_COMPUTABLE, UNKNOWN, dist_metrics, [UNKNOWN],
        'Only within-sample diversity was measured; no target sampling frame or representativeness threshold supplied.', time.perf_counter()-t)

    t = time.perf_counter(); ref = external_reference or {}; ref_rows = ref.get('rows')
    if (isinstance(ref_rows,list) and ref.get('reference_id') and ref_rows
        and isinstance(ref.get('dataset_id'),str) and ref['dataset_id'] != asset.get('dataset_id')):
        ref_keys = {_key(r.get('x')) for r in ref_rows}; ref_keys.discard(None)
        measured = MEASURED; overlap = sum(_key(r.get('x')) in ref_keys for r in rows if _key(r.get('x')) is not None)
        denominator = n-invalid_keys; overlap_fraction = overlap/denominator if denominator else None
    else:
        measured = INSUFFICIENT_REFERENCE; overlap = overlap_fraction = None
    by_dimension['novelty'] = _dimension('Data are sufficiently novel compared with an external reference',
        measured, UNKNOWN, {'internal_exact_feature_unique_fraction': dist_metrics['unique_feature_fraction'],
        'internal_full_record_duplicate_excess': sum(max(0,c-1) for c in by_full.values()),
        'external_exact_overlap_count': overlap, 'external_exact_overlap_fraction': overlap_fraction,
        'approximate_duplicate': 'NOT_EVALUATED', 'information_gain': 'NOT_EVALUATED'}, [UNKNOWN],
        'Internal uniqueness is measurable; external novelty is UNKNOWN without a defined reference and sufficiency criterion.', time.perf_counter()-t)

    t = time.perf_counter(); checks = _task_evidence(rows,schema,task,target_reference)
    task_states = [v['evidence_state'] for v in checks.values()]
    by_dimension['task'] = _dimension('All stated task, target, metric, model and distribution conditions are supported',
        MEASURED if any(s != UNKNOWN for s in task_states) else INSUFFICIENT_REFERENCE,
        _combined(task_states), {'compatible_checks': task_states.count(VERIFIED),
          'conflicting_checks': task_states.count(CONFLICT), 'unknown_checks': task_states.count(UNKNOWN)}, task_states,
        'Compatibility for this explicit task; not a prediction of downstream training utility.', time.perf_counter()-t, checks)

    return {'schema_version': 'DATASET_QUALITY_V1', 'dataset_id': asset.get('dataset_id'), 'n': n,
            'dimensions': by_dimension, 'evaluation_seconds': time.perf_counter()-started,
            'global_quality_score': 'NOT_COMPUTED', 'training_utility_prediction': 'NOT_FITTED',
            'interpretation': 'Evidence and measurements are scoped to explicit claims. UNKNOWN is neither failure nor certification.'}
