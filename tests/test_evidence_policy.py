import unittest
try:
    from .policy import run,MODES
except ImportError:  # direct script invocation from project root
    from data_asset_poc.task_verification.policy import run,MODES
class Tests(unittest.TestCase):
    def fixtures(self):
        records=[{'sample_id':str(i),'group_id':str(i//2)} for i in range(6)]
        actions=[{'action_id':a,'action_scope':'fixture','affected_records':ss,'requires':[]} for a,ss in [('a',['0','1','2']),('b',['1','2','3']),('c',['4','5'])]]
        def evaluate(obs):
            done=set(s for a in obs for s in next(x['affected_records'] for x in actions if x['action_id']==a))
            return {str(i):{'sample_id':str(i),'strict_decision':'REJECT' if str(i) in done else 'ABSTAIN'} for i in range(6)}
        return records,actions,evaluate
    def test_shared_priority_changes(self):
        r,a,e=self.fixtures();f=lambda a:{'seen':a};cost={x['action_id']:1 for x in a}
        s=run(r,a,cost,2,'static_greedy',f,e);d=run(r,a,cost,2,'shared_dynamic',f,e)
        self.assertEqual([t['action_id'] for t in s['trajectory']],['a','b'])
        self.assertEqual([t['action_id'] for t in d['trajectory']],['a','c'])
        self.assertEqual(sum(x['strict_decision']!='ABSTAIN' for x in d['rows']),5)
    def test_common_evidence_gate(self):
        r,a,e=self.fixtures();c={x['action_id']:1 for x in a}
        x=run(r,a,c,2,'static_greedy',lambda a:a,e);y=run(r,a,c,2,'same_information_gate',lambda a:a,e)
        self.assertEqual(x['rows'],y['rows'])
    def test_budget_and_zero(self):
        r,a,e=self.fixtures();c={x['action_id']:1 for x in a}
        for m in MODES:
            x=run(r,a,c,0,m,lambda a:1/0,e);self.assertEqual(x['action_count'],0)
            self.assertLessEqual(run(r,a,c,1.5,m,lambda a:a,e)['charged_local_ms'],1.5)
    def test_dependency(self):
        r,a,e=self.fixtures();a[0]['requires']=['c'];c={x['action_id']:1 for x in a}
        x=run(r,a,c,3,'full',lambda a:a,e);seq=[t['action_id'] for t in x['trajectory']]
        self.assertLess(seq.index('c'),seq.index('a'))
    def test_nonpositive_cost(self):
        r,a,e=self.fixtures()
        with self.assertRaises(ValueError):run(r,a,{x['action_id']:0 for x in a},1,'full',lambda a:a,e)
if __name__=='__main__':unittest.main()
