"""Task_value saved utility gateway around all frozen Reference_evidence endpoints."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re
import uuid

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, constr

from ..audit_app.gateway import create_audit_gateway
from ..dataset_app.gateway import MAX_BYTES
from ..signed_delivery.security import byte_hash, utcnow
from .registry import SavedValueRegistry, EXACT_KEYS

ID = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
Key = constr(strict=True, min_length=1, max_length=200)
Hash = constr(strict=True, regex=r'^[0-9a-f]{64}$')


class ValueQuery(BaseModel):
    asset_id: Key
    version: Key
    content_hash: Hash
    task_id: Key
    model_id: Key
    buyer_id: Key
    scenario_id: Key
    distribution_id: Key
    protocol_hash: Hash
    source_report_id: constr(strict=True, regex=r'^[A-Za-z0-9_.-]{1,100}$') | None = None

    class Config:
        extra = 'forbid'


def create_value_gateway(config_file: Path | str):
    """No new network calls, CSV uploads or model fits are introduced here."""
    cfg_path = Path(config_file).resolve()
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    app = create_audit_gateway(cfg_path)
    app.title = 'Task_value scenario specific dataset task value research'
    workspace = Path(cfg['workspace']).resolve()
    setting = cfg.get('task_value_registry')
    registry = SavedValueRegistry(Path(setting['path']), setting['sha256']) if setting else SavedValueRegistry()
    app.state.task_value_registry = registry
    reports = workspace / 'task_value_reports'
    reports.mkdir(exist_ok=True)
    if reports.is_symlink():
        raise ValueError('VALUE_REPORT_DIRECTORY_MUST_NOT_BE_SYMLINK')

    def local(folder: Path, identity: str):
        if not ID.fullmatch(identity):
            raise HTTPException(400, 'INVALID_VALUE_REPORT_ID')
        path = folder / (identity + '.json')
        if folder.is_symlink() or path.is_symlink() or path.resolve().parent != folder.resolve():
            raise HTTPException(400, 'VALUE_LOCAL_PATH_BOUNDARY_FAILED')
        return path

    def read(folder: Path, identity: str):
        path = local(folder, identity)
        if not path.is_file():
            raise HTTPException(404, 'VALUE_SOURCE_OR_REPORT_NOT_FOUND')
        return json.loads(path.read_text(encoding='utf-8'))

    def source_matches(identity, query):
        source = read(workspace / 'dataset_reports', identity)
        dataset = source['dataset']
        rel = source.get('evidence_bundle', {}).get('raw_bytes_path_relative_to_workspace')
        if not rel:
            raise HTTPException(409, 'VALUE_SOURCE_EXACT_BYTES_UNAVAILABLE')
        raw = workspace / rel
        if raw.is_symlink() or raw.resolve().parent not in {(workspace / 'received').resolve(), (workspace / 'dataset_imports').resolve()}:
            raise HTTPException(409, 'VALUE_SOURCE_PATH_BOUNDARY_FAILED')
        if not raw.is_file():
            raise HTTPException(409, 'VALUE_SOURCE_EXACT_BYTES_UNAVAILABLE')
        with raw.open('rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or byte_hash(data) != dataset['content_hash']:
            raise HTTPException(409, 'VALUE_SOURCE_BYTES_CHANGED')
        return all(dataset.get(k) == query[q] for k, q in (
            ('dataset_id', 'asset_id'), ('version', 'version'), ('content_hash', 'content_hash')))

    @app.get('/api/task-values/options')
    def options():
        result = registry.options()
        result['source_reports'] = [{'report_id': item['report_id'], 'dataset': item['dataset']}
                                   for file in sorted((workspace / 'dataset_reports').glob('*.json'))
                                   if not file.is_symlink()
                                   for item in [json.loads(file.read_text(encoding='utf-8'))]]
        return result

    @app.post('/api/task-values/lookup')
    def lookup(p: ValueQuery):
        query = {k: getattr(p, k) for k in EXACT_KEYS}
        absent = {'status': 'NOT_EVALUATED', 'query': query,
                  'reason': 'NO_EXACT_ASSET_VERSION_HASH_TASK_MODEL_BUYER_SCENARIO_DISTRIBUTION_PROTOCOL_MEASUREMENT',
                  'trust_multiplier_applied': False, 'market_price': 'NOT_EVALUATED', 'record': None}
        if p.source_report_id and not source_matches(p.source_report_id, query):
            return {**absent, 'reason': 'IMPORTED_OR_RECEIVED_DATASET_IS_NOT_THE_EXACT_MEASURED_ASSET'}
        row = registry.lookup(query)
        if row is None:
            return absent
        report_id = 'task-value-' + uuid.uuid4().hex
        document = {'report_id': report_id, 'step': 'task_value', 'created_at': utcnow(),
                    'status': row['validation']['value_status'], 'query': query, 'record': row,
                    'source_report_id': p.source_report_id,
                    'registry_sha256': registry.sha256, 'comparisons': registry.comparisons(query),
                    'selection_basis': 'FROZEN_VALIDATION_ONLY', 'test_results_use': 'POSTHOC_EVALUATION_ONLY',
                    'trust_multiplier_applied': False, 'market_price': 'NOT_EVALUATED',
                    'limitations': ['Value is conditional on this buyer, task, learner, distribution and protocol.',
                        'Classification log-loss and regression absolute-error have different units.',
                        'Bootstrap intervals condition on fitted models and group exchangeability; no unconditional coverage claim.',
                        'Independent factual audits are absent for the new research assets; apparent utility does not establish truth.',
                        'Reference-conformance gates are mechanism experiments and cannot replace independent factual evidence.',
                        'Test oracle is a retrospective benchmark and is never a deployable recommendation.']}
        with local(reports, report_id).open('x', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
        return document

    @app.get('/api/task-values/reports/{report_id}')
    def report(report_id: str):
        return read(reports, report_id)

    @app.get('/api/task-values/reports/{report_id}/export')
    def export(report_id: str):
        return JSONResponse(report(report_id), headers={'Content-Disposition': f'attachment; filename="{report_id}.json"'})

    @app.get('/api/task-values/reports/{report_id}/print', response_class=HTMLResponse)
    def printable(report_id: str):
        raw = html.escape(json.dumps(report(report_id), ensure_ascii=False, indent=2))
        return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>场景化任务价值研究报告</title>'
                '<style>body{font:16px sans-serif;max-width:1000px;margin:30px auto;line-height:1.7}'
                'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>数据集场景化任务价值</h1>'
                '<p>验证集选择与测试集事后评价分开；可信约束与任务效用并列，不相乘。分类与回归损失量纲不同。'
                '不是市场交易价格；没有独立事实审计时保持证据不足。</p><pre>' + raw + '</pre></html>')

    return app
