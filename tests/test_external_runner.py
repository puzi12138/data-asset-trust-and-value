"""Binding and admission checks; these tests do not fit models."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.external_experiments import common, run
from scripts.experiments import resources


class ExternalBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {'schema': 'external-experiment-v1', 'task_id': 'EXTERNAL-SMOKE',
                    'scope': 'SYNTHETIC_SMOKE_NON_SCIENTIFIC', 'dataset': 'synthetic',
                    'seed': 1103, 'task_type': 'classification', 'feature_count': 2,
                    'mutation_feature_index': 0, 'budgets': [128, 512],
                    'models': ['logistic', 'random_forest'], 'inputs': []}
        for k in common.INPUT_KEYS:
            name = k + '.json'; p = self.root / name
            # Deliberately invalid JSON: binding must not parse held-out labels.
            p.write_bytes(b'opaque input bytes')
            self.cfg[k] = name
            self.cfg['inputs'].append({'path': name, 'sha256': common.sha(p)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_binding_does_not_parse_heldout(self):
        self.assertTrue(common.validate_config(self.cfg, self.root))

    def test_changed_heldout_rejected(self):
        (self.root / self.cfg['test_input']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
            common.validate_config(self.cfg, self.root)

    def test_path_traversal_and_windows_drive_rejected(self):
        for bad in ['../escape', 'C:/escape', 'C:escape', '/escape', '\\server\\share', 'a/../b']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                common.safe(self.root, bad)

    def test_missing_input_binding_rejected(self):
        self.cfg['inputs'].pop()
        with self.assertRaisesRegex(ValueError, 'UNBOUND'):
            common.validate_config(self.cfg, self.root)

    def test_unimplemented_task_is_not_silently_accepted(self):
        # EXT-04 now has a regression candidate; an unknown ID must still fail closed.
        self.cfg['task_id'] = 'EXT-99'
        with self.assertRaisesRegex(ValueError, 'ADAPTER_NOT_IMPLEMENTED'):
            common.validate_config(self.cfg, self.root)

    def test_changed_matrix_rejected(self):
        self.cfg['budgets'] = [32, 128]
        with self.assertRaisesRegex(ValueError, 'MATRIX_NOT_FROZEN'):
            common.validate_config(self.cfg, self.root)

    def test_freeze_detects_source_change(self):
        c = self.root / 'config.json'; common.write(c, self.cfg)
        f = self.root / 'FREEZE.json'
        common.write(f, {'schema': 'external-code-freeze-v1', 'config_sha256': common.sha(c),
                         'code': common.code_manifest()})
        self.assertIsInstance(run.check_freeze(f, c, self.root), dict)
        with patch.object(run, 'code_manifest', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'CODE_FREEZE_MISMATCH'):
                run.check_freeze(f, c, self.root)

    def test_writes_refuse_overwrite(self):
        p = self.root / 'receipt.json'; common.write(p, {'original': True})
        with self.assertRaises(FileExistsError):
            common.write(p, {'changed': True})
        self.assertEqual(common.read(p), {'original': True})

    def test_resource_admission_stops_before_worker(self):
        with patch.object(resources.psutil, 'virtual_memory', return_value=SimpleNamespace(available=0)):
            result = resources.execute(self.root/'config', self.root, self.root/'output', 1,
                                       False, True)
        self.assertEqual(result['status'], 'NOT_RUN_RESOURCE_PREFLIGHT')
        self.assertFalse(result['worker_started'])

    def test_resource_guards_cannot_be_relaxed(self):
        for rss, floor in [(1.6, .75), (1.5, .74)]:
            with self.assertRaisesRegex(ValueError, 'RESOURCE_GUARD'):
                resources.execute(self.root/'config', self.root, self.root/'output', 1,
                                  False, True, None, rss, floor)

    def test_eighteen_unique_planned_arms(self):
        arms = common.planned_arms(self.cfg)
        self.assertEqual(len(arms), 18)
        self.assertEqual(len(set(arms)), 18)
        self.assertEqual(sum(x.endswith('__D0') for x in arms), 2)


if __name__ == '__main__':
    unittest.main()
