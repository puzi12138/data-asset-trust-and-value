"""Covertype contracts and replay checks using generated records only."""
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.experiments import resources
from scripts.external_experiments import common, prepare_covertype as adapter, verify, worker


def fixture_line(row, label):
    features = [3000 + row, 25, 12, 150, -9, 118, 190, 160, 40, 700]
    flags = [int(i in (0, 4)) for i in range(44)]
    return (','.join(map(str, [*features, *flags, label])) + '\n').encode('ascii')


class CovertypeFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='covertype-fixture-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.raw = cls.root / 'fixture.data'
        cls.source = cls.root / 'source.json'
        cls.rows = 612
        lines = [fixture_line(row, 1 + row % 7) for row in range(1, cls.rows + 1)]
        lines[1] = fixture_line(1, 7)  # The same feature vector can have distinct labels.
        cls.raw.write_bytes(b''.join(lines))
        record = {'dataset': 'Covertype', 'source_url': adapter.SOURCE_URL, 'license': 'CC BY 4.0',
                  'copyright_notice': 'Copyright Jock A. Blackard and Colorado State University',
                  'raw_size_bytes': cls.raw.stat().st_size, 'raw_sha256': common.sha(cls.raw)}
        common.write(cls.source, record)
        cls.data = cls.root / 'prepared'
        cls.minimums = {'D0': 24, 'pool': 520, 'dev': 20, 'test': 30}
        cls.prepared = adapter.prepare(cls.raw, cls.source, 1103, cls.data,
                                       expected_rows=cls.rows, minimums=cls.minimums)
        cls.config = common.read(cls.data / 'config.json')
        cls.original = [(list(map(int, line.split(b',')[:-1])),
                         line.split(b',')[-1].strip().decode('ascii')) for line in lines]

    def test_integer_fields_and_seven_class_schema(self):
        _, numbers, flags, label = adapter.decode_row(fixture_line(1, 7), 1)
        self.assertEqual((len(numbers) + len(flags), numbers[0], numbers[4], label), (54, 3001, -9, 7))
        for bad, expected in ((b'1,2\n', 'INVALID_COLUMNS'), (fixture_line(1, 8), 'INVALID_LABEL'),
                              (fixture_line(1, 7).replace(b',1,0,', b',2,0,', 1), 'INVALID_FLAG')):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                adapter.decode_row(bad, 1)
        self.assertTrue(common.validate_config(self.config, self.data))
        self.assertEqual(adapter.LABELS, tuple('1234567'))
        for wrong in ({'feature_count': 16}, {'task_id': 'EXT-99'}, {'models': ['ridge', 'random_forest']}):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                common.validate_config({**self.config, **wrong}, self.data)

    def test_assignment_matches_published_group_order_and_keeps_repeats_in_one_role(self):
        groups = {}
        for position, (x, label) in enumerate(self.original, 1):
            groups.setdefault(tuple(x), []).append((position, label))
        groups_by_key = {','.join(str(row) for row, _ in group): group for group in groups.values()}
        source_hash = common.sha(self.raw)
        ranked = sorted(groups_by_key, key=lambda key:
                        (hashlib.sha256(f'{source_hash}:1103:{key}'.encode()).hexdigest(), key))
        self.assertEqual(self.prepared['raw_exact_groups'], len(groups))
        members = common.read(self.data / 'MEMBERS.json')['roles']
        seen = set()
        for name, entries in members.items():
            with self.subTest(role=name):
                keys = {record['group'] for record in entries}
                self.assertFalse(seen & keys)
                seen |= keys
                actual = {record['source_row'] for record in entries}
                self.assertEqual(actual, {row for key in keys for row, _ in groups_by_key[key]})
        self.assertEqual(set(groups_by_key), seen)
        coverage = set(adapter.LABELS)
        d0 = []
        for key in ranked:
            present = {label for _, label in groups_by_key[key]}
            if present & coverage:
                d0.append(key)
                coverage -= present
            if not coverage:
                break
        ranked = [key for key in ranked if key not in d0]
        expected = {'D0': d0}
        for role, minimum in self.minimums.items():
            selected = expected.setdefault(role, [])
            while sum(len(groups_by_key[key]) for key in selected) < minimum:
                selected.append(ranked.pop(0))
            self.assertEqual(set(selected), {r['group'] for r in members[role]})
        self.assertEqual(set(ranked), {r['group'] for r in members['unassigned']})
        self.assertEqual({self.original[r['source_row'] - 1][1] for r in members['D0']}, set(adapter.LABELS))

    def test_reference_injection_and_role_isolation(self):
        selection = common.read(self.data / 'selection_input.json')
        refs = common.read(self.data / 'audit_reference.json')['records']
        truth = common.read(self.data / 'injection_truth.json')['records']
        rows = selection['rows']
        pool = selection['roles']['pool']
        self.assertEqual([ref['index'] for ref in refs], random.Random(1103 + 160000).sample(pool, 256))
        for ref in refs:
            source_row = rows[ref['index']]['source_row']
            self.assertEqual((ref['x'], ref['y']), self.original[source_row - 1])
        changed = random.Random(1103 + 170000).sample(pool, math.floor(.2 * len(pool)))
        self.assertEqual([entry['index'] for entry in truth], changed)
        d0 = [rows[i] for i in selection['roles']['D0']]
        elevation = [r['x'][0] for r in d0]
        extreme = max(elevation) + 3 * statistics.pstdev(elevation)
        for i, entry in enumerate(truth):
            stage = rows[entry['index']]
            x, label = self.original[stage['source_row'] - 1]
            self.assertEqual((entry['original_x'], entry['original_y']), (x, label))
            if i < len(truth) // 2:
                self.assertEqual((entry['kind'], stage['x'], stage['y']), ('label', x, str(int(label) % 7 + 1)))
            else:
                self.assertEqual((entry['kind'], stage['x'], stage['y']), ('feature', [extreme] + x[1:], label))
        dev = common.read(self.data / 'development_input.json')['records']
        test = common.read(self.data / 'test_input.json')['records']
        self.assertEqual(verify._separate(selection, dev, test)[0], rows)
        for record in d0 + dev + test:
            self.assertEqual((record['x'], record['y']), self.original[record['source_row'] - 1])
        _, _, _, _, evidence, _ = worker._validate_selection_input(selection, refs, 1103, 'EXT-01')
        self.assertEqual(set(evidence), {reference['id'] for reference in refs})
        self.assertIn('CONFLICT', evidence.values())
        self.assertIn('VERIFIED', evidence.values())
        self.assertEqual(evidence.get(next(rows[i]['id'] for i in pool if rows[i]['id'] not in evidence), 'UNKNOWN'), 'UNKNOWN')
        selection['task']['allowed_labels'] = list('1234568')
        with self.assertRaisesRegex(ValueError, 'CLASSIFICATION_LABEL_SET_MISMATCH'):
            worker._validate_selection_input(selection, refs, 1103, 'EXT-01')

    def test_hash_validation_and_no_overwrite(self):
        fake = {**self.config, 'inputs': [dict(item) for item in self.config['inputs']]}
        fake['inputs'][0]['sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'INPUT_HASH_MISMATCH'):
            common.validate_config(fake, self.data)
        with self.assertRaises(FileExistsError):
            adapter.prepare(self.raw, self.source, 1103, self.data)
        self.assertEqual(common.read(self.data / 'config.json'), self.config)

    def test_repeat_preparation_preserves_all_input_and_config_bytes(self):
        repeat = self.root / 'repeat-prepared'
        adapter.prepare(self.raw, self.source, 1103, repeat,
                        expected_rows=self.rows, minimums=self.minimums)
        for previous in self.data.iterdir():
            self.assertEqual(previous.read_bytes(), (repeat / previous.name).read_bytes(), previous.name)

    def test_no_heldout_read_or_output_before_supervisor_go(self):
        output = self.root / 'direct-worker-output'
        result = subprocess.run([sys.executable, '-B', str(Path(worker.__file__)), '--config',
                                 str(self.data / 'config.json'), '--data-dir', str(self.data), '--out', str(output)],
                                input='', text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_selection_paths_and_budgets_are_frozen(self):
        input_keys = ('selection_input', 'audit_reference', 'development_input', 'test_input', 'injection_truth')
        self.assertEqual(len({self.config[k] for k in input_keys}), 5)
        plan = worker._plan()
        for model in self.config['models']:
            self.assertEqual(sorted(k for actual, _, k in plan if actual == model),
                             [0, 128, 128, 128, 128, 512, 512, 512, 512])
        self.assertEqual([worker._arm_name(*arm) for arm in plan], common.planned_arms(self.config))

    def test_heldout_files_are_first_parsed_after_selection_freeze(self):
        import numpy as np

        class ConstantDetector:
            classes_ = np.array([0, 1])
            estimators_ = []

            def fit(self, x, y):
                return self

            def get_params(self):
                return {}

            def predict_proba(self, features):
                return np.full((len(features), 2), 0.5)

        class FirstHeldoutRead(Exception):
            pass

        output = self.root / 'selection-order-output'
        protected = {self.config[key] for key in ('development_input', 'test_input', 'injection_truth')}
        original_read = worker.read
        reads = []

        def observe_read(path):
            if Path(path).name in protected:
                reads.append(Path(path).name)
                selection = common.read(output / 'SELECTION_FREEZE.json')
                self.assertEqual(len(selection['methods']), 8)
                self.assertTrue((output / 'HELDOUT_ACCESS.json').is_file())
                raise FirstHeldoutRead()
            return original_read(path)

        arguments = SimpleNamespace(config=self.data / 'config.json', data_dir=self.data, out=output)
        # Isolate data-order logic from the separately exercised supervisor handshake.
        with (patch.object(worker, 'authorize', return_value={'handshake': 'TEST_FIXTURE'}),
              patch.object(worker, 'read', side_effect=observe_read),
              patch('sklearn.ensemble.RandomForestClassifier', return_value=ConstantDetector())):
            with self.assertRaises(FirstHeldoutRead):
                worker.run(arguments)
        self.assertEqual(reads, [self.config['development_input']])

    def test_memory_guard_stops_before_worker(self):
        output = self.root / 'resource-stop-output'
        with patch.object(resources.psutil, 'virtual_memory', return_value=SimpleNamespace(available=0)):
            receipt = resources.execute(self.data / 'config.json', self.data, output, 1, False, True)
        self.assertEqual(receipt['status'], 'NOT_RUN_RESOURCE_PREFLIGHT')
        self.assertFalse(receipt['worker_started'])
        self.assertFalse(output.exists())

    def test_replay_seven_probability_columns_and_metrics(self):
        model = {'model': 'LogisticRegression', 'classes': list(adapter.LABELS),
                 'preprocess_D0': {'median': [0.] * 54, 'scale': [1.] * 54, 'feature_count': 54},
                 'coef': [[0.] * 54 for _ in adapter.LABELS], 'intercept': [0.] * 7}
        replay = verify._make_predictor(model)
        selected = common.read(self.data / 'test_input.json')['records']
        predictions = [replay(record) for record in selected]
        self.assertEqual(len(predictions), len(selected))
        ys, ps, losses = [], [], []
        for record, (predicted, probabilities) in zip(selected, predictions):
            self.assertEqual((predicted, len(probabilities)), ('1', 7))
            for probability in probabilities:
                self.assertAlmostEqual(probability, 1 / 7, places=15)
            ys.append(record['y'])
            ps.append(predicted)
            losses.append(-math.log(probabilities[model['classes'].index(record['y'])]))
        self.assertAlmostEqual(statistics.fmean(losses), math.log(7))
        fraction_one = ys.count('1') / len(ys)
        self.assertAlmostEqual(verify.reference.macro_f1(ys, ps, model['classes']),
                               (2 * fraction_one / (1 + fraction_one)) / 7)


if __name__ == '__main__':
    unittest.main()
