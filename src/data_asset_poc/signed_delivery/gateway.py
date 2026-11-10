"""Loopback consumer gateway: browser has no signing keys or invented evidence."""
from __future__ import annotations
import html
from contextlib import closing
import json
import os
import sqlite3
from pathlib import Path
import uuid
from urllib.parse import urlencode
from typing import Literal
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, constr
from .security import keyring_from_config, utcnow, parse_time, byte_hash
from ..dataspace.consumer import HTTPClient, HTTPFailure
from ..dataspace.valuation import UtilityIndex
from ..quality import evaluate_quality
from ..schema import RULES

Identity = constr(regex=r'^[A-Za-z0-9_.-]{1,100}$')
class StrictInput(BaseModel):
    class Config: extra='forbid'
class AssetInput(StrictInput):
    asset_id: Identity
    version: Identity
class AccessInput(AssetInput):
    purpose: constr(min_length=1,max_length=100)='research'
    mode: Literal['normal','tamper','wrong_version','schema_mismatch','timeout','invalid_signature','unknown']='normal'
    request_id: Identity | None=None
class DeliveryInput(StrictInput):
    contract_id: Identity
class PublishInput(StrictInput):
    provider_id: Literal['provider_a','provider_b'] | None=None
    asset_id: Identity | None=None
    version: Identity | None=None
class ValueInput(AssetInput):
    task_id: constr(min_length=1,max_length=200)
    economics_id: constr(min_length=1,max_length=100)='fp1_fn5'
    at: str | None=None
    origin: Literal['local_operational','fault_injection']='local_operational'
    method: Literal['beta','decay30','decay90','hierarchy','decay_hier30','decay_hier90']='decay30'
class CompareInput(StrictInput):
    assets: list[AssetInput]=Field(...,min_items=2,max_items=10)
    task_id: constr(min_length=1,max_length=200)
    economics_id: constr(min_length=1,max_length=100)='fp1_fn5'
    at: str | None=None
    origin: Literal['local_operational','fault_injection']='local_operational'
    method: Literal['beta','decay30','decay90','hierarchy','decay_hier30','decay_hier90']='decay30'
class StatusInput(StrictInput):
    version: Identity
    state: Literal['REVOKED','EXPIRED']
    expected_state: str | None=None
class DisputeInput(DeliveryInput):
    reason: constr(min_length=1,max_length=500)

def create_gateway(config_file: Path | str) -> FastAPI:
    path=Path(config_file).resolve(); cfg=json.loads(path.read_text(encoding='utf-8'))
    keys=keyring_from_config(cfg,path.parent,'consumer')
    client=HTTPClient(path.parent/'consumer_http_trace.jsonl')
    index=UtilityIndex(Path(cfg['utility_index']))
    registry=cfg['registry_url']; workspace=Path(cfg['workspace'])
    token_db=workspace/'consumer_contracts.sqlite'
    with closing(sqlite3.connect(token_db)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS tokens(contract_id TEXT PRIMARY KEY,token TEXT NOT NULL)')
    app=FastAPI(title='Signed_delivery local research UI gateway')
    app.state.keys=keys

    @app.middleware('http')
    async def local_only(request: Request,call_next):
        host=request.headers.get('host','').split(':')[0]
        if host not in {'127.0.0.1','localhost','testserver'}:
            return JSONResponse(status_code=403,content={'detail':'LOCAL_RESEARCH_HOST_ONLY'})
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            allowed={cfg['demo_urls']['consumer'],cfg['demo_urls']['consumer'].replace('127.0.0.1','localhost')}
            if origin and origin not in allowed or request.headers.get('x-research-request')!='1':
                return JSONResponse(status_code=403,content={'detail':'LOCAL_DEMO_ORIGIN_OR_ACTION_HEADER_REQUIRED'})
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response
    @app.exception_handler(HTTPFailure)
    def upstream(_,exc): return JSONResponse(status_code=exc.status,content={'detail':exc.body,'upstream_status':exc.status})
    @app.exception_handler(ValueError)
    def invalid(_,exc): return JSONResponse(status_code=400,content={'detail':str(exc)})
    @app.exception_handler(KeyError)
    def missing(_,exc): return JSONResponse(status_code=404,content={'detail':str(exc)})
    @app.get('/health')
    def health(): return {'status':'OK','role':'consumer','pid':os.getpid(),'signing_actor':keys.actor,'deployment_eligible':False}

    def get(route: str,query: dict | None=None):
        return client.json('GET',registry+route+('?' + urlencode({k:v for k,v in (query or {}).items() if v is not None}) if query else ''))
    def post(route: str,payload: dict): return client.json('POST',registry+route,keys.sign('consumer',payload))
    def contracts():
        return [get('/deliveries/'+c['contract_id']) for c in get('/contracts')['contracts']]
    def all_audit():
        result,after=[],0
        while True:
            page=get('/audit/events',{'after':after,'limit':1000}); result.extend(page['events'])
            if not page['events']: return result
            after=page['next_after']
    def quality(asset):
        # Read only actually received, exact-hash files. No provider self-score.
        for file in (workspace/'received').glob('*.csv'):
            if byte_hash(file.read_bytes())==asset['content_hash']:
                frame=pd.read_csv(file)
                result=evaluate_quality(frame,{c:RULES[c] for c in frame})
                return {**result,'verified_content_hash':asset['content_hash'],'source':'VERIFIER_RECEIVED_EXACT_BYTES',
                    'not_factual_truth':True,'label_correctness_not_detected_by_format':True}
        return None

    @app.get('/api/dashboard')
    def dashboard():
        assets=get('/assets')['assets']; records=contracts(); counts={}
        for item in records:
            origin=item['contract']['origin']; counts.setdefault(origin,{})
            counts[origin][item['status']]=counts[origin].get(item['status'],0)+1
        nodes=[]
        for name,url in cfg['demo_urls'].items():
            if name=='consumer': nodes.append({'name':name,**health()}); continue
            try: nodes.append({'name':name,**client.json('GET',url+'/health',timeout=.8)})
            except Exception: nodes.append({'name':name,'status':'UNAVAILABLE'})
        return {'providers':len(cfg['provider_urls']),'assets':len({a['asset_id'] for a in assets}),'versions':len(assets),
            'contracts':len(records),'status_counts_by_origin':counts,'nodes':nodes,
            'recent_reports':get('/reports')['reports'][:5], 'simulated':get('/legacy/coverage'),
            'deployment_eligible':False,'research_warning':'研究原型，非商业信用评级或法定数据资产估值'}

    @app.get('/api/assets')
    def assets():
        items=get('/assets')['assets']
        for item in items:
            item['column_count']=len(item['schema']); q=quality(item); item['row_count']=q['row_count'] if q else item.get('row_count')
        return {'assets':items}
    @app.get('/api/assets/{asset_id}')
    def detail(asset_id: str,version: str):
        asset=get('/assets/'+asset_id,{'version':version}); measurements=[]
        with index.connection() as conn:
            for row in conn.execute('SELECT task_id,economics_id FROM utility WHERE asset_id=? AND version=? AND content_hash=?',
                (asset_id,version,asset['content_hash'])): measurements.append({'task_id':row[0],'economics_id':row[1]})
        return {'asset':asset,'versions':get('/assets/'+asset_id+'/versions')['versions'], 'quality':quality(asset),
            'task_measurements':measurements,'state_events':get('/assets/'+asset_id+'/state-events',{'version':version})['events']}

    @app.post('/api/publish')
    def publish(p: PublishInput):
        published=[]
        for provider,url in cfg['provider_urls'].items():
            if p.provider_id and p.provider_id!=provider: continue
            for envelope in client.json('GET',url+'/assets')['assets']:
                meta=keys.verify(envelope,expected_actor=provider)
                if p.asset_id and p.asset_id!=meta['asset_id'] or p.version and p.version!=meta['version']: continue
                published.append(client.json('POST',registry+'/assets',envelope))
        if not published: raise HTTPException(404,'NO_PREPARED_ASSET_VERSION_MATCH')
        return {'published':published,'count':len(published)}
    @app.post('/api/access')
    def access(p: AccessInput):
        result=post('/access-requests',{'asset_id':p.asset_id,'version':p.version,'purpose':p.purpose,
            'request_id':p.request_id or uuid.uuid4().hex,'fault_mode':p.mode,'ttl_seconds':.5 if p.mode=='timeout' else 30})
        token=result['contract']
        with closing(sqlite3.connect(token_db)) as db, db:
            db.execute('INSERT OR IGNORE INTO tokens VALUES(?,?)',(token['payload']['contract_id'],json.dumps(token)))
        return result
    @app.post('/api/deliver')
    def deliver(p: DeliveryInput):
        with closing(sqlite3.connect(token_db)) as db, db:
            row=db.execute('SELECT token FROM tokens WHERE contract_id=?',(p.contract_id,)).fetchone()
        if row is None: raise HTTPException(404,'本Consumer未持有该合同的原始授权令牌')
        token=json.loads(row[0])
        return client.json('POST',cfg['verifier_url']+'/verify-delivery',keys.sign('consumer',{'contract':token}),timeout=35)
    @app.get('/api/contracts')
    def list_contracts():
        return {'contracts':[{**item['contract'],'status':item['status'],'record':item['record']} for item in contracts()]}

    @app.get('/api/tasks')
    def tasks():
        with index.connection() as conn:
            result=[{'task_id':row[0],'economics_ids':[e['id'] for e in index.summary['economics']]} for row in conn.execute('SELECT DISTINCT task_id FROM utility ORDER BY task_id')]
        return {'tasks':result,'economics':index.summary['economics'],'default_task_id':cfg['task_id'],
            'utility_source':'FROZEN_VALIDATION_ONLY','heldout_rows':0}
    @app.get('/api/credit/{asset_id}')
    def credit(asset_id: str,version: str,origin: str='local_operational',method: str='decay30',at: str | None=None):
        if origin=='simulated':
            asset=get('/assets/'+asset_id,{'version':version})
            with index.connection() as conn:
                maps=[json.loads(row[0]) for row in conn.execute('SELECT row_json FROM legacy_mapping')]
            matches=[m for m in maps if m['asset_id']==asset_id and m['version']==version and m['content_hash']==asset['content_hash']]
            if len(matches)!=1: raise ValueError('NO_EXACT_LEGACY_MAPPING_FOR_ASSET_VERSION_HASH')
            mapping=matches[0]; historical_at=mapping['procurement_time']
            if at and parse_time(at)!=parse_time(historical_at):
                raise ValueError('历史模拟只覆盖冻结day180时点：'+historical_at)
            saved=post('/legacy/estimate_delivery_reliability',{'asset_id':asset_id,'version':version,
                'task_id':cfg['task_id'],'as_of_time':historical_at,'method':method,'economics_id':'fp1_fn5'})['response']
            r=saved['result']
            return {**r,'asset_id':asset_id,'version':version,'origin':origin,'source':'FROZEN_SIMULATED_MAPPING_NOT_LOCAL_EVENTS',
                'n_history':int(saved['mapping']['credit_n_history']),'successes':None,'failures':None,
                'as_of_time':historical_at,'method':method,'deployment_eligible':False,
                'diagnostics':{'frozen_mapping':saved['mapping']},'legacy_response':saved,
                'warning':'旧模拟资产到当前资产的显式假设映射；仅day180窗口，未从本地交付形成。成功/失败数未在索引保存，显示未知。'}
        return get('/credit/'+asset_id,{'version':version,'origin':origin,'method':method,'at':at})
    @app.get('/api/credit-series/{asset_id}')
    def series(asset_id: str,version: str,origin: str='local_operational',method: str='decay30',at: str | None=None):
        if origin=='simulated':
            r=credit(asset_id,version,origin,method,at)
            return {'points':[{'at':r['as_of_time'],'q':r['q'],'q_lower':r['q_lower'],'q_upper':r['q_upper'],
                'n_history':r['n_history'],'reason':'冻结历史模拟day180映射；无本地事件'}],
                'origin':origin,'window':'SINGLE_FROZEN_SIMULATED_WINDOW_NOT_LOCAL_TIME_SERIES'}
        until=parse_time(at or utcnow()); points=[]
        for event in all_audit():
            body=event['body']
            if body.get('asset_id')!=asset_id or body.get('asset_version',body.get('version'))!=version: continue
            if event['kind'] not in {'ASSET_REGISTERED','DELIVERY_VERIFIED','DELIVERY_DISPUTED_NOT_AUTOMATIC_FAILURE','ASSET_STATUS_CHANGED'}: continue
            if body.get('origin',origin)!=origin or parse_time(event['event_time'])>until: continue
            try:
                result=credit(asset_id,version,origin,method,event['event_time'])
                points.append({'at':event['event_time'],'q':result['q'],'q_lower':result['q_lower'],
                    'q_upper':result['q_upper'],'n_history':result['n_history'],'reason':event['kind']})
            except HTTPFailure: pass
        current=credit(asset_id,version,origin,method,until.isoformat())
        points.append({'at':until.isoformat(),'q':current['q'],'q_lower':current['q_lower'],'q_upper':current['q_upper'],
            'n_history':current['n_history'],'reason':'评估时点'})
        return {'points':points,'origin':origin,'interval_scope':'模型条件区间，未作商业风险校准'}
    def value_payload(p):
        data=p.dict(); data.pop('at',None)
        if p.at: parse_time(p.at); data['as_of_time']=p.at
        return data
    @app.post('/api/value')
    def value(p: ValueInput): return post('/valuations',value_payload(p))
    @app.post('/api/compare')
    def compare(p: CompareInput): return post('/valuations/compare',value_payload(p))

    @app.post('/api/assets/{asset_id}/status')
    def change(asset_id: str,p: StatusInput):
        asset=get('/assets/'+asset_id,{'version':p.version}); url=cfg['provider_urls'][asset['provider_id']]
        return client.json('POST',url+'/status-changes',keys.sign('consumer',{'asset_id':asset_id,**p.dict(exclude_none=True)}))
    @app.post('/api/disputes')
    def dispute(p: DisputeInput):
        return client.json('POST',cfg['verifier_url']+'/disputes',keys.sign('consumer',p.dict()))
    @app.get('/api/audit')
    def audit(asset_id: str | None=None,contract_id: str | None=None,origin: str | None=None,
              from_: str | None=None,to: str | None=None,request: Request=None):
        start=request.query_params.get('from') if request else from_
        if start: parse_time(start)
        if to: parse_time(to)
        result=[]; contractmap={c['contract_id']:c for c in get('/contracts')['contracts']}
        reports={r['report_id']:r['report'] for r in get('/reports')['reports']}
        for event in all_audit():
            body=event['body']; context=contractmap.get(body.get('contract_id'),reports.get(body.get('report_id'),{}))
            aid=body.get('asset_id',context.get('asset_id')); source=body.get('origin',context.get('origin',context.get('credit_origin')))
            if asset_id and aid!=asset_id or contract_id and body.get('contract_id')!=contract_id or origin and source!=origin: continue
            if start and parse_time(event['event_time'])<parse_time(start) or to and parse_time(event['event_time'])>parse_time(to): continue
            result.append({**event,'display_asset_id':aid,'display_origin':source})
        return {'events':result,'clock':'REGISTRY_OBSERVED_UTC'}
    @app.get('/api/reports')
    def reports(): return get('/reports')
    @app.get('/api/reports/{report_id}')
    def report(report_id: str): return get('/reports/'+report_id)
    @app.get('/api/reports/{report_id}/export')
    def export(report_id: str):
        return JSONResponse(content={**report(report_id),'exported_at':utcnow(),
            'limitations':'研究原型；假设损失单位，非人民币；非商业信用评级或法定资产评估。'},
            headers={'Content-Disposition':f'attachment; filename="{report_id}.json"'})
    @app.get('/api/reports/{report_id}/print',response_class=HTMLResponse)
    def printable(report_id: str):
        document=report(report_id); payload=document['report']; r=payload.get('result') or {}
        rows=[('资产/版本',f"{payload.get('asset_id','对比报告')} / {payload.get('asset_version','见明细')}"),
              ('任务/评估时点',f"{payload.get('task_id','见明细')} / {payload.get('as_of_time','见明细')}"),
              ('信用来源/有效合同',f"{r.get('q_source','见明细')} / {r.get('credit_n_history','见明细')}"),
              ('q / 成功收益 / 失败收益 / 成本', ' / '.join(str(r.get(k,'见明细')) for k in ('q','b_success','b_failure','fixed_cost'))),
              ('W = q × B_success + (1-q) × B_failure − C_fixed',str(r.get('value','见明细'))),
              ('固定q条件区间',f"[{r.get('conditional_low','见明细')}, {r.get('conditional_high','见明细')}]"),
              ('状态',str(r.get('state',payload.get('status','见明细'))))]
        table=''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(v)+'</td></tr>' for k,v in rows)
        raw=html.escape(json.dumps(document,ensure_ascii=False,indent=2))
        return '<!doctype html><html lang="zh"><meta charset="utf-8"><title>研究评估报告</title><style>body{font:16px sans-serif;max-width:1000px;margin:40px auto;line-height:1.7}th,td{border:1px solid #ccd;padding:12px;text-align:left}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}@media print{body{margin:0}details{display:block}}</style><h1>数据资产研究评估报告</h1><p>研究原型，非商业信用评级或法定数据资产估值。所有经济量为假设损失单位，不是人民币；区间不代表商业风险保证。</p><p>报告ID：'+html.escape(report_id)+'</p><table>'+table+'</table><h2>完整计算、参数、来源与证据</h2><pre>'+raw+'</pre></html>'

    dist=Path(cfg['frontend_dist'])
    if (dist/'assets').is_dir(): app.mount('/assets',StaticFiles(directory=dist/'assets'),name='static-assets')
    @app.get('/',response_class=FileResponse)
    def frontend():
        if not (dist/'index.html').is_file(): raise HTTPException(503,'请先构建frontend生产文件')
        return FileResponse(dist/'index.html')
    return app
