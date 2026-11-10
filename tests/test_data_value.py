"""Numerical reference uses direct numpy linear solves, not model training."""
import random,unittest
import numpy as np
from data_asset_poc.assessment.value_methods import data_shapley_ridge,transform
class ShapleyAlgebraTests(unittest.TestCase):
 def test_marginals_match_independent_direct_linear_solve(self):
  d0=[{'x':[0.,0.],'y':0.},{'x':[1.,0.],'y':2.},{'x':[0.,1.],'y':3.}]
  pool=[{'x':[1.,1.],'y':4.},{'x':[2.,0.],'y':5.},{'x':[2.,2.],'y':1.}]
  dev=[{'x':[0.5,0.5],'y':1.7},{'x':[1.5,1.],'y':3.6}]
  stats={'median':[.5,.5],'scale':[1.,2.]};r=data_shapley_ridge(d0,pool,dev,stats,seed=128,permutations=8,alpha=1.)
  self.assertEqual(r['permutation_count'],8)
  def utility(train):
   x=transform(train,stats);x=np.column_stack((np.ones(len(x)),x));y=np.array([r['y'] for r in train]);z=transform(dev,stats);z=np.column_stack((np.ones(len(z)),z));pen=np.diag([0.,1.,1.]);w=np.linalg.solve(x.T@x+pen,x.T@y)
   return -float(np.mean(np.abs(np.array([r['y'] for r in dev])-z@w)))
  sums=np.zeros(len(pool))
  for run in r['permutations']:
   train=list(d0);old=utility(train)
   for i in run['permutation']:
    train.append(pool[i]);new=utility(train)
    self.assertAlmostEqual(run['contributions'][i],new-old,places=10)
    sums[i]+=new-old;old=new
   self.assertLess(run['telescoping_error'],1e-12)
  np.testing.assert_allclose(r['scores'],sums/8,rtol=1e-10,atol=1e-10)
 def test_empty_dev_rejected(self):
  with self.assertRaises(ValueError):data_shapley_ridge([{'x':[1.],'y':2.}],[{'x':[2.],'y':2.}],[],{'median':[1.],'scale':[1.]},1)
 def test_fixed_d0_imputation_does_not_consult_development(self):
  result=transform([{'x':[None,9.]}],{'median':[3.,5.],'scale':[2.,2.]})
  np.testing.assert_allclose(result,[[0.,2.]])

if __name__=='__main__':unittest.main(verbosity=2)
