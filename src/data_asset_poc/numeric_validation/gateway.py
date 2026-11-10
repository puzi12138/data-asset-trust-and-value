"""Local coverage interface with explicit no-audit state for every asset."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from ..joint_assessment import canonical, digest
from ..joint_assessment.gateway import JOINT_ID, create_joint_gateway

METHOD_VERSION = 'coverage-unavailable-v1'
OUTCOMES = frozenset({'MATCH', 'MISMATCH', 'INCOMPARABLE', 'DISPUTED', 'NOT_AUDITED'})


def _not_evaluated(joint, reason):
    document = {'method_version': METHOD_VERSION, 'status': 'NOT_EVALUATED',
        'joint_id': joint['joint_id'], 'joint_result_sha256': joint.get('result_sha256'),
        'joint_binding': joint.get('binding'), 'binding': None, 'audit_summary': None,
        'members': [], 'budget_results': [], 'reference': None,
        'task_value': copy.deepcopy(joint.get('task_value')),
        'decision': {'status': 'NEED_MORE_EVIDENCE', 'fact_status': 'UNKNOWN',
                     'reason': reason, 'trust_multiplier_applied': False},
        'issues': [{'code': reason, 'path': 'candidate_reference_audit'}],
        'source_resolution': 'SERVER_JOINT_IDENTIFIER_ONLY',
        'report_markdown': '# 候选数据可信证据覆盖\n\n'
                           '当前未配置候选数据的独立审计源，内容真实性仍为UNKNOWN。\n\n'
                           '任务效用保留现有评估记录，不因缺少审计改写。\n'}
    document['result_sha256'] = digest(canonical(document))
    return document


def create_coverage_gateway(config_file: Path | str, project_root: Path | str | None = None,
                            registry_root: Path | str | None = None, adapter=None):
    """Add read-only unavailable coverage to the real server-side resolver."""
    root = Path(project_root or Path(__file__).resolve().parents[3]).resolve()
    app = create_joint_gateway(config_file, root, registry_root, adapter)
    prior_get = next(route.endpoint for route in app.routes
        if getattr(route, 'path', None) == '/api/same-asset-joint/{joint_id}'
        and 'GET' in getattr(route, 'methods', set()))

    def resolved_audit(identity):
        if not JOINT_ID.fullmatch(identity):
            raise HTTPException(404, 'DI_JOINT_REPORT_NOT_FOUND')
        response = prior_get(identity, 'json')
        joint = json.loads(response.body)
        if not isinstance(joint, dict) or joint.get('joint_id') != identity:
            raise HTTPException(409, {'status':'BINDING_MISMATCH', 'fact_status':'UNKNOWN',
                                      'issues':[{'code':'DI_JOINT_RESOLVER_RESPONSE_INVALID'}]})
        reason = ('COMPLETED_SAME_VERSION_VALUE_REQUIRED' if joint.get('status') != 'SAME_ASSET_BOUND'
                  else 'DI_AUDIT_NOT_CONFIGURED')
        return _not_evaluated(joint, reason)

    @app.get('/api/di-evidence/capabilities')
    def coverage_capabilities():
        return {'method_version':METHOD_VERSION, 'validation_version':None,
            'audit_availability':'NOT_AVAILABLE', 'audit_error':None,
            'request_fields':['joint_id'], 'read_only':True,
            'no_new_model_calls':True, 'no_training':True,
            'endpoints':{'report':'/api/di-evidence/{joint_id}?format=json|md',
                         'rows':'/api/di-evidence/{joint_id}/rows?offset=0&limit=100&outcome=MATCH',
                         'record':'/api/di-evidence/{joint_id}/rows/{candidate_row}'},
            'reference_level':None, 'fact_status':'UNKNOWN', 'max_page_size':100,
            'message':'当前未配置候选数据的独立审计源，审计保持未评估。'}

    @app.get('/api/di-evidence/{joint_id}')
    def coverage_report(joint_id: str, format: str = 'json'):
        if format not in {'json', 'md'}:
            raise HTTPException(422, 'DI_REPORT_FORMAT_MUST_BE_MD_OR_JSON')
        document = resolved_audit(joint_id)
        headers = {'Content-Disposition': f'attachment; filename="di_{joint_id}.{format}"',
                   'Cache-Control':'no-store', 'X-Di-Coverage-SHA256':document['result_sha256']}
        if format == 'json':
            return JSONResponse(document, headers=headers)
        return Response(document['report_markdown'].encode('utf-8'), media_type='text/markdown', headers=headers)

    @app.get('/api/di-evidence/{joint_id}/rows')
    def coverage_rows(joint_id: str, offset: int = 0, limit: int = 100, outcome: str | None = None):
        if offset < 0 or not 1 <= limit <= 100 or outcome is not None and outcome not in OUTCOMES:
            raise HTTPException(422, 'DI_PAGE_OR_OUTCOME_INVALID')
        document = resolved_audit(joint_id)
        return {'rows':[], 'total':0, 'offset':offset, 'limit':limit, 'status':document['status'],
                'joint_id':joint_id, 'result_sha256':document['result_sha256']}

    @app.get('/api/di-evidence/{joint_id}/rows/{candidate_row}')
    def coverage_record(joint_id: str, candidate_row: int):
        resolved_audit(joint_id)
        raise HTTPException(404, 'DI_MEMBER_NOT_FOUND')

    return app
