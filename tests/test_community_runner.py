"""Supervisor and protocol-boundary checks without fitting any models."""
from pathlib import Path
from unittest import TestCase,mock
import json,os,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.experiments import run_task,resources

FIXTURE=ROOT/'tests/fixtures/community_smoke'
CFG=FIXTURE/'config.json'

class RunnerValidationTests(TestCase):
    def test_synthetic_fixture_hashes_and_scope(self):
        c=run_task.read(CFG);run_task.validate_inputs(c,FIXTURE)
        self.assertEqual(c['scope'],'SYNTHETIC_SMOKE_NON_SCIENTIFIC')
    def test_reject_changed_hash(self):
        c=run_task.read(CFG);c['inputs'][0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'INPUT_HASH_MISMATCH'):run_task.validate_inputs(c,FIXTURE)
    def test_reject_duplicate_paths(self):
        c=run_task.read(CFG);c['inputs'].append(c['inputs'][0].copy())
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):run_task.validate_inputs(c,FIXTURE)
    def test_reject_input_path_escape(self):
        with self.assertRaises(ValueError):run_task.safe(FIXTURE,'../config.json')
    def test_real_task_ids_and_seeds_are_restricted(self):
        with self.assertRaises(ValueError):run_task.resolve_config('EXP-07',1103)
        with self.assertRaises(ValueError):run_task.resolve_config('EXP-01',7)
    def test_resource_guards_cannot_be_relaxed(self):
        with self.assertRaisesRegex(ValueError,'RESOURCE_GUARD'):
            resources.execute(CFG,FIXTURE,'unused',300,max_rss_gib=2.)
        with self.assertRaisesRegex(ValueError,'RESOURCE_GUARD'):
            resources.execute(CFG,FIXTURE,'unused',300,min_free_gib=.5)
    def test_resource_preflight_does_not_spawn(self):
        with mock.patch.object(resources.psutil,'virtual_memory',return_value=mock.Mock(available=0)):
            with mock.patch.object(resources.subprocess,'Popen') as spawn:
                x=resources.execute(CFG,FIXTURE,'unused',300,smoke=True)
        self.assertEqual(x['status'],'NOT_RUN_RESOURCE_PREFLIGHT');self.assertFalse(x['worker_started']);spawn.assert_not_called()
    def test_worker_direct_launch_fails_before_go(self):
        with tempfile.TemporaryDirectory(prefix='community_runner_') as d:
            out=Path(d)/'worker_output'
            x=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/experiments/worker.py'),'--config',str(CFG),'--data-dir',str(FIXTURE),'--out',str(out)],input='',text=True,capture_output=True,timeout=10)
            self.assertNotEqual(x.returncode,0);self.assertFalse(out.exists())
    def test_dry_run_no_output_or_training(self):
        with tempfile.TemporaryDirectory(prefix='community_runner_') as d:
            out=Path(d)/'unused'
            x=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/experiments/run_task.py'),'--task-id','SMOKE-01','--seed','1103','--output',str(out),'--dry-run'],capture_output=True,text=True,timeout=20)
            self.assertIn(x.returncode,(0,2));payload=json.loads(x.stdout)
            self.assertTrue(payload['input_hashes_verified']);self.assertFalse(payload['training_started']);self.assertFalse(out.exists())
    def test_invalid_input_has_stopped_receipt(self):
        with tempfile.TemporaryDirectory(prefix='community_runner_') as d:
            parent=Path(d);out=parent/'results';empty=parent/'empty';empty.mkdir()
            x=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/experiments/run_task.py'),'--task-id','SMOKE-01','--seed','1103','--data-dir',str(empty),'--output',str(out)],capture_output=True,text=True,timeout=20)
            self.assertEqual(x.returncode,2);payload=json.loads(x.stdout);self.assertEqual(payload['status'],'NOT_RUN_INVALID_INPUT')
            run=out/payload['run_id'];m=run_task.read(run/'RUN_MANIFEST.json');cost=run_task.read(run/'COST.json')
            self.assertFalse(cost['worker_monitor']['worker_started']);self.assertNotIn(str(parent),json.dumps(m));self.assertEqual(run_task.read(run/'VERIFY_REPORT.json')['status'],'NEEDS_REVIEW')
    def test_budget_blocks_unresolved_reservation(self):
        with tempfile.TemporaryDirectory(prefix='community_runner_') as d:
            a=run_task.Budget(d,False,1,'run-a');a.reserve();a.close()
            b=run_task.Budget(d,False,1,'run-b')
            try:
                with self.assertRaisesRegex(ValueError,'UNRESOLVED_BUDGET_RESERVATION'):b.reserve()
            finally:b.close()
    def test_budget_refunds_unused_reservation(self):
        with tempfile.TemporaryDirectory(prefix='community_runner_') as d:
            a=run_task.Budget(d,False,20,'run-a');a.reserve();a.finish(3.5);a.close()
            self.assertEqual(run_task.read(Path(d)/'.community-budget.json')['spent_seconds'],3.5)

if __name__=='__main__':
    import unittest;unittest.main()
