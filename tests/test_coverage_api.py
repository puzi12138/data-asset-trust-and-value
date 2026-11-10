"""The unconfigured coverage API uses only server-resolved joint reports."""
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from data_asset_poc.numeric_validation import gateway
from tests import test_joint_api as prior


class CoverageWithoutAuditTests(unittest.TestCase):
    install_run = prior.JointAPITests.install_run
    post = prior.JointAPITests.post

    def setUp(self):
        prior.JointAPITests.setUp(self)
        self.joint = self.post().json()
        self.assertEqual(self.joint['status'], 'SAME_ASSET_BOUND')
        self.factory = patch.object(gateway, 'create_joint_gateway', return_value=self.app)
        self.factory.start()
        self.client.close()
        self.app = gateway.create_coverage_gateway(self.workspace / 'consumer.json', self.workspace)
        self.client = TestClient(self.app)
        self.url = '/api/di-evidence/' + self.joint['joint_id']

    def tearDown(self):
        self.factory.stop()
        prior.JointAPITests.tearDown(self)

    def test_same_version_is_not_audit_or_positive_fact(self):
        capabilities = self.client.get('/api/di-evidence/capabilities').json()
        self.assertEqual(capabilities['audit_availability'], 'NOT_AVAILABLE')
        self.assertIsNone(capabilities['reference_level'])
        report = self.client.get(self.url).json()
        self.assertEqual(report['status'], 'NOT_EVALUATED')
        self.assertEqual(report['decision']['fact_status'], 'UNKNOWN')
        self.assertFalse(report['decision']['trust_multiplier_applied'])
        self.assertEqual(report['task_value'], self.joint['task_value'])
        self.assertEqual(report['issues'][0]['code'], 'DI_AUDIT_NOT_CONFIGURED')

    def test_untrusted_files_do_not_activate_audit(self):
        arbitrary = Path(self.workspace) / 'results' / 'untrusted' / 'audit.json'
        arbitrary.parent.mkdir(parents=True)
        arbitrary.write_text('{"status":"VERIFIED"}', encoding='utf-8')
        self.assertEqual(self.client.get('/api/di-evidence/capabilities').json()['audit_availability'], 'NOT_AVAILABLE')
        self.assertEqual(self.client.get(self.url).json()['status'], 'NOT_EVALUATED')

    def test_rows_formats_and_identifiers_fail_closed(self):
        self.assertEqual(self.client.get(self.url + '/rows').json()['rows'], [])
        self.assertEqual(self.client.get(self.url + '/rows/0').status_code, 404)
        self.assertEqual(self.client.get(self.url + '/rows?limit=0').status_code, 422)
        self.assertEqual(self.client.get(self.url + '?format=md').status_code, 200)
        self.assertEqual(self.client.get(self.url + '?format=xml').status_code, 422)
        self.assertEqual(self.client.get('/api/di-evidence/not_a_joint').status_code, 404)
        self.assertEqual(self.client.get('/api/visual-review/case').status_code, 404)
