"""A generated empty workspace exercises the real local gateway without training."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from data_asset_poc.numeric_validation.gateway import create_coverage_gateway
from scripts.serve import prepare_workspace


class WorkspaceStartupTests(unittest.TestCase):
    def test_empty_workspace_real_gateway_and_boundaries(self):
        with tempfile.TemporaryDirectory(prefix='data_asset_engineering_') as tmp:
            parent = Path(tmp)
            cfg = prepare_workspace(parent / 'workspace', 12380)
            with self.assertRaises(FileExistsError):
                prepare_workspace(parent / 'workspace', 12380)
            with patch.dict('os.environ', {'VISUAL_EVIDENCE_LIVE_VISUAL_ADAPTER': ''}):
                app = create_coverage_gateway(cfg, project_root=parent)
                with TestClient(app) as client:
                    self.assertEqual(client.get('/health').json()['status'], 'OK')
                    self.assertEqual(client.get('/api/tasks').json()['tasks'], [])
                    self.assertEqual(client.get('/api/self-service/datasets').json()['datasets'], [])
                    self.assertEqual(client.get('/api/di-evidence/capabilities').json()['audit_availability'], 'NOT_AVAILABLE')
                    bad_audit = parent / 'results' / 'untrusted' / 'audit.json'
                    bad_audit.parent.mkdir(parents=True)
                    bad_audit.write_text('{}', encoding='utf-8')
                    self.assertEqual(client.get('/api/di-evidence/capabilities').json()['audit_availability'], 'NOT_AVAILABLE')
                    self.assertEqual(client.post('/api/self-service/datasets', content=b'a,b\n1,2\n').status_code, 403)
                    registered = client.post('/api/self-service/datasets', content=b'a,b\n1,2\n',
                        headers={'X-Research-Request':'1','Content-Type':'text/csv'})
                    self.assertEqual(registered.status_code, 200, registered.text)
                    self.assertEqual(client.get('/api/self-service/datasets').json()['datasets'][0]['row_count'], 1)

    def test_reject_source_tree_workspace_and_invalid_port(self):
        from scripts.serve import ROOT
        with self.assertRaisesRegex(ValueError, 'WORKSPACE_MUST_BE_OUTSIDE_SOURCE_TREE'):
            prepare_workspace(ROOT / 'new-workspace', 12380)
        with self.assertRaisesRegex(ValueError, 'PORT_MUST_BE_1024_TO_65535'):
            prepare_workspace(Path('invalid'), 80)

