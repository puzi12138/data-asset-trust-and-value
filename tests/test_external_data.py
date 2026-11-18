"""Tests of the external Letter preparation path, using synthetic rows only."""
import csv
import hashlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from scripts.external_experiments import prepare_letter as p


def synthetic_raw():
    """20k rows, 26 labels; every pair has identical 16-feature vectors."""
    result = io.StringIO(newline='')
    writer = csv.writer(result, lineterminator='\n')
    for i in range(20000):
        group = i // 2
        x = [(group // (16 ** j)) % 16 for j in range(4)]
        x += [(group * 3 + j) % 16 for j in range(12)]
        writer.writerow([p.LABELS[i % 26]] + x)
    return result.getvalue().encode('ascii')


class TestLetterPreparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='letter-unit-')
        base = Path(cls.temp.name)
        cls.raw = synthetic_raw()
        cls.raw_path = base / 'synthetic.csv'
        cls.raw_path.write_bytes(cls.raw)
        cls.source = {'dataset': 'Letter Recognition', 'raw_sha256': p.sha_bytes(cls.raw),
                      'raw_size_bytes': len(cls.raw), 'license': 'CC BY 4.0',
                      'source_url': 'https://archive.ics.uci.edu/dataset/59/letter+recognition',
                      'attribution': 'synthetic fixture, not the UCI raw data'}
        cls.source_path = base / 'source.json'
        cls.source_path.write_text(json.dumps(cls.source), encoding='utf8')
        cls.out = base / 'prepared'
        cls.receipt = p.prepare(cls.raw_path, cls.source_path, 1103, cls.out)
        cls.config = json.loads((cls.out / 'config.json').read_text('utf8'))
        cls.payload = {name: json.loads((cls.out / filename).read_text('utf8'))
                       for name, filename in p.FILES.items()}
        cls.original = p.parse_letter(cls.raw)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_dimensions_and_official_label_set(self):
        self.assertEqual(len(self.original), 20000)
        self.assertEqual({r['y'] for r in self.original}, set(p.LABELS))
        self.assertEqual({len(r['x']) for r in self.original}, {16})

    def test_source_metadata_is_bound_to_original_bytes(self):
        self.assertEqual(p.validate_source(self.source, self.raw), hashlib.sha256(self.raw).hexdigest())
        for key, value in [('raw_sha256', '0' * 64), ('raw_size_bytes', 42),
                           ('dataset', 'OTHER'), ('license', 'UNKNOWN'),
                           ('source_url', 'https://example.org/data')]:
            broken = {**self.source, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.validate_source(broken, self.raw)

    def test_parser_rejects_bad_schema_labels_values_and_row_count(self):
        for raw in [b'A,1\n', b'!,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n',
                    b'A,16,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n',
                    self.raw[:-1].rsplit(b'\n', 1)[0]]:
            with self.subTest(prefix=raw[:20]), self.assertRaises(ValueError):
                p.parse_letter(raw)

    def test_role_sizes_and_separate_exact_feature_groups(self):
        members = self.payload['members']['roles']
        for name, minimum in p.MINIMUMS.items():
            self.assertGreaterEqual(len(members[name]), minimum)
        self.assertLessEqual(len(members['D0']), 768)
        group_sets = [{r['group'] for r in members[name]} for name in members]
        for i, left in enumerate(group_sets):
            for right in group_sets[i + 1:]:
                self.assertFalse(left & right)
        assigned = [row['source_row'] for role in members.values() for row in role]
        self.assertEqual(len(assigned), 20000)
        self.assertEqual(set(assigned), set(range(1, 20001)))
        # Pairs are identical features, even if their labels differ.
        for role in members.values():
            for row in role:
                sibling = row['source_row'] + (1 if row['source_row'] % 2 else -1)
                self.assertIn(str(sibling), row['group'].split(','))

    def test_d0_contains_every_class(self):
        data = self.payload['selection_input']
        self.assertEqual({data['rows'][i]['y'] for i in data['roles']['D0']}, set(p.LABELS))

    def test_no_evaluation_rows_in_selection_input(self):
        selection = self.payload['selection_input']
        selected_source_rows = {r['source_row'] for r in selection['rows']}
        self.assertEqual(len(selected_source_rows), len(selection['rows']))
        for name in ('development_input', 'test_input'):
            self.assertFalse(selected_source_rows & {r['source_row'] for r in self.payload[name]['records']})
        indexes = selection['roles']['D0'] + selection['roles']['pool']
        self.assertEqual(set(indexes), set(range(len(selection['rows']))))
        self.assertEqual([r['source_row'] for r in selection['rows']], sorted(selected_source_rows))

    def test_references_are_fixed_256_originals(self):
        data = self.payload['selection_input']
        refs = self.payload['audit_reference']['records']
        self.assertEqual(len(refs), 256)
        expected = random.Random(1103 + 160000).sample(data['roles']['pool'], 256)
        self.assertEqual([r['index'] for r in refs], expected)
        for ref in refs:
            row = data['rows'][ref['index']]
            original = self.original[row['source_row'] - 1]
            self.assertEqual((ref['id'], ref['x'], ref['y']), (original['id'], original['x'], original['y']))

    def test_modifications_follow_published_rng_and_counts(self):
        data = self.payload['selection_input']
        pool = data['roles']['pool']
        truth = self.payload['injection_truth']['records']
        n = len(pool) // 5
        expected = random.Random(1103 + 170000).sample(pool, n)
        self.assertEqual([r['index'] for r in truth], expected)
        self.assertEqual([r['kind'] for r in truth], ['label'] * (n // 2) + ['feature'] * (n - n // 2))
        for record in truth:
            row = data['rows'][record['index']]
            original = self.original[row['source_row'] - 1]
            self.assertEqual((record['original_x'], record['original_y']), (original['x'], original['y']))
            if record['kind'] == 'label':
                self.assertEqual(row['x'], original['x'])
                self.assertEqual(row['y'], p.LABELS[(p.LABELS.index(original['y']) + 1) % 26])
            else:
                self.assertEqual(row['y'], original['y'])
                self.assertEqual(row['x'][1:], original['x'][1:])
                self.assertGreater(row['x'][0], 15)

    def test_evaluation_and_d0_values_are_unmodified(self):
        data = self.payload['selection_input']
        records = [data['rows'][i] for i in data['roles']['D0']]
        records += self.payload['development_input']['records']
        records += self.payload['test_input']['records']
        for row in records:
            original = self.original[row['source_row'] - 1]
            self.assertEqual((row['x'], row['y']), (original['x'], original['y']))

    def test_frozen_config_hashes_all_prepared_files(self):
        inputs = self.config['inputs']
        self.assertEqual({v['path'] for v in inputs}, set(p.FILES.values()))
        for entry in inputs:
            self.assertEqual(entry['sha256'], p.sha_bytes((self.out / entry['path']).read_bytes()))
        self.assertEqual(self.config['budgets'], [128, 512])
        self.assertEqual(self.config['models'], ['logistic', 'random_forest'])
        self.assertEqual(self.config['mutation_feature_index'], 0)
        self.assertEqual(self.receipt['training'], 'NOT_RUN')

    def test_split_reproducibility_and_role_excess(self):
        repeated = p.group_and_split(self.original, self.source['raw_sha256'], 1103)
        members = self.payload['members']['roles']
        for name in members:
            self.assertEqual([r['source_row'] for r in repeated[name]], [r['source_row'] for r in members[name]])
        prep = self.payload['preparation']
        for name in p.MINIMUMS:
            self.assertEqual(prep['whole_group_excess'][name], len(members[name]) - p.MINIMUMS[name])

    def test_input_order_does_not_change_group_assignment(self):
        shuffled = list(self.original)
        random.Random(1).shuffle(shuffled)
        roles = p.group_and_split(shuffled, self.source['raw_sha256'], 1103)
        for role in roles:
            self.assertEqual([r['source_row'] for r in roles[role]],
                             [r['source_row'] for r in self.payload['members']['roles'][role]])

    def test_hash_group_order_is_exact(self):
        groups = {}
        for row in self.original:
            groups.setdefault(tuple(row['x']), []).append(row)
        ordered = []
        for group in groups.values():
            group = sorted(group, key=lambda r: r['source_row'])
            key = ','.join(str(r['source_row']) for r in group)
            h = hashlib.sha256(f'{self.source["raw_sha256"]}:1103:{key}'.encode()).hexdigest()
            ordered.append((h, key, group))
        ordered.sort(key=lambda item: (item[0], item[1]))
        chosen, remaining, uncovered, n = [], [], set(p.LABELS), 0
        for _, key, group in ordered:
            if uncovered & {r['y'] for r in group}:
                chosen.append((key, group))
                uncovered -= {r['y'] for r in group}
                n += len(group)
        picked = {key for key, _ in chosen}
        for _, key, group in ordered:
            if n >= p.MINIMUMS['D0']:
                break
            if key not in picked:
                chosen.append((key, group)); picked.add(key); n += len(group)
        expected_d0 = {r['source_row'] for _, group in chosen for r in group}
        self.assertEqual(expected_d0, {r['source_row'] for r in self.payload['members']['roles']['D0']})
        remaining = [(key, group) for _, key, group in ordered if key not in picked]
        cursor = 0
        for name in ('pool', 'dev', 'test'):
            selected, n = set(), 0
            while n < p.MINIMUMS[name]:
                _, group = remaining[cursor]; cursor += 1
                selected.update(r['source_row'] for r in group); n += len(group)
            self.assertEqual(selected, {r['source_row'] for r in self.payload['members']['roles'][name]})

    def test_output_never_overwrites_existing_directory(self):
        before = (self.out / 'config.json').read_bytes()
        with self.assertRaises(FileExistsError):
            p.prepare(self.raw_path, self.source_path, 1103, self.out)
        self.assertEqual(before, (self.out / 'config.json').read_bytes())

    def test_invalid_seed_and_unavailable_grouped_rows_stop(self):
        with self.assertRaises(ValueError):
            p.group_and_split(self.original, self.source['raw_sha256'], 42)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_GROUPED_ROWS'):
            p.group_and_split(self.original, self.source['raw_sha256'], 1103,
                              {'D0': 512, 'pool': 19999, 'dev': 1, 'test': 1})
        with self.assertRaisesRegex(ValueError, 'D0_SIZE'):
            p.group_and_split(self.original, self.source['raw_sha256'], 1103,
                              {'D0': 800, 'pool': 1, 'dev': 1, 'test': 1})


if __name__ == '__main__':
    unittest.main()
