"""External worker protocol tests; no model fitting or external data access."""
import copy
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.external_experiments import worker


def fixture():
    rows = []
    for i in range(540):
        rows.append({'id': f'r{i:04}', 'source_row': i + 1, 'group': f'g{i:04}',
                     'x': [float(i)] * 16, 'y': 'A' if i % 2 == 0 else 'B'})
    d0 = list(range(20))
    pool = list(range(20, 540))
    sample = random.Random(1103 + 160000).sample(pool, 256)
    refs = [{'index': i, 'id': rows[i]['id'], 'x': list(rows[i]['x']), 'y': rows[i]['y']} for i in sample]
    data = {'rows': rows, 'roles': {'D0': d0, 'pool': pool},
            'task': {'type': 'classification', 'feature_count': 16, 'allowed_labels': ['A', 'B']}}
    return data, refs


class WorkerProtocolTests(unittest.TestCase):
    def test_direct_launch_cannot_create_output_before_go(self):
        with tempfile.TemporaryDirectory(prefix='external_worker_') as tmp:
            out = Path(tmp) / 'out'
            p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/external_experiments/worker.py'),
                                '--config', str(Path(tmp) / 'absent_config.json'), '--data-dir', tmp,
                                '--out', str(out)], input='', text=True, capture_output=True, timeout=15)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(out.exists())

    def test_invalid_go_stops_after_ready_before_parsing_or_output(self):
        with tempfile.TemporaryDirectory(prefix='external_worker_') as tmp:
            parent = psutil.Process(os.getpid())
            out = Path(tmp) / 'out'
            config = Path(tmp) / 'malformed.json'
            config.write_text('not JSON', encoding='utf8')
            challenge = {'kind': 'COMMUNITY_CHALLENGE_V1', 'nonce': 'a' * 64,
                         'pid': parent.pid, 'created': parent.create_time(),
                         'config': str(config), 'out': str(out), 'data_dir': tmp,
                         'worker_sha256': worker.sha(worker.__file__),
                         'config_sha256': worker.sha(config), 'origins': {}}
            p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/external_experiments/worker.py'),
                                '--config', str(config), '--data-dir', tmp, '--out', str(out)],
                               input=json.dumps(challenge) + '\n' + json.dumps({'kind': 'INVALID'}) + '\n',
                               text=True, capture_output=True, timeout=15)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(json.loads(p.stdout.splitlines()[0])['kind'], 'COMMUNITY_READY_V1')
            self.assertIn('NO_GO', p.stderr)
            self.assertFalse(out.exists())

    def test_exact_eighteen_arm_matrix(self):
        arms = [worker._arm_name(*v) for v in worker._plan()]
        self.assertEqual(len(arms), 18)
        self.assertEqual(len(set(arms)), 18)
        self.assertIn('logistic__D0', arms)
        self.assertIn('random_forest__k512__evidence_rf_group', arms)

    def test_probability_clipping_then_normalization(self):
        p = worker.normalized_probabilities([0.0, 0.3, 0.7])
        self.assertGreater(p[0], 0.)
        self.assertAlmostEqual(math.fsum(p), 1.)
        self.assertAlmostEqual(p[1] / p[2], 3 / 7)
        for values in ([], [float('nan'), 1], [-0.1, 1]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                worker.normalized_probabilities(values)

    def test_reference_states_and_unreferenced_unknown(self):
        data, refs = fixture()
        changed = refs[0]['index']
        data['rows'][changed]['y'] = 'B' if refs[0]['y'] == 'A' else 'A'
        _, d0, pool, _, evidence, audit = worker._validate_selection_input(data, refs, 1103)
        self.assertEqual(len(d0), 20)
        self.assertEqual(len(pool), 520)
        self.assertEqual(len(evidence), 256)
        self.assertEqual(evidence[refs[0]['id']], 'CONFLICT')
        self.assertEqual(sum(v == 'VERIFIED' for v in evidence.values()), 255)
        other = next(data['rows'][i]['id'] for i in pool if data['rows'][i]['id'] not in evidence)
        self.assertEqual(evidence.get(other, 'UNKNOWN'), 'UNKNOWN')
        self.assertEqual(len(audit), 256)

    def test_reject_pool_overlap_and_duplicate_group(self):
        data, refs = fixture()
        data['roles']['pool'][0] = data['roles']['D0'][0]
        with self.assertRaisesRegex(ValueError, 'DISJOINT'):
            worker._validate_selection_input(data, refs, 1103)
        data, refs = fixture()
        data['rows'][data['roles']['pool'][0]]['group'] = data['rows'][data['roles']['D0'][0]]['group']
        with self.assertRaisesRegex(ValueError, 'GROUP_LEAKAGE'):
            worker._validate_selection_input(data, refs, 1103)

    def test_reject_reference_mismatch_and_reordered_sample(self):
        data, refs = fixture()
        invalid = copy.deepcopy(refs)
        invalid[0]['id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'BAD_AUDIT_BINDING'):
            worker._validate_selection_input(data, invalid, 1103)
        with self.assertRaisesRegex(ValueError, 'AUDIT_SAMPLE_NOT_PROTOCOL_ORDER'):
            worker._validate_selection_input(data, list(reversed(refs)), 1103)

    def test_reject_unsorted_pool_and_d0_class_failure(self):
        data, refs = fixture()
        data['roles']['pool'].reverse()
        with self.assertRaisesRegex(ValueError, 'POOL_NOT_SOURCE_SORTED'):
            worker._validate_selection_input(data, refs, 1103)
        data, refs = fixture()
        for i in data['roles']['D0']:
            data['rows'][i]['y'] = 'A'
        with self.assertRaisesRegex(ValueError, 'D0_CLASSES'):
            worker._validate_selection_input(data, refs, 1103)

    def test_safe_paths_refuse_traversal(self):
        with tempfile.TemporaryDirectory(prefix='external_worker_paths_') as tmp:
            self.assertEqual(worker.safe(tmp, 'abc.json').name, 'abc.json')
            for value in ('../abc.json', 'a\\b', ''):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    worker.safe(tmp, value)


if __name__ == '__main__':
    unittest.main()
