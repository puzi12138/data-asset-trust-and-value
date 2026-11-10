"""Bounded local CSV ingestion and evidence-aware dataset assessment.

Only the gateway's trusted local key registry is used. Uploaded declarations
cannot introduce public keys or claim independent factual verification.
"""
from __future__ import annotations

import csv
import html
import io
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlencode
from typing import Literal

import pandas as pd
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, constr

from ..dataspace.consumer import HTTPClient
from ..dataspace.valuation import UtilityIndex
from ..signed_delivery.gateway import create_gateway
from ..signed_delivery.security import byte_hash, utcnow

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 20000
MAX_COLUMNS = 128
MAX_FIELD_CHARS = 65536
ID = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')


def read_csv(data: bytes, separator: str = ',') -> pd.DataFrame:
    """Reject ambiguous/binary CSV before allocation of a pandas frame."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError('CSV_EMPTY_OR_SIZE_LIMIT_2_MIB')
    if separator not in {',', ';', '\t'}:
        raise ValueError('CSV_SEPARATOR_MUST_BE_COMMA_SEMICOLON_OR_TAB')
    try:
        text = data.decode('utf-8-sig', errors='strict')
    except UnicodeError as exc:
        raise ValueError('CSV_MUST_BE_UTF8') from exc
    if any(ord(ch) < 32 and ch not in '\r\n\t' for ch in text):
        raise ValueError('CSV_BINARY_OR_CONTROL_CHARACTERS')
    try:
        reader = csv.reader(io.StringIO(text), delimiter=separator, strict=True)
        header = next(reader)
        if not 1 <= len(header) <= MAX_COLUMNS:
            raise ValueError('CSV_COLUMN_LIMIT_128')
        if any(not col.strip() or len(col) > 200 for col in header) or len(set(header)) != len(header):
            raise ValueError('CSV_EMPTY_DUPLICATE_OR_LONG_COLUMN_NAME')
        n = 0
        for row in reader:
            if not row:  # Same treatment as pandas skip_blank_lines.
                continue
            n += 1
            if n > MAX_ROWS:
                raise ValueError('CSV_ROW_LIMIT_20000')
            if len(row) != len(header):
                raise ValueError('CSV_INCONSISTENT_FIELD_COUNT')
            if any(len(cell) > MAX_FIELD_CHARS for cell in row):
                raise ValueError('CSV_FIELD_LIMIT_65536_CHARACTERS')
        if n == 0:
            raise ValueError('CSV_HAS_NO_DATA_ROWS')
        frame = pd.read_csv(io.StringIO(text), sep=separator)
        if frame.empty:
            raise ValueError('CSV_HAS_NO_DATA_ROWS_AFTER_PARSING')
        if len(frame) > MAX_ROWS:
            raise ValueError('CSV_ROW_LIMIT_20000')
        return frame
    except (csv.Error, StopIteration, pd.errors.ParserError) as exc:
        raise ValueError('CSV_PARSE_ERROR') from exc


class AssessInput(BaseModel):
    source_declaration: constr(max_length=500) = ''
    schema_name: Literal['auto', 'generic', 'bank', 'wine'] = 'auto'
    task_id: constr(max_length=200) | None = None
    economics_id: constr(max_length=100) = 'fp1_fn5'

    class Config:
        extra = 'forbid'


class AssetInput(AssessInput):
    asset_id: constr(regex=r'^[A-Za-z0-9_.-]{1,100}$')
    version: constr(regex=r'^[A-Za-z0-9_.-]{1,100}$')


def create_dataset_gateway(config_file: Path | str):
    # Same security middleware, own Consumer signing identity and old seven APIs.
    path = Path(config_file).resolve()
    cfg = json.loads(path.read_text(encoding='utf-8'))
    app = create_gateway(path)
    app.title = 'Dataset_app local dataset trust research'
    workspace = Path(cfg['workspace']).resolve()
    if not workspace.is_dir():
        raise ValueError('LOCAL_WORKSPACE_MISSING')
    data_dir = workspace / 'dataset_imports'
    report_dir = workspace / 'dataset_reports'
    data_dir.mkdir(exist_ok=True)
    report_dir.mkdir(exist_ok=True)
    if data_dir.is_symlink() or report_dir.is_symlink():
        raise ValueError('LOCAL_DATA_DIRECTORY_MUST_NOT_BE_SYMLINK')
    keys = app.state.keys
    client = HTTPClient(workspace / 'dataset_http_trace.jsonl')
    index = UtilityIndex(Path(cfg['utility_index']))

    def local_file(folder: Path, identity: str, suffix: str) -> Path:
        if not ID.fullmatch(identity):
            raise HTTPException(400, 'INVALID_LOCAL_DATASET_OR_REPORT_ID')
        target = folder / (identity + suffix)
        if folder.is_symlink() or target.is_symlink() or target.resolve().parent != folder.resolve():
            raise HTTPException(400, 'LOCAL_PATH_BOUNDARY_FAILED')
        return target

    def bounded_read(file: Path) -> bytes:
        with file.open('rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, 'CSV_SIZE_LIMIT_2_MIB')
        return data

    def saved_json(folder, identity, suffix='.json'):
        target = local_file(folder, identity, suffix)
        if not target.is_file():
            raise HTTPException(404, 'LOCAL_DATASET_OR_REPORT_NOT_FOUND')
        return json.loads(target.read_text(encoding='utf-8'))

    def get(route, query=None):
        return client.json('GET', cfg['registry_url'] + route + ('?' + urlencode(query) if query else ''))

    def value_for(identity, version, digest, p):
        absent = {'status': 'NOT_EVALUATED', 'reason': 'NO_EXACT_ASSET_VERSION_HASH_TASK_MEASUREMENT',
                  'trust_multiplier_applied': False}
        if not p.task_id:
            return {**absent, 'reason': 'TARGET_TASK_NOT_SELECTED'}
        with index.connection() as conn:
            count = conn.execute('SELECT COUNT(*) FROM utility WHERE asset_id=? AND version=? AND content_hash=? AND task_id=? AND economics_id=?',
                                 (identity, version, digest, p.task_id, p.economics_id)).fetchone()[0]
        if count != 1:
            return absent
        result = client.json('POST', cfg['registry_url'] + '/valuations', keys.sign('consumer', {
            'asset_id': identity, 'version': version, 'task_id': p.task_id,
            'economics_id': p.economics_id, 'origin': 'local_operational', 'method': 'beta'}))
        return {**result, 'trust_multiplier_applied': False,
                'separation': 'Dataset trust is independent of task utility and delivery reliability.'}

    def choose_schema(frame, selection):
        from ..trustworthiness import identify_schema
        identified = identify_schema(frame)
        if selection in {'bank', 'wine'} and identified != selection:
            raise HTTPException(400, 'DECLARED_SCHEMA_DOES_NOT_MATCH_COMPLETE_COLUMN_SET')
        return identified if selection == 'auto' else selection

    def assess(data, identity, version, context, p, origin, evidence_bundle=None):
        from ..trustworthiness import evaluate_dataset
        frame = read_csv(data)
        context = {**context, 'schema_name': choose_schema(frame, p.schema_name)}
        evaluation = evaluate_dataset(frame, dataset_id=identity, version=version,
                                      raw_bytes=data, context=context)
        report_id = 'dataset-report-' + uuid.uuid4().hex
        result = {'report_id': report_id, 'step': 'dataset_app', 'created_at': utcnow(),
                  'dataset': {'dataset_id': identity, 'version': version, 'content_hash': byte_hash(data),
                              'row_count': len(frame), 'column_count': len(frame.columns),
                              'schema': [{'name': c, 'dtype': str(frame[c].dtype)} for c in frame], 'origin': origin},
                  'assessment': evaluation, 'value': value_for(identity, version, byte_hash(data), p),
                  'evidence_bundle': evidence_bundle or {},
                  'limitations': ['Signatures bind bytes and local identities; they do not prove content truth.',
                                  'No independent factual labels or target population reference were supplied.',
                                  'Uploaded datasets stay in this local workspace; no third-party transmission.']}
        target = local_file(report_dir, report_id, '.json')
        with target.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        return result

    @app.get('/api/datasets')
    def list_datasets():
        return {'datasets': [json.loads(f.read_text(encoding='utf-8')) for f in sorted(data_dir.glob('*.json'))
                             if not f.is_symlink()],
                'limits': {'bytes': MAX_BYTES, 'rows': MAX_ROWS, 'columns': MAX_COLUMNS, 'encoding': 'UTF-8'}}

    @app.post('/api/datasets/import')
    async def import_dataset(request: Request, name: str = 'local.csv', separator: str = ','):
        if len(name) > 200 or '/' in name or '\\' in name or not name.lower().endswith('.csv'):
            raise HTTPException(400, 'DISPLAY_NAME_MUST_BE_CSV_WITHOUT_PATH')
        if request.headers.get('content-type', '').split(';')[0] not in {'text/csv', 'application/csv'}:
            raise HTTPException(415, 'RAW_UTF8_CSV_REQUIRED')
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > MAX_BYTES):
            raise HTTPException(413, 'CSV_SIZE_LIMIT_2_MIB')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BYTES:
                raise HTTPException(413, 'CSV_SIZE_LIMIT_2_MIB')
            chunks.append(chunk)
        data = b''.join(chunks)
        frame = read_csv(data, separator)
        identity = 'local-' + uuid.uuid4().hex
        record = {'dataset_id': identity, 'version': 'v1', 'name': name,
                  'content_hash': byte_hash(data), 'separator': separator, 'row_count': len(frame),
                  'column_count': len(frame.columns), 'created_at': utcnow(),
                  'schema': [{'name': c, 'dtype': str(frame[c].dtype)} for c in frame],
                  'origin': 'LOCAL_USER_IMPORT', 'source_verification': 'UNKNOWN'}
        with local_file(data_dir, identity, '.csv').open('xb') as handle:
            handle.write(data)
        with local_file(data_dir, identity, '.json').open('x', encoding='utf-8') as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        return record

    @app.post('/api/datasets/{dataset_id}/assess')
    def assess_import(dataset_id: str, p: AssessInput):
        record = saved_json(data_dir, dataset_id)
        data = bounded_read(local_file(data_dir, dataset_id, '.csv'))
        if byte_hash(data) != record['content_hash']:
            raise HTTPException(409, 'IMPORTED_BYTES_CHANGED_SINCE_REGISTRATION')
        # Normalize separator for analysis only; hash and provenance bind raw bytes.
        frame = read_csv(data, record['separator'])
        from ..trustworthiness import evaluate_dataset
        schema_name = choose_schema(frame, p.schema_name)
        result = evaluate_dataset(frame, dataset_id=dataset_id, version=record['version'], raw_bytes=data,
                                  context={'schema_name': schema_name, 'source': p.source_declaration})
        report_id = 'dataset-report-' + uuid.uuid4().hex
        document = {'report_id': report_id, 'step': 'dataset_app', 'created_at': utcnow(), 'dataset': record,
                    'user_declarations': {'source': p.source_declaration, 'verification_status': 'NOT_VERIFIED',
                                          'schema_rule_selection': p.schema_name, 'applied_schema': schema_name},
                    'assessment': result, 'value': {'status': 'NOT_EVALUATED',
                    'reason': 'LOCAL_IMPORT_HAS_NO_EXACT_TASK_MEASUREMENT', 'trust_multiplier_applied': False},
                    'evidence_bundle': {'raw_bytes_path_relative_to_workspace': 'dataset_imports/' + dataset_id + '.csv',
                                        'content_hash': record['content_hash'], 'external_evidence': 'NOT_SUPPLIED'},
                    'limitations': ['Provider declarations are not independent verification.',
                                    'No factual label audit, observation time or target population reference supplied.']}
        with local_file(report_dir, report_id, '.json').open('x', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
        return document

    @app.post('/api/datasets/assess-asset')
    def assess_asset(p: AssetInput):
        asset = get('/assets/' + p.asset_id, {'version': p.version})
        provider = asset['provider_id']
        if provider not in cfg['provider_urls']:
            raise HTTPException(400, 'PROVIDER_NOT_IN_LOCAL_TRUST_CONFIG')
        envelopes = client.json('GET', cfg['provider_urls'][provider] + '/assets')['assets']
        envelope = next((e for e in envelopes if e.get('payload', {}).get('asset_id') == p.asset_id
                         and e.get('payload', {}).get('version') == p.version), None)
        if envelope is None:
            raise HTTPException(404, 'EXACT_SIGNED_METADATA_NOT_AVAILABLE')
        meta = keys.verify(envelope, expected_actor=provider)
        if meta.get('content_hash') != asset.get('content_hash'):
            raise HTTPException(409, 'PROVIDER_REGISTRY_HASH_MISMATCH')
        data, verified_receipt, received_id = None, None, None
        # No arbitrary user path, no Provider filesystem access. Check a signed
        # Verifier observation and its signed commitment before local read.
        for contract in get('/contracts')['contracts']:
            if contract.get('asset_id') != p.asset_id or contract.get('asset_version') != p.version:
                continue
            record = get('/deliveries/' + contract['contract_id']).get('record') or {}
            signed = record.get('signed_receipt')
            if not signed:
                continue
            receipt = keys.verify(signed, expected_actor='verifier')
            commitment = keys.verify(receipt['commitment'], expected_actor=provider)
            if (receipt.get('actual_content_hash') != asset['content_hash']
                    or commitment.get('asset_id') != p.asset_id or commitment.get('asset_version') != p.version
                    or receipt.get('contract_id') != contract['contract_id']
                    or receipt.get('received_contract_id') != contract['contract_id']
                    or receipt.get('received_version') != p.version
                    or receipt.get('actual_schema_hash') != asset.get('schema_hash')
                    or commitment.get('expected_content_hash') != asset.get('content_hash')
                    or commitment.get('expected_schema_hash') != asset.get('schema_hash')
                    or receipt.get('transport_status') != 'RECEIVED'
                    or receipt.get('status') != 'SUCCESS'
                    or not all(receipt.get('checks', {}).get(k) is True for k in (
                        'hash_match', 'schema_match', 'version_match', 'contract_match', 'on_time'))
                    or any(commitment.get(k) != v for k, v in contract.items())):
                continue
            file = local_file(workspace / 'received', contract['contract_id'], '.csv')
            if file.is_file():
                candidate = bounded_read(file)
                if byte_hash(candidate) == asset['content_hash']:
                    data, verified_receipt, received_id = candidate, signed, contract['contract_id']
                    break
        if data is None:
            raise HTTPException(409, 'NO_VERIFIER_RECEIVED_EXACT_BYTES: complete authorized delivery first')
        context = {'schema_name': 'bank', 'source': meta.get('provenance', {}).get('source', ''),
                   'provenance': {'metadata': meta, 'envelope': envelope,
                                  'public_keys': keys.public_registry(), 'expected_signer': provider}}
        return assess(data, p.asset_id, p.version, context, p, 'VERIFIER_RECEIVED_EXACT_BYTES', {
            'signed_metadata': envelope, 'signed_verifier_receipt': verified_receipt,
            'trusted_public_keys': keys.public_registry(), 'trust_anchor_scope': 'LOCAL_PRECONFIGURED_RESEARCH_IDENTITIES',
            'raw_bytes_path_relative_to_workspace': 'received/' + received_id + '.csv',
            'content_hash': asset['content_hash'], 'registry_authorization_token_included': False})

    @app.get('/api/datasets/reports/{report_id}')
    def report(report_id: str):
        return saved_json(report_dir, report_id)

    @app.get('/api/datasets/reports/{report_id}/export')
    def export(report_id: str):
        document = saved_json(report_dir, report_id)
        return JSONResponse(document, headers={'Content-Disposition': f'attachment; filename="{report_id}.json"'})

    @app.get('/api/datasets/reports/{report_id}/print', response_class=HTMLResponse)
    def printable(report_id: str):
        document = saved_json(report_dir, report_id)
        raw = html.escape(json.dumps(document, ensure_ascii=False, indent=2))
        return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>数据集可信评估报告</title>'
                '<style>body{font:16px sans-serif;max-width:1000px;margin:30px auto;line-height:1.7}'
                'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>数据集自身可信度研究报告</h1>'
                '<p>指标不是内容真实概率。签名不证明事实；UNKNOWN不是负面证据。任务价值独立报告。</p><pre>'
                + raw + '</pre></html>')

    return app

