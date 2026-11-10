"""Local dataset workbench around trust and task-value services.

This is not the blind evaluator. Only pinned demo/candidate files have a
configured A-class label reference; arbitrary imports never acquire one.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal
import uuid

import pandas as pd
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, conint
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..value_app.gateway import create_value_gateway
from ..value_app.registry import EXACT_KEYS
from ..dataset_app.gateway import MAX_BYTES, read_csv
from ..signed_delivery.security import utcnow
from ..trustworthiness import evaluate_dataset
from ..reference_workbench.budget import PILOT_PER_STRATUM, STRATEGIES, run_budget_audit

ID = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class WorkbenchInput(BaseModel):
    kind: Literal['demo', 'measured', 'import']
    identity: str
    budget: conint(strict=True, ge=0, le=1000) = 50
    seed: conint(strict=True, ge=0, le=4294967295) = 101
    strategy: Literal['srs', 'fixed_random', 'proportional', 'risk_priority', 'candidate'] = 'candidate'

    class Config:
        extra = 'forbid'


def _load_json(path: Path):
    def distinct(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('DUPLICATE_JSON_KEY')
            result[k] = v
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=distinct,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def _snapshot(setting):
    if not setting:
        return None
    path = Path(setting['path'])
    if path.is_symlink() or sha256(path.read_bytes()) != setting['sha256']:
        raise ValueError('WORKBENCH_SNAPSHOT_HASH_MISMATCH')
    return _load_json(path)


def joint_decision(audit: dict, value: dict) -> dict:
    """Formal factual uncertainty and A-reference exploration stay separate."""
    content = audit.get('content_decision', 'NEED_MORE_EVIDENCE')
    if audit.get('decision') == 'EVIDENCE_INVALID':
        content = 'EVIDENCE_INVALID'
    elif audit.get('decision') == 'NEED_MORE_EVIDENCE':
        content = 'NEED_MORE_EVIDENCE'
    validation = (value.get('record') or {}).get('validation', {})
    status = validation.get('value_status', 'NOT_EVALUATED')
    lower = validation.get('ci_lower')
    if content in {'REJECT_CONFIRMED', 'EVIDENCE_INVALID'}:
        exploration = 'DO_NOT_USE_UNDER_CURRENT_REQUIREMENTS'
    elif status == 'NOT_EVALUATED':
        exploration = 'VALUE_NOT_EVALUATED'
    elif content == 'ACCEPT_CONDITIONALLY' and isinstance(lower, (int, float)) and lower > 0:
        exploration = 'CONSIDER_EXPLORATORY_USE'
    elif content != 'ACCEPT_CONDITIONALLY':
        exploration = 'DEFER_AND_AUDIT_MORE'
    elif validation.get('mean_utility', 0) <= 0:
        exploration = 'DO_NOT_USE_FOR_THIS_TASK'
    else:
        exploration = 'DEFER_VALUE_UNCERTAINTY'
    return {'formal_use': 'DEFER_NO_INDEPENDENT_FACTUAL_AUDIT', 'exploratory_use': exploration,
            'trust_multiplier_applied': False, 'market_price': 'NOT_EVALUATED',
            'scope': 'Local assessment only; reference conformance is not independent factual certification.'}


def markdown_report(document: dict) -> str:
    audit, value = document['audit'], document['value']
    dataset, joint = document['dataset'], document['joint_decision']
    return ('# 数据集可信—价值联合评估报告\n\n'
        f"- 报告：{document['report_id']}\n- 模式：{document['mode']} / 本地评估\n"
        f"- 数据集：{dataset['dataset_id']} / {dataset['version']}\n"
        f"- SHA-256：{dataset['content_hash']}\n- 规模：{dataset['row_count']}行 × {dataset['column_count']}列\n\n"
        '## 证据与判断\n\n'
        f"- 来源状态：{document['source_evidence']['status']}（哈希绑定不证明事实）\n"
        f"- 内容参照判断：{audit.get('content_decision')}\n"
        f"- 样本：{audit.get('n', 0)}个可比 / {audit.get('e', 0)}个分歧；未解决 {audit.get('unresolved', 0)}\n"
        f"- 错误率区间：{audit.get('error_rate_interval', 'UNKNOWN')}\n"
        f"- 审计成本：{audit.get('audit_cost', 0)}（实验单位：每记录一次核验）\n\n"
        '固定预算；参照范围以本报告证据字段为准，没有独立参考时保持未知。区间不是现实真实性概率，不能据反复预算择优推断。\n\n'
        '## 条件任务价值与使用建议\n\n'
        f"- 任务价值：{value.get('status')}；未匹配精确版本时为NOT_EVALUATED\n"
        f"- 正式用途：{joint['formal_use']}\n- 探索建议：{joint['exploratory_use']}\n"
        '- 不将可信分乘任务效用，不输出货币市场价格；测试结果仅事后评价。\n\n'
        '## 完整方法、配置、样本与证据\n\n```json\n'
        + json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n```\n')


def create_workbench_gateway(config_file: Path | str):
    cfg_path = Path(config_file).resolve()
    cfg = _load_json(cfg_path)
    app = create_value_gateway(cfg_path)
    app.title = 'Reference_workbench local dataset evaluation workbench'
    root, workspace = Path(cfg['workbench_root']).resolve(), Path(cfg['workspace']).resolve()
    manifest = _snapshot(cfg['workbench_manifest'])
    public_trust = _snapshot(cfg.get('workbench_trust'))
    if cfg.get('workbench_reveal_receipt'):
        reveal = Path(cfg['workbench_reveal_receipt'])
        if not reveal.is_file() or _load_json(reveal).get('status') != 'PASS':
            raise ValueError('REVEALED_DEMO_REQUIRES_ACTUAL_REVEAL_RECEIPT')
    value_registry = _snapshot(cfg.get('workbench_value_registry'))
    cases = {row['case_id']: row for row in manifest.get('cases', [])}
    if len(cases) != len(manifest.get('cases', [])):
        raise ValueError('DUPLICATE_WORKBENCH_CASE')
    values = (value_registry or {}).get('records', [])
    if any(not all(key in row for key in EXACT_KEYS) for row in values):
        raise ValueError('WORKBENCH_VALUE_NINE_EXACT_KEYS_REQUIRED')
    if len({tuple(row[key] for key in EXACT_KEYS) for row in values}) != len(values):
        raise ValueError('DUPLICATE_WORKBENCH_VALUE_NINE_KEYS')
    reports = workspace / 'workbench_reports'
    reports.mkdir(exist_ok=True)
    if reports.is_symlink():
        raise ValueError('WORKBENCH_REPORT_DIRECTORY_MUST_NOT_BE_SYMLINK')

    def file_at(relative: str) -> Path:
        p = root / relative
        if p.is_symlink() or not p.resolve().is_relative_to(root):
            raise HTTPException(409, 'WORKBENCH_FILE_PATH_BOUNDARY_FAILED')
        if not p.is_file():
            raise HTTPException(409, 'WORKBENCH_PINNED_FILE_UNAVAILABLE')
        return p

    def bytes_at(relative: str, expected: str | None):
        p = file_at(relative)
        with p.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, 'WORKBENCH_CSV_EXCEEDS_LIMIT')
        if expected is not None and sha256(raw) != expected.removeprefix('sha256:'):
            raise HTTPException(409, 'WORKBENCH_PINNED_BYTES_CHANGED')
        return raw

    def read_report(identity):
        if not ID.fullmatch(identity):
            raise HTTPException(400, 'INVALID_WORKBENCH_REPORT_ID')
        path = reports / (identity + '.json')
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, 'WORKBENCH_REPORT_NOT_FOUND')
        return _load_json(path)

    def provenance(row):
        if row.get('signature') is None:
            return 'UNKNOWN'
        if not public_trust:
            return 'UNKNOWN'
        try:
            payload = {k: v for k, v in row.items() if k != 'signature'}
            raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_trust['public_key'], validate=True))
            key.verify(base64.b64decode(row['signature'], validate=True), raw)
            return 'VERIFIED'
        except Exception:
            return 'INVALID'

    @app.get('/api/workbench/options')
    def options():
        return {'mode': 'LOCAL_ASSESSMENT', 'epsilon': .05, 'alpha': .05,
            'strategies': list(STRATEGIES), 'budgets': [20, 50, 100],
            'manifest_sha256': cfg['workbench_manifest']['sha256'],
            'cases': [{**{k: r.get(k) for k in ('case_id', 'dataset_id', 'version', 'content_hash', 'row_count',
                       'name', 'source_description', 'evidence_class')}, 'provenance_status': provenance(r)}
                      for r in cases.values()],
            'measured': [{'identity': str(i), **{k: row[k] for k in EXACT_KEYS},
                         'loss_unit': row.get('loss_unit'), 'task_type': row.get('task_type')}
                        for i, row in enumerate(values)],
            'value_registry_status': 'READY' if value_registry else 'NOT_CONFIGURED',
            'value_registry_sha256': cfg.get('workbench_value_registry', {}).get('sha256'),
            'limits': {'bytes': MAX_BYTES, 'rows': 20000, 'columns': 128},
            'notes': ['未提供独立参考时保持未知。', '参照符合性不证明现实事实。',
                      '每个预算是独立研究情境，不能择优停止。']}

    @app.post('/api/workbench/assess')
    def assess(p: WorkbenchInput):
        use_oracle = False
        if p.kind == 'demo':
            if p.identity not in cases:
                raise HTTPException(404, 'WORKBENCH_CASE_NOT_FOUND')
            row = cases[p.identity]
            raw = bytes_at(row['path'], row['content_hash'])
            dataset_id, version = row.get('dataset_id', p.identity), row['version']
            use_oracle = bool(cfg.get('workbench_demo_oracle'))
            ref_raw = (bytes_at(row['reference_path'], row['reference_hash'])
                       if row.get('reference_path') and not use_oracle else None)
            frame = read_csv(raw)
            target, source = row.get('target', 'Diagnosis'), provenance(row)
            reference = True if use_oracle else read_csv(ref_raw) if ref_raw else None
            value = {'status': 'NOT_EVALUATED', 'record': None,
                     'reason': 'NO_EXACT_NINE_KEY_TASK_MEASUREMENT_FOR_THIS_DEMO_VERSION'}
        elif p.kind == 'measured':
            if not p.identity.isdigit() or not 0 <= int(p.identity) < len(values):
                raise HTTPException(404, 'WORKBENCH_MEASURED_ASSET_NOT_FOUND')
            row = values[int(p.identity)]
            raw = bytes_at(row['asset_csv_path'], row['content_hash'])
            frame = read_csv(raw)
            dataset_id, version, target = row['asset_id'], row['version'], row.get('target_column', 'y')
            source = 'LOCAL_HASH_BOUND_PUBLIC_DERIVATION'
            reference = None
            if row.get('audit_reference_path'):
                reference = read_csv(bytes_at(row['audit_reference_path'], row['audit_reference_hash']))
            value = {'status': row['validation']['value_status'], 'record': copy.deepcopy(row),
                     'selection_basis': 'FROZEN_VALIDATION_ONLY', 'test_results_use': 'POSTHOC_EVALUATION_ONLY'}
        else:
            if not ID.fullmatch(p.identity):
                raise HTTPException(400, 'INVALID_IMPORT_ID')
            folder = workspace / 'dataset_imports'
            metadata = folder / (p.identity + '.json')
            datafile = folder / (p.identity + '.csv')
            if metadata.is_symlink() or datafile.is_symlink() or not metadata.is_file() or not datafile.is_file():
                raise HTTPException(404, 'WORKBENCH_IMPORT_NOT_FOUND')
            row = _load_json(metadata)
            raw = datafile.read_bytes()
            if sha256(raw) != row['content_hash'].removeprefix('sha256:'):
                raise HTTPException(409, 'WORKBENCH_IMPORTED_BYTES_CHANGED')
            frame = read_csv(raw, row.get('separator', ','))
            dataset_id, version = row['dataset_id'], row['version']
            target, source, reference = None, 'UNKNOWN', None
            value = {'status': 'NOT_EVALUATED', 'record': None,
                     'reason': 'LOCAL_IMPORT_HAS_NO_EXACT_TASK_OR_INDEPENDENT_AUDIT'}
        if len(frame) == 0:
            raise HTTPException(400, 'WORKBENCH_EMPTY_DATASET')
        assessment = evaluate_dataset(frame, dataset_id=dataset_id, version=version, raw_bytes=raw)
        dataset = {'dataset_id': dataset_id, 'version': version, 'content_hash': sha256(raw),
                   'row_count': len(frame), 'column_count': len(frame.columns),
                   'schema': [{'name': c, 'dtype': str(frame[c].dtype)} for c in frame]}
        if reference is None or target not in frame or (not use_oracle and target not in reference):
            audit = {'decision': 'NEED_MORE_EVIDENCE', 'content_decision': 'NEED_MORE_EVIDENCE',
                     'n': 0, 'e': 0, 'unresolved': 0, 'audit_cost': 0, 'observations': [],
                     'error_rate_interval': None, 'reasons': ['NO_CONFIGURED_VALID_REFERENCE_FOR_EXACT_DATASET'],
                     'assumptions': [], 'epsilon': .05, 'alpha': .05}
        else:
            if not use_oracle and len(reference) != len(frame):
                raise HTTPException(409, 'WORKBENCH_REFERENCE_ROW_COUNT_MISMATCH')
            if not use_oracle and 'row_id' in frame and 'row_id' in reference and not frame.row_id.equals(reference.row_id):
                raise HTTPException(409, 'WORKBENCH_REFERENCE_ROW_BINDING_MISMATCH')
            feature = 'radius1' if 'radius1' in frame else 'radius_mean'
            strata = (pd.to_numeric(frame[feature], errors='coerce').gt(14).astype(int).tolist()
                      if feature in frame else [0] * len(frame))
            # The kernel requires stratum 0 to exist. A single high stratum is
            # the same one-population design, with its sole layer renamed 0.
            if set(strata) == {1}:
                strata = [0] * len(strata)
            def direct_reference(position):
                observed, expected = frame.iloc[position][target], reference.iloc[position][target]
                if pd.isna(observed) or pd.isna(expected):
                    return 'UNKNOWN'
                return 'PASS' if str(observed) == str(expected) else 'ERROR'
            actual_budget = min(p.budget, len(frame))
            minimum_pilot = sum(min(PILOT_PER_STRATUM, strata.count(h)) for h in set(strata))
            if p.strategy == 'candidate' and actual_budget < minimum_pilot:
                raise HTTPException(422, f'CANDIDATE_FIXED_PILOT_REQUIRES_AT_LEAST_{minimum_pilot}_AUDITS; '
                                        'choose SRS or supply the fixed pilot budget')
            server = None
            try:
                if use_oracle:
                    server = subprocess.Popen([sys.executable, '-B', cfg['workbench_demo_oracle']],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8', cwd=root,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    def oracle(position):
                        server.stdin.write(json.dumps({'case_id': p.identity, 'position': position}) + '\n')
                        server.stdin.flush()
                        answer = json.loads(server.stdout.readline())
                        if answer.get('error') or answer.get('case_id') != p.identity or answer.get('position') != position:
                            raise ValueError('WORKBENCH_ORACLE_BINDING_FAILED')
                        return answer['outcome']
                else:
                    oracle = direct_reference
                audit = run_budget_audit(strata=strata, budget=actual_budget, seed=p.seed,
                    strategy=p.strategy, audit=oracle, epsilon=.05, alpha=.05,
                    reference_error_upper=row.get('reference_error_upper', 0.),
                    reference_risk_beta=row.get('reference_risk_beta', 0.))
            finally:
                if server is not None:
                    server.stdin.close(); server.terminate(); server.wait(timeout=10)
            audit['requested_budget'] = p.budget
            audit['reasons'] = ['A_CLASS_ORIGINAL_PUBLIC_LABEL_CONFORMANCE_ONLY',
                                'FIXED_EPSILON_ALPHA_NOT_TUNED_IN_BROWSER']
            if source == 'INVALID' or row.get('reference_status') == 'UNTRUSTED':
                audit['decision'] = 'EVIDENCE_INVALID'
                audit['reasons'].append('INVALID_METADATA_SIGNATURE_OR_UNTRUSTED_REFERENCE')
            elif source == 'UNKNOWN':
                audit['decision'] = 'NEED_MORE_EVIDENCE'
                audit['reasons'].append('PROVENANCE_EVIDENCE_MISSING')
        document = {'report_id': 'workbench-' + uuid.uuid4().hex, 'step': 'reference_workbench',
            'created_at': utcnow(), 'mode': 'REVEALED_DEMO' if p.kind == 'demo' else 'LOCAL_ASSESSMENT', 'input_kind': p.kind, 'dataset': dataset,
            'assessment': assessment, 'source_evidence': {'status': source,
                'evidence_class': 'A' if reference is not None else 'NONE',
                'factual_accuracy_verified': False, 'signature_proves_facts': False},
            'audit': audit, 'value': value, 'joint_decision': joint_decision(audit, value),
            'manifest_sha256': cfg['workbench_manifest']['sha256'],
            'value_registry_sha256': cfg.get('workbench_value_registry', {}).get('sha256'),
            'limitations': ['No independent factual audit is available.',
                'Time and representativeness remain UNKNOWN without external evidence.',
                'Bootstrap task intervals are conditional approximations, not unconditional future guarantees.',
                'All costs are declared per-record experimental units; no market price is estimated.']}
        path = reports / (document['report_id'] + '.json')
        with path.open('x', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
        return document

    @app.get('/api/workbench/reports/{report_id}')
    def report(report_id: str):
        return read_report(report_id)

    @app.get('/api/workbench/reports/{report_id}/export')
    def export(report_id: str):
        return JSONResponse(read_report(report_id), headers={
            'Content-Disposition': f'attachment; filename="{report_id}.json"'})

    @app.get('/api/workbench/reports/{report_id}/markdown')
    def markdown(report_id: str):
        return Response(markdown_report(read_report(report_id)), media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{report_id}.md"'})

    return app
