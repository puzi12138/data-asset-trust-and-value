"""Independent verifier process forwards a consumer signature; owns no consumer key."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import socket
import urllib.error
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .security import keyring_from_config, byte_hash, utcnow, parse_time
from .validation import check_schema
from ..dataspace.consumer import HTTPClient, HTTPFailure

def create_verifier(config_file: Path | str) -> FastAPI:
    path=Path(config_file).resolve(); cfg=json.loads(path.read_text(encoding='utf-8'))
    keys=keyring_from_config(cfg,path.parent,'verifier')
    client=HTTPClient(path.parent/'verifier_http_trace.jsonl')
    app=FastAPI(title='Signed_delivery independent local verifier')

    @app.exception_handler(ValueError)
    def invalid(_,exc): return JSONResponse(status_code=400,content={'detail':str(exc)})
    @app.exception_handler(KeyError)
    def missing(_,exc): return JSONResponse(status_code=400,content={'detail':'MISSING_FIELD:'+str(exc)})
    @app.exception_handler(HTTPFailure)
    def upstream(_,exc): return JSONResponse(status_code=exc.status,content={'detail':exc.body})
    @app.get('/health')
    def health(): return {'status':'OK','role':'verifier','pid':os.getpid(),'signing_actor':keys.actor,'deployment_eligible':False}

    @app.post('/verify-delivery')
    def deliver(request: dict):
        # Consumer authorizes exact contract. Verifier cannot sign as consumer.
        payload=keys.verify(request,expected_actor='consumer')
        token=payload['contract']; c=keys.verify(token,expected_actor='registry')
        if c['provider_url'] != cfg['provider_urls'].get(c['provider_id']):
            raise ValueError('PROVIDER_URL_NOT_PRECONFIGURED')
        commitment=client.json('POST',c['provider_url']+'/commitments',request)
        try:
            promised=keys.verify(commitment,expected_actor=c['provider_id'])
            if any(promised.get(k)!=v for k,v in c.items()): raise ValueError('COMMITMENT_BINDING_MISMATCH')
        except ValueError as exc:
            bad=keys.sign('verifier',{'contract_id':c['contract_id'],'commitment':commitment})
            try:
                client.json('POST',cfg['registry_url']+'/deliveries',bad)
                raise RuntimeError('Registry accepted bad provider signature')
            except HTTPFailure as denied:
                return {'contract_id':c['contract_id'],'status':'EVIDENCE_REJECTED','origin':c['origin'],
                    'reason':str(exc),'registry_http_status':denied.status,'received_bytes':0,'checks':{},
                    'signed_submission':bad,'credit_event_created':False,'verifier_signature_valid':True}
        data,headers,transport,error=b'',{},'UNKNOWN',None
        try:
            remaining=max(.05,(parse_time(c['agreed_deadline'])-parse_time(utcnow())).total_seconds()+.05)
            status,headers,data=client.request('POST',c['provider_url']+'/transfers/'+c['contract_id'],request,timeout=remaining)
            transport='RECEIVED' if status==200 else 'UNKNOWN'
            if status!=200: error,data=f'HTTP_{status}',b''
        except (TimeoutError,socket.timeout): transport,error='TIMEOUT','RECEIVE_DEADLINE_EXCEEDED'
        except urllib.error.URLError as exc:
            transport='TIMEOUT' if isinstance(exc.reason,(TimeoutError,socket.timeout)) else 'UNKNOWN'; error=str(exc)
        now=utcnow()
        schema_ok,schema_hash,rows=check_schema(data,c['schema']) if transport=='RECEIVED' else (False,None,0)
        headers={k.lower():v for k,v in headers.items()}
        digest=byte_hash(data) if transport=='RECEIVED' else None
        checks={'hash_match':digest==c['expected_content_hash'], 'schema_match':schema_ok and schema_hash==c['expected_schema_hash'],
            'version_match':headers.get('x-asset-version')==c['asset_version'],
            'contract_match':headers.get('x-contract-id')==c['contract_id'],'on_time':parse_time(now)<=parse_time(c['agreed_deadline'])}
        state=('SUCCESS' if all(checks.values()) else 'FAILURE') if transport=='RECEIVED' else (
            'FAILURE' if transport=='TIMEOUT' and not checks['on_time'] else 'PENDING' if checks['on_time'] else 'UNKNOWN')
        receipt=keys.sign('verifier',{'contract_id':c['contract_id'],'commitment':commitment,'event_time':now,
            'evidence_origin':c['origin'],'transport_status':transport,'received_bytes':len(data),
            'actual_content_hash':digest,'actual_schema_hash':schema_hash,'row_count':rows,
            'received_version':headers.get('x-asset-version'),'received_contract_id':headers.get('x-contract-id'),
            'checks':checks,'status':state,'transport_error':error})
        if transport=='RECEIVED':
            dest=Path(cfg['evidence_dir']); dest.mkdir(parents=True,exist_ok=True)
            (dest/(c['contract_id']+'.csv')).write_bytes(data)
        result=client.json('POST',cfg['registry_url']+'/deliveries',receipt)
        return {'contract_id':c['contract_id'],'status':state,'origin':c['origin'],'checks':checks,
            'received_bytes':len(data),'received_hash':digest,'signed_submission':receipt,'registry_result':result,
            'credit_event_created':state in {'SUCCESS','FAILURE'},'verifier_signature_valid':True,
            'provider_signature_valid':True,'transport_status':transport}

    @app.post('/disputes')
    def dispute(request: dict):
        payload=keys.verify(request,expected_actor='consumer')
        if (set(payload)!={'contract_id','reason'} or not isinstance(payload['reason'],str) or not 1<=len(payload['reason'])<=500
                or not isinstance(payload.get('contract_id'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',payload['contract_id'])):
            raise ValueError('INVALID_DISPUTE_REQUEST')
        signed=keys.sign('verifier',{**payload,'event_time':utcnow()})
        return client.json('POST',cfg['registry_url']+'/deliveries/'+payload['contract_id']+'/disputes',signed)
    return app
