"""Result PR intake must not execute code supplied by the result author."""
from pathlib import Path
import importlib.util,json,sys,tempfile,unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'scripts/experiments/check_submissions.py'
spec=importlib.util.spec_from_file_location('trusted_intake',path)
intake=importlib.util.module_from_spec(spec);spec.loader.exec_module(intake)

class IntakeBoundaryTests(unittest.TestCase):
    def test_duplicate_task_seed_prepares_once_verifies_each(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);candidate=root/'candidate'
            for name in ('one','two'):
                folder=candidate/'experiments/community/results/submissions'/name;folder.mkdir(parents=True)
                (folder/'RUN_MANIFEST.json').write_text(json.dumps({'task_id':'EXP-01','seed':1103}),encoding='utf8')
            def invoke(cmd,**kwargs):
                if cmd[2].endswith('verify_result.py'):
                    Path(cmd[cmd.index('--output')+1]).write_text(json.dumps({'status':'REPRODUCED'}),encoding='utf8')
                return type('Result',(),{'returncode':0})()
            with patch.object(sys,'argv',['check','--candidate',str(candidate),'--data-root',str(root/'data'),'--output',str(root/'out')]),patch.object(intake.subprocess,'run',side_effect=invoke) as runner:
                self.assertEqual(intake.main(),0)
                calls=[Path(c.args[0][2]).name for c in runner.call_args_list]
                self.assertEqual(calls,['prepare_data.py','verify_result.py','verify_result.py'])
            self.assertEqual(len(json.loads((root/'out/SUMMARY.json').read_text())['results']),2)

    def test_archived_results_do_not_exhaust_pr_limit_and_payload_changes_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);candidate=root/'candidate';baseline=root/'trusted'
            for checkout in (candidate,baseline):
                for i in range(5):
                    folder=checkout/'experiments/community/results/submissions'/str(i);folder.mkdir(parents=True)
                    (folder/'RUN_MANIFEST.json').write_text(json.dumps({'task_id':'EXP-01','seed':1103}),encoding='utf8')
                    (folder/'METRICS.json').write_text('{}',encoding='utf8')
            (candidate/'experiments/community/results/submissions/4/METRICS.json').write_text('{"changed":true}',encoding='utf8')
            def invoke(cmd,**kwargs):
                if cmd[2].endswith('verify_result.py'):
                    self.assertTrue(cmd[cmd.index('--result')+1].endswith('4'))
                    Path(cmd[cmd.index('--output')+1]).write_text(json.dumps({'status':'REJECTED'}),encoding='utf8')
                return type('Result',(),{'returncode':0})()
            with patch.object(sys,'argv',['check','--candidate',str(candidate),'--baseline',str(baseline),'--data-root',str(root/'data'),'--output',str(root/'out')]),patch.object(intake.subprocess,'run',side_effect=invoke) as runner:
                self.assertEqual(intake.main(),1);self.assertEqual(runner.call_count,2)

    def test_archived_result_deletion_needs_manual_review(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);baseline=root/'trusted';(baseline/'experiments/community/results/submissions/existing').mkdir(parents=True)
            with patch.object(sys,'argv',['check','--candidate',str(root/'candidate'),'--baseline',str(baseline),'--data-root',str(root/'data'),'--output',str(root/'out')]),patch.object(intake.subprocess,'run') as runner:
                with self.assertRaisesRegex(ValueError,'deletion'):intake.main()
                runner.assert_not_called()

    def test_schema_contains_all_terminal_runner_states(self):
        schema=json.loads((intake.ROOT/'experiments/community/schemas/run_manifest.schema.json').read_text(encoding='utf8'))
        expected={'COMPLETE','FAILED_WORKER_OR_RESOURCE','FAILED_OUTPUT_CHECK','FAILED_SUPERVISOR','NOT_RUN_PREFLIGHT_OR_BUDGET','NOT_RUN_INVALID_INPUT','NOT_RUN_RESOURCE_PREFLIGHT','NOT_RUN_DEPENDENCY_PREFLIGHT'}
        self.assertEqual(set(schema['properties']['status']['enum']),expected)

    def test_candidate_code_is_not_executed_without_results(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);candidate=root/'candidate';(candidate/'scripts/experiments').mkdir(parents=True)
            (candidate/'scripts/experiments/verify_result.py').write_text('raise RuntimeError("untrusted executable")',encoding='utf8')
            with patch.object(sys,'argv',['check','--candidate',str(candidate),'--data-root',str(root/'data'),'--output',str(root/'out')]),patch.object(intake.subprocess,'run') as runner:
                self.assertEqual(intake.main(),0);runner.assert_not_called()
            self.assertEqual(json.loads((root/'out/SUMMARY.json').read_text())['status'],'NO_SUBMISSIONS')

    def test_invalid_identity_cannot_select_executable_or_url(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);candidate=root/'candidate';folder=candidate/'experiments/community/results/submissions/one';folder.mkdir(parents=True)
            (folder/'RUN_MANIFEST.json').write_text(json.dumps({'task_id':'../../evil.py','seed':1103}),encoding='utf8')
            with patch.object(sys,'argv',['check','--candidate',str(candidate),'--data-root',str(root/'data'),'--output',str(root/'out')]),patch.object(intake.subprocess,'run') as runner:
                with self.assertRaisesRegex(ValueError,'Unrecognized'):intake.main()
                runner.assert_not_called()

    def test_no_automatic_acceptance_when_data_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);candidate=root/'candidate';folder=candidate/'experiments/community/results/submissions/one';folder.mkdir(parents=True)
            (folder/'RUN_MANIFEST.json').write_text(json.dumps({'task_id':'EXP-01','seed':1103}),encoding='utf8')
            with patch.object(sys,'argv',['check','--candidate',str(candidate),'--data-root',str(root/'data'),'--output',str(root/'out')]),patch.object(intake.subprocess,'run') as runner:
                runner.return_value.returncode=1
                self.assertEqual(intake.main(),1)
                cmd=runner.call_args.args[0]
                self.assertEqual(Path(cmd[2]),intake.ROOT/'scripts/experiments/prepare_data.py')
                self.assertNotIn(str(candidate),cmd[2])
            report=json.loads((root/'out/SUMMARY.json').read_text());self.assertFalse(report['scientific_acceptance']);self.assertEqual(report['status'],'NEEDS_REVIEW')

if __name__=='__main__':unittest.main()
