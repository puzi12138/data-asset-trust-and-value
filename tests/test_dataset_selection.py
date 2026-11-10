import unittest
from data_asset_poc.assessment import selection as s

class SelectionTests(unittest.TestCase):
 def setUp(self):
  self.rows=[{'id':str(i),'group':str(i//2),'x':[float(i)],'y':('a' if i%2==0 else 'b')} for i in range(10)]
  self.stats=s.fit_d0_statistics(self.rows,[0,1]);self.pool=list(range(2,10));self.evidence={'2':'VERIFIED','4':'CONFLICT'}
  self.task={'type':'classification','feature_count':1,'allowed_labels':['a','b']}
  self.records=s.features(self.rows,self.pool,self.stats,self.evidence,self.task)
 def test_group_capacity(self):
  out=s.select_all(self.records,4,1,{r['id']:0. for r in self.records})
  for method in ['random_group','evidence_group','rf_source_conformity_group','evidence_rf_group']:
   self.assertEqual(len({self.rows[i]['group'] for i in out[method]}),4)
 def test_actual_task_switch_changes_selection(self):
  a=s.features(self.rows,self.pool,self.stats,{},dict(self.task,allowed_labels=['a']))
  b=s.features(self.rows,self.pool,self.stats,{},dict(self.task,allowed_labels=['b']))
  uniform={r['id']:0. for r in a}
  first_a=s.select_all(a,1,1,uniform)['evidence_only'][0]
  first_b=s.select_all(b,1,1,uniform)['evidence_only'][0]
  self.assertEqual(self.rows[first_a]['y'],'a');self.assertEqual(self.rows[first_b]['y'],'b')
  self.assertNotEqual(first_a,first_b)
 def test_unknown_not_assumed_true(self):
  self.assertEqual(s.task_state(self.rows[2],None),'UNKNOWN')
  self.assertEqual(next(x for x in self.records if x['id']=='3')['source_state'],'UNKNOWN')
 def test_conflicts_deferred_and_group_factors_can_differ(self):
  out=s.select_all(self.records,2,1,{r['id']:0. for r in self.records})
  self.assertEqual(out['evidence_only'][0],2);self.assertNotIn(4,out['evidence_only'])
  self.assertEqual([r['index'] for r in s.group_cap(self.records,2)],[2,4])
  self.assertNotEqual([r['index'] for r in self.records[:2]],[r['index'] for r in s.group_cap(self.records,2)])
 def test_method_timings_and_sample_budget(self):
  timings={};o=s.select_all(self.records,3,1,{r['id']:0. for r in self.records},timing=timings)
  self.assertEqual(set(o),set(timings));self.assertTrue(all(len(v)==3 and len(set(v))==3 for v in o.values()))
 def test_missing_task_model_and_nonfinite_do_not_look_verified(self):
  self.assertEqual(s.task_state({'x':[float('nan')],'y':'a'},self.task),'CONFLICT')
  self.assertEqual(s.task_state({'x':[1.],'y':'a'},{'type':'classification','feature_count':1}), 'UNKNOWN')

if __name__=='__main__':unittest.main(verbosity=2)
