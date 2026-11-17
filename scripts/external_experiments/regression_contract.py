"""Strict regression input checks shared by worker and independent verifier.

No model fitting and no access to labels outside the explicitly passed objects.
"""
import datetime
import math
import random
import re
from urllib.parse import urlparse

BIKE_FEATURES = ['season','yr','mnth','hr','holiday','weekday','workingday',
                 'weathersit','temp','atemp','hum','windspeed']


def numeric(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def validate_selection(data, reference, cfg):
    rows,roles,task=data['rows'],data['roles'],data['task']
    d0,pool=list(roles['D0']),list(roles['pool'])
    if task.get('type')!='regression' or task.get('feature_count')!=cfg['feature_count']:
        raise ValueError('REGRESSION_TASK_SCHEMA')
    if cfg['task_id'] in ('EXT-04','REGRESSION-SMOKE') and task.get('feature_names')!=BIKE_FEATURES:
        raise ValueError('BIKE_FEATURE_ALLOWLIST_ORDER')
    if cfg['task_id']=='EXT-03':
        names=task.get('feature_names')
        if (not isinstance(names,list) or len(names)!=81 or len(set(names))!=81 or
                any(not isinstance(n,str) or not n or n.lower() in ('critical_temp','material','chemical_formula') for n in names)):
            raise ValueError('SUPERCONDUCTIVITY_FEATURE_ALLOWLIST')
    if not d0 or len(pool)<512 or len(reference)!=256:
        raise ValueError('NOT_RUN_INSUFFICIENT_POOL_OR_REFERENCE')
    if cfg['task_id']=='EXT-03' and (len(d0)<512 or len(pool)<8192):
        raise ValueError('NOT_RUN_INSUFFICIENT_REAL_SUPERCONDUCTIVITY_INPUT')
    if any(type(i) is not int or not 0<=i<len(rows) for i in d0+pool):raise ValueError('BAD_ROW_INDEX')
    if len(set(d0+pool))!=len(d0)+len(pool) or set(d0+pool)!=set(range(len(rows))):
        raise ValueError('D0_POOL_INDICES_NOT_DISJOINT_AND_COMPLETE')
    if len({r['id'] for r in rows})!=len(rows) or len({r['source_row'] for r in rows})!=len(rows):raise ValueError('DUPLICATE_MEMBER')
    if [r['source_row'] for r in rows]!=sorted(r['source_row'] for r in rows):raise ValueError('ROWS_NOT_SOURCE_SORTED')
    if [rows[i]['source_row'] for i in pool]!=sorted(rows[i]['source_row'] for i in pool):raise ValueError('POOL_NOT_SOURCE_SORTED')
    if {rows[i]['group'] for i in d0}&{rows[i]['group'] for i in pool}:raise ValueError('D0_POOL_GROUP_LEAKAGE')
    for r in rows:
        if not isinstance(r['id'],str) or not r['id'] or not isinstance(r['group'],str) or not r['group']:
            raise ValueError('BAD_MEMBER_ID_OR_GROUP')
        if not isinstance(r['x'],list) or len(r['x'])!=cfg['feature_count'] or any(x is not None and not numeric(x) for x in r['x']):raise ValueError('BAD_FEATURE_VECTOR')
        if not numeric(r['y']):raise ValueError('BAD_REGRESSION_LABEL')
    if cfg['task_id'] in ('EXT-04','REGRESSION-SMOKE'):validate_bike_rows(rows)
    pool_set=set(pool)
    if len({r['index'] for r in reference})!=256:raise ValueError('DUPLICATE_AUDIT_REFERENCE')
    evidence,audit={},[]
    for r in reference:
        i=r['index']
        if i not in pool_set or rows[i]['id']!=r['id']:raise ValueError('BAD_AUDIT_BINDING')
        state='VERIFIED' if rows[i]['x']==r['x'] and rows[i]['y']==r['y'] else 'CONFLICT'
        evidence[r['id']]=state
        audit.append({'index':i,'id':r['id'],'state':state,'scope':'SAME_SOURCE_VERSION_COMPARISON_NOT_FACTUAL_ORACLE'})
    if [r['index'] for r in reference]!=random.Random(cfg['seed']+160000).sample(pool,256):raise ValueError('AUDIT_SAMPLE_NOT_PROTOCOL_ORDER')
    return rows,d0,pool,task,evidence,audit


def validate_source_binding(data,members,source,cfg):
    """Bind declared source data; actual authenticity/row alignment is external evidence."""
    raw=source.get('raw_sha256')
    if source.get('dataset')!=cfg['dataset'] or not isinstance(raw,str) or not re.fullmatch('[0-9a-f]{64}',raw):
        raise ValueError('SOURCE_METADATA_MISMATCH')
    if data.get('asset_metadata',{}).get('source_sha256')!=raw or members.get('source_sha256')!=raw or members.get('seed')!=cfg['seed']:
        raise ValueError('SOURCE_OR_MEMBERS_BINDING_MISMATCH')
    if cfg['task_id'] in ('EXT-03','EXT-04'):
        u=urlparse(source.get('source_url',''))
        if u.scheme!='https' or u.hostname!='archive.ics.uci.edu' or source.get('license')!='CC BY 4.0':raise ValueError('SOURCE_OR_LICENSE_MISMATCH')
    if cfg['task_id']=='EXT-03':
        m=source.get('materials_sha256')
        if not isinstance(m,str) or not re.fullmatch('[0-9a-f]{64}',m) or source.get('alignment_status')!='SOURCE_SUPPORTED':
            raise ValueError('SUPERCONDUCTIVITY_ALIGNMENT_UNRESOLVED')


def validate_bike_rows(rows):
    ids=set()
    for r in rows:
        if r.get('split_group')!=r.get('group'):raise ValueError('BIKE_SPLIT_GROUP_MISMATCH')
        try:datetime.date.fromisoformat(r['group'])
        except (TypeError,ValueError):raise ValueError('BIKE_INVALID_DATE') from None
        h=r['x'][3]
        if not numeric(h) or int(h)!=h or not 0<=h<=23:raise ValueError('BIKE_INVALID_HOUR')
        identity=f"{r['group']}:{int(h):02d}"
        if r.get('record_identity')!=identity or identity in ids:raise ValueError('BIKE_DUPLICATE_OR_INVALID_HOUR_IDENTITY')
        ids.add(identity)


def validate_roles(data,dev,test,cfg,members):
    rows=data['rows']; roles=data['roles'];d0=[rows[i] for i in roles['D0']];pool=[rows[i] for i in roles['pool']]
    splits={'D0':d0,'pool':pool,'dev':dev,'test':test}
    for name,records in splits.items():
        if not records or any(not numeric(r['y']) for r in records):raise ValueError('EMPTY_OR_INVALID_HELDOUT_'+name)
        if len({r['id'] for r in records})!=len(records):raise ValueError('DUPLICATE_ID_'+name)
        expected=[{key:r[key] for key in ('source_row','id','group')} for r in records]
        if members['roles'][name]!=expected:raise ValueError('FROZEN_MEMBERS_MISMATCH_'+name)
    for i,a in enumerate(splits):
        for b in list(splits)[i+1:]:
            if {r['id'] for r in splits[a]}&{r['id'] for r in splits[b]} or {r['group'] for r in splits[a]}&{r['group'] for r in splits[b]}:
                raise ValueError('ROLE_GROUP_OR_ID_LEAKAGE_'+a+'_'+b)
    if cfg['task_id'] in ('EXT-04','REGRESSION-SMOKE'):
        for records in splits.values():validate_bike_rows(records)
        groups={k:{r['group'] for r in v} for k,v in splits.items()}
        buffer=members['roles'].get('buffer',[])
        bg={r['group'] for r in buffer}
        if any(bg&v for v in groups.values()):raise ValueError('BUFFER_GROUP_LEAKAGE')
        all_days=sorted(bg|set().union(*groups.values()));n=len(all_days)-6
        if n<=0:raise ValueError('INSUFFICIENT_DAYS')
        nd0=int(.20*n);npool=int(.40*n);ndev=int(.15*n)
        j=0;expected={}
        for role,length in [('D0',nd0),('buffer1',2),('pool',npool),('buffer2',2),('dev',ndev),('buffer3',2)]:
            expected[role]=set(all_days[j:j+length]);j+=length
        expected['test']=set(all_days[j:])
        if any(groups[k]!=expected[k] for k in groups) or bg!=expected['buffer1']|expected['buffer2']|expected['buffer3']:
            raise ValueError('BIKE_DATE_PARTITION_MISMATCH')
    return splits
