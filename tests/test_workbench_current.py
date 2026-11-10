"""Real empty-workspace CSV route; no preloaded case or training output."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from data_asset_poc.numeric_validation.gateway import create_coverage_gateway
from scripts.serve import prepare_workspace

class CurrentWorkbenchTests(unittest.TestCase):
    def test_new_csv_keeps_generic_mode_and_unknown_evidence(self):
        with tempfile.TemporaryDirectory(prefix='current_csv_') as folder:
            root=Path(folder)
            config=prepare_workspace(root/'workspace',12380)
            with patch.dict('os.environ',{'VISUAL_EVIDENCE_LIVE_VISUAL_ADAPTER':''}):
                with TestClient(create_coverage_gateway(config,project_root=root)) as client:
                    options=client.get('/api/workbench/options').json()
                    self.assertEqual(options['mode'],'LOCAL_ASSESSMENT')
                    self.assertEqual(options['cases'],[])
                    headers={'X-Research-Request':'1','Content-Type':'text/csv'}
                    imported=client.post('/api/datasets/import?name=sample.csv',content=b'a,b\n1,2\n3,4\n',headers=headers)
                    self.assertEqual(imported.status_code,200,imported.text)
                    assessed=client.post('/api/workbench/assess',json={'kind':'import','identity':imported.json()['dataset_id'],'budget':20,'seed':101,'strategy':'srs'},headers={'X-Research-Request':'1'})
                    self.assertEqual(assessed.status_code,200,assessed.text)
                    report=assessed.json()
                    self.assertEqual(report['mode'],'LOCAL_ASSESSMENT')
                    self.assertEqual(report['source_evidence']['status'],'UNKNOWN')
                    self.assertFalse(report['source_evidence']['factual_accuracy_verified'])
                    self.assertEqual(report['value']['status'],'NOT_EVALUATED')
                    markdown=client.get('/api/workbench/reports/'+report['report_id']+'/markdown').text
                    self.assertIn('LOCAL_ASSESSMENT',markdown)
                    for text in ['REVEALED_DEMO','clinical','病理','答案揭示']:
                        self.assertNotIn(text,markdown)

if __name__=='__main__':unittest.main()
