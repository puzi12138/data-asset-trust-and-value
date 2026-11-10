"""A thin local audit layer around the frozen Dataset_app dataset gateway.

Uploaded JSON may provide observations but never trust anchors, private keys,
reference reliability, or a replacement decision. Research plans freeze the
sample and risk requirements before outcomes are received.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import re
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, constr, conint

from ..credit.evidence import canonical_hash
from ..dataset_app.gateway import create_dataset_gateway, read_csv, MAX_BYTES
from ..signed_delivery.security import byte_hash, utcnow

ID = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
MAX_AUDIT_BYTES = 1024 * 1024


class PlanInput(BaseModel):
    source_report_id: constr(regex=r'^[A-Za-z0-9_.-]{1,100}$')
    reference_id: constr(regex=r'^[A-Za-z0-9_.-]{1,100}$')
    sample_size: conint(strict=True, ge=1, le=20000) = 50
    seed: conint(strict=True, ge=0, le=4294967295) = 101
    epsilon: float = Field(0.05, gt=0, lt=1)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    claim_scope: str = Field('reference_conformance', max_length=100)
    scenario_description: str = Field(..., min_length=1, max_length=500)
    require_representativeness: bool = False

    class Config:
        extra = 'forbid'


def create_audit_gateway(config_file: Path | str):
    """Reuse loopback origin controls, delivery checks and old APIs unchanged."""
    from ..audit_risk import create_audit_plan, evaluate_audit, reference_descriptor_hash

    path = Path(config_file).resolve()
    cfg = json.loads(path.read_text(encoding='utf-8'))
    app = create_dataset_gateway(path)
    app.title = 'Reference_evidence risk constrained dataset audit research'
    workspace = Path(cfg['workspace']).resolve()
    settings = cfg.get('audit_trust', {'public_keys': {}, 'references': {}})
    if set(settings) - {'public_keys', 'references'}:
        raise ValueError('AUDIT_TRUST_CONFIG_MUST_CONTAIN_PUBLIC_DATA_ONLY')
    folders = {name: workspace / name for name in ('audit_plans', 'audit_evidence', 'audit_reports')}
    for folder in folders.values():
        folder.mkdir(exist_ok=True)
        if folder.is_symlink():
            raise ValueError('AUDIT_STORAGE_MUST_NOT_BE_SYMLINK')

    def local_file(folder: Path, identity: str) -> Path:
        if not ID.fullmatch(identity):
            raise HTTPException(400, 'INVALID_AUDIT_ID')
        result = folder / (identity + '.json')
        if folder.is_symlink() or result.is_symlink() or result.resolve().parent != folder.resolve():
            raise HTTPException(400, 'AUDIT_PATH_BOUNDARY_FAILED')
        return result

    def read_file(folder: Path, identity: str):
        target = local_file(folder, identity)
        if not target.is_file():
            raise HTTPException(404, 'AUDIT_RECORD_NOT_FOUND')
        return json.loads(target.read_text(encoding='utf-8'))

    def save_file(folder: Path, identity: str, payload):
        with local_file(folder, identity).open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=False)

    def source(report_id: str):
        report = read_file(workspace / 'dataset_reports', report_id)
        dataset = report['dataset']
        identity, version = dataset['dataset_id'], dataset['version']
        if not ID.fullmatch(identity) or not ID.fullmatch(version):
            raise HTTPException(409, 'SOURCE_DATASET_IDENTITY_INVALID')
        rel = report.get('evidence_bundle', {}).get('raw_bytes_path_relative_to_workspace')
        if not rel:
            raise HTTPException(409, 'SOURCE_REPORT_HAS_NO_EXACT_BYTES')
        file = workspace / rel
        resolved = file.resolve()
        allowed = {workspace / 'received', workspace / 'dataset_imports'}
        if file.is_symlink() or resolved.parent not in {p.resolve() for p in allowed}:
            raise HTTPException(409, 'SOURCE_BYTES_PATH_BOUNDARY_FAILED')
        with file.open('rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or byte_hash(data) != dataset['content_hash']:
            raise HTTPException(409, 'SOURCE_DATASET_BYTES_CHANGED')
        frame = read_csv(data, dataset.get('separator', ','))
        if len(frame) != dataset['row_count']:
            raise HTTPException(409, 'SOURCE_ROW_COUNT_CHANGED')
        return report

    def checked_plan(identity: str):
        stored = read_file(folders['audit_plans'], identity)
        original = source(stored['source_report_id'])
        if canonical_hash(original) != stored['source_report_hash']:
            raise HTTPException(409, 'SOURCE_REPORT_CHANGED_SINCE_PLAN')
        if canonical_hash(stored['plan']) != stored['plan_storage_hash']:
            raise HTTPException(409, 'FROZEN_AUDIT_PLAN_CHANGED')
        return stored, original

    def assess(stored, original, evidence=None):
        dimensions = original['assessment']['dimensions']
        provenance = dimensions.get('provenance', {}).get('status')
        representation = dimensions.get('representativeness', {})
        representation_status = 'UNKNOWN'
        if representation.get('status') == 'NOT_APPLICABLE':
            representation_status = 'NOT_APPLICABLE'
        # No arbitrary cutoff turns a JS similarity into population sufficiency.
        context = {**settings, 'trusted_plan_hash': stored['plan'].get('plan_hash'),
                   'provenance_status': 'VERIFIED' if provenance == 'VERIFIED' else 'INVALID' if provenance == 'FAILED' else 'MISSING',
                   'representation_status': representation_status,
                   'require_representativeness': stored['scenario']['require_representativeness']}
        outcome = evaluate_audit(dataset=original['dataset'], plan=stored['plan'],
                                 evidence=evidence, trust_context=context, as_of=utcnow())
        report_id = 'audit-report-' + uuid.uuid4().hex
        result = {'report_id': report_id, 'step': 'reference_evidence', 'created_at': utcnow(),
                  'dataset': original['dataset'], 'source_report_id': original['report_id'],
                  'source_report_hash': stored['source_report_hash'], 'plan': stored['plan'],
                  'scenario': stored['scenario'], 'audit': outcome, 'evidence': evidence,
                  'trust_context_public': context, 'dataset_assessment': original['assessment'],
                  'value': original['value'], 'trust_multiplier_applied': False,
                  'limitations': ['Conditional scope is not an absolute factual truth label.',
                      'Signatures bind observations to locally trusted keys; they do not prove factual correctness.',
                      'Reference reliability and sampling assumptions must be independently supported.',
                      'SIMULATED references demonstrate mechanics only; uploaded claims never establish C evidence.',
                      'Same-account local storage is not a production multi-user security boundary.']}
        save_file(folders['audit_reports'], report_id, result)
        return result

    @app.get('/api/dataset-audits/options')
    def options():
        reports = []
        for file in sorted((workspace / 'dataset_reports').glob('*.json')):
            if file.is_symlink():
                continue
            data = json.loads(file.read_text(encoding='utf-8'))
            reports.append({'report_id': data['report_id'], 'created_at': data['created_at'],
                            'dataset': data['dataset'], 'value_status': data['value'].get('status')})
        return {'source_reports': reports, 'references': settings['references'],
                'trusted_auditors': settings['public_keys'],
                'limits': {'evidence_bytes': MAX_AUDIT_BYTES, 'outcomes': 20000},
                'disclosure': 'Reference and key trust are local configuration, never upload declarations.'}

    @app.post('/api/dataset-audits/plans')
    def new_plan(p: PlanInput):
        reference = settings['references'].get(p.reference_id)
        if reference is None or p.claim_scope not in reference.get('supported_scopes', []):
            raise HTTPException(400, 'REFERENCE_OR_CLAIM_SCOPE_NOT_PRECONFIGURED')
        original = source(p.source_report_id)
        dataset = original['dataset']
        plan = create_audit_plan(dataset_id=dataset['dataset_id'], version=dataset['version'],
                 content_hash=dataset['content_hash'], population_size=dataset['row_count'],
                 sample_size=p.sample_size, seed=p.seed, created_at=utcnow(), reference_id=p.reference_id,
                 reference_hash=reference['reference_hash'], reference_descriptor_hash=reference_descriptor_hash(reference),
                 epsilon=p.epsilon, alpha=p.alpha, claim_scope=p.claim_scope)
        stored = {'plan': plan, 'plan_storage_hash': canonical_hash(plan),
                  'source_report_id': p.source_report_id, 'source_report_hash': canonical_hash(original),
                  'scenario': {'description': p.scenario_description,
                               'require_representativeness': p.require_representativeness}}
        save_file(folders['audit_plans'], plan['plan_id'], stored)
        return {**stored, 'assessment_without_evidence': assess(stored, original)}

    @app.get('/api/dataset-audits/plans/{plan_id}')
    def get_plan(plan_id: str):
        stored, _ = checked_plan(plan_id)
        return stored

    @app.get('/api/dataset-audits/plans/{plan_id}/export')
    def export_plan(plan_id: str):
        stored, _ = checked_plan(plan_id)
        return JSONResponse(stored, headers={'Content-Disposition': f'attachment; filename="{plan_id}.json"'})

    @app.post('/api/dataset-audits/plans/{plan_id}/assess')
    def assess_without_evidence(plan_id: str):
        stored, original = checked_plan(plan_id)
        return assess(stored, original)

    @app.post('/api/dataset-audits/plans/{plan_id}/evidence')
    async def import_evidence(plan_id: str, request: Request):
        stored, original = checked_plan(plan_id)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'AUDIT_EVIDENCE_JSON_REQUIRED')
        chunks, length = [], 0
        async for part in request.stream():
            length += len(part)
            if length > MAX_AUDIT_BYTES:
                raise HTTPException(413, 'AUDIT_EVIDENCE_LIMIT_1_MIB')
            chunks.append(part)
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError('DUPLICATE_JSON_KEY')
                result[key] = value
            return result
        try:
            envelope = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON_NUMBER')))
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise HTTPException(400, 'INVALID_AUDIT_JSON: ' + str(exc)) from exc
        if not isinstance(envelope, dict) or set(envelope) - {'signer_id', 'payload', 'signature'}:
            raise HTTPException(422, 'AUDIT_ENVELOPE_ONLY_NO_UPLOADED_TRUST_OR_DECISIONS')
        payload = envelope.get('payload')
        if not isinstance(payload, dict) or set(payload) - {'audit_id', 'plan_id', 'plan_hash', 'dataset_id', 'version', 'content_hash', 'reference_id', 'audited_at', 'outcomes'}:
            raise HTTPException(422, 'AUDIT_PAYLOAD_FIELDS_INVALID')
        outcomes = payload.get('outcomes')
        if not isinstance(outcomes, list) or len(outcomes) > 20000:
            raise HTTPException(422, 'AUDIT_OUTCOMES_MUST_BE_BOUNDED_LIST')
        for row in outcomes:
            if (not isinstance(row, dict) or set(row) - {'position', 'outcome', 'evidence_locator'}
                    or not isinstance(row.get('position'), int) or isinstance(row.get('position'), bool)
                    or not isinstance(row.get('outcome'), str)
                    or row.get('outcome') not in {'PASS', 'ERROR', 'INCOMPARABLE', 'UNRESOLVED'}
                    or not isinstance(row.get('evidence_locator', ''), str)
                    or len(row.get('evidence_locator', '')) > 1000):
                raise HTTPException(422, 'AUDIT_OUTCOME_SCHEMA_INVALID')
        evidence_id = 'audit-evidence-' + uuid.uuid4().hex
        save_file(folders['audit_evidence'], evidence_id, {'received_at': utcnow(), 'envelope': envelope})
        return assess(stored, original, envelope)

    @app.get('/api/dataset-audits/reports/{report_id}')
    def report(report_id: str):
        return read_file(folders['audit_reports'], report_id)

    @app.get('/api/dataset-audits/reports/{report_id}/export')
    def export(report_id: str):
        return JSONResponse(report(report_id), headers={'Content-Disposition': f'attachment; filename="{report_id}.json"'})

    @app.get('/api/dataset-audits/reports/{report_id}/print', response_class=HTMLResponse)
    def printable(report_id: str):
        raw = html.escape(json.dumps(report(report_id), ensure_ascii=False, indent=2))
        return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>风险约束可信审计报告</title>'
                '<style>body{font:16px sans-serif;max-width:1000px;margin:30px auto;line-height:1.7}'
                'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>风险约束数据集可信审计</h1>'
                '<p>条件接受不是绝对真实标签。签名不证明事实；模拟证据不得冒充独立事实核验。可信结果不乘以经济价值。</p><pre>'
                + raw + '</pre></html>')

    return app
