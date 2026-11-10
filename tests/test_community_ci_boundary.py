"""Result PR intake must not execute code supplied by the result author."""
from pathlib import Path
import importlib.util,json,sys,tempfile,unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'scripts/experiments/check_submissions.py'
spec=importlib.util.spec_from_file_location('trusted_intake',path)
intake=importlib.util.module_from_spec(spec);spec.loader.exec_module(intake)

class IntakeBoundaryTests(unittest.TestCase):
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
