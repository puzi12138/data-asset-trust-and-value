"""Selection mechanisms with explicit evidence, task compatibility and group factors.

All functions operate on the same candidate information; no test data is read.
The estimates are screening rules, not factual accuracy or calibrated utility.
"""
from __future__ import annotations
from collections import Counter
import math,random,statistics,time

FEATURE_NAMES=('present_fraction','finite_fraction','d0_squared_distance',
               'group_count','group_rarity','task_match',
               'source_verified','source_conflict','source_unknown')

def finite(x):
 return x is not None and not isinstance(x,bool) and isinstance(x,(float,int)) and math.isfinite(float(x))

def task_state(row,task):
 """Assess declared schema/label compatibility, never actual downstream benefit."""
 if not isinstance(task,dict) or task.get('type') not in ('classification','regression'):
  return 'UNKNOWN'
 expected=task.get('feature_count')
 if not isinstance(expected,int) or isinstance(expected,bool) or expected<=0:return 'UNKNOWN'
 x=row.get('x');y=row.get('y')
 if not isinstance(x,list) or len(x)!=expected:return 'CONFLICT'
 if any(v is not None and not finite(v) for v in x):return 'CONFLICT'
 if y is None:return 'UNKNOWN'
 if task['type']=='regression':return 'VERIFIED' if finite(y) else 'CONFLICT'
 labels=task.get('allowed_labels')
 if not isinstance(labels,list) or not labels:return 'UNKNOWN'
 return 'VERIFIED' if y in labels else 'CONFLICT'

def fit_d0_statistics(rows,d0):
 if not d0:raise ValueError('empty D0')
 dim=len(rows[d0[0]]['x']);med=[];scale=[]
 for j in range(dim):
  a=[float(rows[i]['x'][j]) for i in d0 if finite(rows[i]['x'][j])]
  m=statistics.median(a) if a else 0.
  filled=[float(rows[i]['x'][j]) if finite(rows[i]['x'][j]) else m for i in d0]
  avg=statistics.fmean(filled);std=math.sqrt(statistics.fmean((v-avg)**2 for v in filled)) or 1.
  med.append(m);scale.append(std)
 return {'median':med,'scale':scale,'feature_count':dim}

def features(rows,indices,stats,evidence,task):
 """Feature values use D0 stats only; each evidence value is an explicit state."""
 groups=Counter(rows[i]['group'] for i in indices);out=[]
 for i in indices:
  r=rows[i];x=r['x'];state=evidence.get(r['id'],'UNKNOWN')
  if state not in ('VERIFIED','CONFLICT','UNKNOWN'):state='UNKNOWN'
  match=task_state(r,task);d=0.
  for j,(med,sc) in enumerate(zip(stats['median'],stats['scale'])):
   v=x[j] if j<len(x) and finite(x[j]) else med;d+=((float(v)-med)/sc)**2
  count=groups[r['group']]
  out.append({'index':i,'id':r['id'],'group':r['group'],
   'present_fraction':sum(v is not None for v in x)/len(x) if x else 0.,
   'finite_fraction':sum(finite(v) for v in x)/len(x) if x else 0.,
   'd0_squared_distance':d/max(1,len(stats['median'])),
   'group_count':float(count),'group_rarity':1./count,
   'task_match':{'VERIFIED':1.,'UNKNOWN':.5,'CONFLICT':0.}[match],
   'task_state':match,'source_state':state,
   'source_verified':float(state=='VERIFIED'),'source_conflict':float(state=='CONFLICT'),
   'source_unknown':float(state=='UNKNOWN')})
 return out

def group_cap(ordered,k):
 """One per group per round in order of each group's best ranked member."""
 queues={}
 for r in ordered:queues.setdefault(r['group'],[]).append(r)
 chosen=[];level=0
 while len(chosen)<k:
  progressed=False
  for q in queues.values():
   if len(q)>level:
    chosen.append(q[level]);progressed=True
    if len(chosen)==k:break
  if not progressed:raise ValueError('k exceeds number of candidates')
  level+=1
 return chosen

def select_all(records,k,seed,learned_scores,shapley_scores=None,timing=None):
 """Return deterministic ordered records by strategy, with no hidden duplicate code."""
 if not 0<=k<=len(records) or len({r['id'] for r in records})!=len(records):raise ValueError('invalid candidates/k')
 if set(learned_scores)!={r['id'] for r in records}:raise ValueError('scores must cover pool')
 rs=list(records);random.Random(seed).shuffle(rs)
 prior={'VERIFIED':0,'UNKNOWN':1,'CONFLICT':2}
 evidence_key=lambda r:(prior[r['source_state']],-r['task_match'],r['d0_squared_distance'],r['id'])
 rf_key=lambda r:(-float(learned_scores[r['id']]),r['id'])
 full_key=lambda r:(prior[r['source_state']],-r['task_match'],-float(learned_scores[r['id']]),r['d0_squared_distance'],r['id'])
 def simple(r):return .45*r['present_fraction']+.25*r['finite_fraction']+.2/(1+r['d0_squared_distance'])+.1*r['group_rarity']
 rules={
  'random':lambda:rs[:k],
  'random_group':lambda:group_cap(rs,k),
  'evidence_only':lambda:sorted(records,key=evidence_key)[:k],
  'evidence_group':lambda:group_cap(sorted(records,key=evidence_key),k),
  'rf_source_conformity':lambda:sorted(records,key=rf_key)[:k],
  'rf_source_conformity_group':lambda:group_cap(sorted(records,key=rf_key),k),
  'evidence_rf_group':lambda:group_cap(sorted(records,key=full_key),k),
  'completeness':lambda:sorted(records,key=lambda r:(-r['present_fraction'],r['id']))[:k],
  'distance':lambda:sorted(records,key=lambda r:(r['d0_squared_distance'],r['id']))[:k],
  'fixed_quality_score':lambda:sorted(records,key=lambda r:(-simple(r),r['id']))[:k]}
 if shapley_scores is not None:
  if set(shapley_scores)!={r['id'] for r in records}:raise ValueError('Shapley must cover pool')
  rules['mc_data_shapley']=lambda:sorted(records,key=lambda r:(-float(shapley_scores[r['id']]),r['id']))[:k]
 o={}
 for method,rule in rules.items():
  before=time.perf_counter();o[method]=rule()
  if timing is not None:timing[method]=time.perf_counter()-before
 return {k:[r['index'] for r in v] for k,v in o.items()}
