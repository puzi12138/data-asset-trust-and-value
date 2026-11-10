"""Task-conditioned, D0-anchored Monte Carlo Data Shapley for ridge regression.

Data Shapley: Ghorbani & Zou (2019), https://proceedings.mlr.press/v97/ghorbani19c.html.
U(S) is negative dev MAE, with frozen D0-only imputation/scaling and the same
ridge alpha / unpenalized intercept as the target model. No test data is used.
"""
from __future__ import annotations
import random,time
import numpy as np

def transform(rows,statistics):
 median=np.asarray(statistics['median'],dtype=float);scale=np.asarray(statistics['scale'],dtype=float)
 x=np.asarray([[np.nan if v is None else float(v) for v in r['x']] for r in rows],dtype=float)
 if x.ndim!=2 or x.shape[1]!=len(median) or np.any(~np.isfinite(scale)) or np.any(scale<=0):raise ValueError('invalid data/scale')
 x=np.where(np.isfinite(x),x,median)
 return (x-median)/scale

def data_shapley_ridge(d0_rows,pool_rows,dev_rows,statistics,seed,permutations=8,alpha=1.):
 """Return all permutations' exact sequential marginal values, no retraining of target arms.

 Uses Sherman-Morrison updates for the normal-equation inverse. Each marginal
 equals the change in negative dev MAE when that record joins D0 plus all
 earlier candidates in the permutation, up to floating-point roundoff.
 The resulting finite MC Shapley is an estimator, not exact all-permutation.
 """
 if not d0_rows or not pool_rows or not dev_rows or permutations<1 or alpha<=0:raise ValueError('invalid inputs')
 t0=time.perf_counter();d0=transform(d0_rows,statistics);pool=transform(pool_rows,statistics);dev=transform(dev_rows,statistics)
 y0=np.asarray([float(r['y']) for r in d0_rows]);yp=np.asarray([float(r['y']) for r in pool_rows]);yd=np.asarray([float(r['y']) for r in dev_rows])
 if not all(np.all(np.isfinite(x)) for x in [d0,pool,dev,y0,yp,yd]):raise ValueError('nonfinite')
 X0=np.column_stack([np.ones(len(d0)),d0]);X=np.column_stack([np.ones(len(pool)),pool]);Z=np.column_stack([np.ones(len(dev)),dev])
 penalty=np.eye(X.shape[1])*float(alpha);penalty[0,0]=0.
 gram=X0.T@X0+penalty;b0=X0.T@y0;base_inverse=np.linalg.inv(gram);w0=base_inverse@b0
 rng=random.Random(int(seed));n=len(pool);runs=[];preprocess=time.perf_counter()-t0
 for q in range(permutations):
  order=list(range(n));rng.shuffle(order);P=base_inverse.copy();w=w0.copy();pred=Z@w
  initial=-float(np.mean(np.abs(yd-pred)));previous=initial;contrib=np.zeros(n,dtype=float)
  for i in order:
   z=X[i];a=P@z;den=1.+float(z@a)
   if not np.isfinite(den) or den<=0:raise ArithmeticError('invalid update denominator')
   mult=(yp[i]-float(z@w))/den;w+=a*mult;pred+=(Z@a)*mult;P-=np.outer(a,a)/den
   utility=-float(np.mean(np.abs(yd-pred)));contrib[i]=utility-previous;previous=utility
  runs.append({'permutation':order,'contributions':contrib.tolist(),'utility_D0':initial,'utility_all':previous,'telescoping_error':float(abs(sum(contrib)-(previous-initial)))})
 arr=np.array([x['contributions'] for x in runs]);score=arr.mean(axis=0)
 return {'status':'COMPUTED','algorithm':'D0-conditional Monte Carlo Data Shapley, ridge/negative-MAE',
  'paper_url':'https://proceedings.mlr.press/v97/ghorbani19c.html','permutation_seed':seed,'permutations':runs,
  'permutation_count':permutations,'alpha':alpha,'scores':score.tolist(),
  'score_sd_across_permutations':arr.std(axis=0,ddof=1).tolist() if permutations>1 else None,
  'preprocessing_seconds':preprocess,'total_seconds':time.perf_counter()-t0,
  'information':'uses D0 and all candidate labels, independent dev labels; never test labels',
  'utility_scope':'negative dev MAE with fixed D0 preprocessing, unpenalized intercept, ridge(alpha)',
  'stability':'permutation standard deviations are descriptive, not independent dataset uncertainty'}
