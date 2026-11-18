import csv
from hashlib import sha256
import io
from pathlib import Path
import random
import statistics
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.external_experiments import prepare_superconductivity as adapter
from scripts.external_experiments.common import read, validate_config, write


FIXTURE_MINIMUMS = {'D0': 12, 'pool': 20, 'dev': 6, 'test': 5}


def write_csv(header, values):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(values)
    return stream.getvalue().encode('utf8')


def rows(count=60):
    train, materials = [], []
    for n in range(1, count + 1):
        train.append(['1'] + [str(n)] * 80 + [str(2 * n)])
        materials.append(['1'] + ['0'] * 85 + [str(2 * n), f'H{n}'])
    return train, materials


def encode(train, materials):
    return (write_csv((*adapter.FEATURES, 'critical_temp'), train),
            write_csv((*adapter.ELEMENTS, 'critical_temp', 'material'), materials))


def metadata(train, materials):
    return {
        'dataset': adapter.DATASET, 'source_url': adapter.SOURCE_URL, 'download_url': adapter.DOWNLOAD_URL,
        'license': 'CC BY 4.0', 'attribution': 'Kam Hamidieh', 'retrieved_at_utc': '2026-11-18T00:00:00Z',
        'raw_size_bytes': len(train), 'raw_sha256': sha256(train).hexdigest(),
        'materials_size_bytes': len(materials), 'materials_sha256': sha256(materials).hexdigest(),
        'alignment_status': 'SOURCE_SUPPORTED',
    }


class SuperconductivityInputTests(unittest.TestCase):
    def setUp(self):
        self.train, self.materials = encode(*rows())
        self.source = metadata(self.train, self.materials)
        self.records = adapter.parse_superconductivity(self.train, self.materials, expected_rows=60)

    def test_each_source_digest_and_size_bound(self):
        adapter.validate_source(self.source, self.train, self.materials)
        for file in ('raw', 'materials'):
            for field, wrong in (('size_bytes', 1), ('sha256', '0' * 64)):
                with self.subTest(file=file, field=field):
                    source = {**self.source, f'{file}_{field}': wrong}
                    problem = 'HASH' if field == 'sha256' else 'SIZE'
                    with self.assertRaisesRegex(ValueError, f'SOURCE_{problem}_MISMATCH:{file}'):
                        adapter.validate_source(source, self.train, self.materials)

    def test_unknown_alignment_or_license_refused(self):
        for key, value, error in (('alignment_status', 'ALIGNMENT_UNRESOLVED', 'ALIGNMENT_UNRESOLVED'),
                                  ('license', 'unknown', 'SOURCE_LICENSE_MISMATCH'),
                                  ('download_url', 'https://example.org/file.zip', 'SOURCE_URL_MISMATCH')):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, error):
                adapter.validate_source({**self.source, key: value}, self.train, self.materials)

    def test_strict_headers_reject_missing_target_and_wrong_formula(self):
        train, materials = rows(2)
        bad_train = write_csv((*adapter.FEATURES, 'temperature'), train)
        bad_materials = write_csv((*adapter.ELEMENTS, 'critical_temp', 'formula'), materials)
        with self.assertRaisesRegex(ValueError, 'TRAIN_COLUMNS_MISMATCH'):
            adapter.parse_superconductivity(bad_train, self.materials)
        with self.assertRaisesRegex(ValueError, 'MATERIALS_COLUMNS_MISMATCH'):
            adapter.parse_superconductivity(self.train, bad_materials)

    def test_targets_and_element_counts_checked_row_by_row(self):
        for column, value, message in ((-2, '2.00000000000000001', 'critical temperatures differ'),
                                        (1, '1', 'element counts differ'),
                                        (-1, ' ', 'missing chemical formula')):
            with self.subTest(column=column):
                train, materials = rows(2)
                materials[0][column] = value
                with self.assertRaisesRegex(ValueError, f'INVALID_SOURCE_ROW:1: {message}'):
                    adapter.parse_superconductivity(*encode(train, materials), expected_rows=2)

    def test_nonfinite_and_invalid_width_rejected(self):
        for table, index, value in ((0, 2, 'NaN'), (0, -1, '-Infinity'), (1, 1, 'inf')):
            with self.subTest(table=table, index=index, value=value):
                both = rows(1)
                both[table][0][index] = value
                with self.assertRaisesRegex(ValueError, 'INVALID_SOURCE_ROW:1'):
                    adapter.parse_superconductivity(*encode(*both), expected_rows=1)
        train, materials = rows(1)
        train[0].pop()
        with self.assertRaisesRegex(ValueError, 'COLUMN_COUNT_MISMATCH:1'):
            adapter.parse_superconductivity(*encode(train, materials), expected_rows=1)

    def test_count_and_insufficient_grouped_rows_rejected(self):
        train, materials = rows(1)
        with self.assertRaisesRegex(ValueError, 'UNPAIRED_SOURCE_ROW:1'):
            adapter.parse_superconductivity(*encode(train, []))
        with self.assertRaisesRegex(ValueError, 'RAW_ROW_COUNT_MISMATCH: expected 21263'):
            adapter.parse_superconductivity(*encode(train, materials))
        with self.assertRaisesRegex(ValueError, 'NOT_RUN_INSUFFICIENT_GROUPED_ROWS:test'):
            adapter.split_records(self.records, self.source['raw_sha256'], 1103,
                                  {**FIXTURE_MINIMUMS, 'test': 60})

    def test_groups_follow_transitive_connections_and_keep_original_precision(self):
        train, materials = rows()
        materials[3][-1] = materials[0][-1]
        train[9][:-1] = train[3][:-1]
        train[15][:-1] = train[3][:-1]
        train[15][1] = '4.00000000000000001'
        raw_train, raw_materials = encode(train, materials)
        source = metadata(raw_train, raw_materials)
        records = adapter.parse_superconductivity(raw_train, raw_materials, expected_rows=60)
        splits = adapter.split_records(records, source['raw_sha256'], 1103, FIXTURE_MINIMUMS)
        all_members = {r['source_row']: (role, r['group']) for role, entries in splits.items() for r in entries}
        self.assertEqual(all_members[1], all_members[4])
        self.assertEqual(all_members[4], all_members[10])
        self.assertEqual(all_members[10][1], '1,4,10')
        self.assertNotEqual(all_members[10][1], all_members[16][1])
        self.assertEqual(set(all_members), set(range(1, 61)))
        self.assertEqual(sum(map(len, splits.values())), 60)
        for role, minimum in FIXTURE_MINIMUMS.items():
            self.assertGreaterEqual(len(splits[role]), minimum)
        for role, records in splits.items():
            for group in {r['group'] for r in records}:
                self.assertTrue(all(all_members[int(n)][0] == role for n in group.split(',')))

    def test_references_and_controlled_modifications_reproduce(self):
        raw_train, raw_materials = encode(*rows(400))
        source = metadata(raw_train, raw_materials)
        records = adapter.parse_superconductivity(raw_train, raw_materials, expected_rows=400)
        minimums = {**FIXTURE_MINIMUMS, 'pool': 280}
        result = adapter.build_payloads(records, source, 1103, minimums)
        self.assertEqual(adapter.build_payloads(list(reversed(records)), source, 1103, minimums), result)
        selection = result['selection_input.json']
        pool, d0 = (selection['roles'][key] for key in ('pool', 'D0'))
        references = result['audit_reference.json']['records']
        self.assertEqual([x['index'] for x in references], random.Random(1103 + 160000).sample(pool, 256))
        by_number = {r['source_row']: r for r in records}
        for reference in references:
            observed = selection['rows'][reference['index']]
            raw = by_number[observed['source_row']]
            self.assertEqual((reference['x'], reference['y']), (list(map(float, raw['x'])), raw['y']))
        truth = result['injection_truth.json']['records']
        self.assertEqual([x['index'] for x in truth], random.Random(1103 + 170000).sample(pool, 56))
        self.assertEqual([x['kind'] for x in truth], ['label'] * 28 + ['feature'] * 28)
        label_delta = 2 * (statistics.pstdev(selection['rows'][i]['y'] for i in d0) or 1)
        for choice in truth:
            record = selection['rows'][choice['index']]
            raw = by_number[record['source_row']]
            if choice['kind'] == 'label':
                self.assertEqual(choice['original'], raw['y'])
                self.assertEqual(record['y'], raw['y'] + label_delta)
                self.assertEqual(record['x'][0], 1.0)
            else:
                self.assertEqual(choice['original'], 1.0)
                self.assertEqual(record['x'][0], 4.0)
                self.assertEqual(record['y'], raw['y'])
        touched = {choice['index'] for choice in truth}
        self.assertTrue(touched.isdisjoint(d0))
        for i, row in enumerate(selection['rows']):
            if i not in touched:
                raw = by_number[row['source_row']]
                self.assertEqual(row['y'], raw['y'])
                self.assertEqual(row['x'], list(map(float, raw['x'])))
        self.assertEqual(set(selection), {'roles', 'rows', 'task', 'asset_metadata'})
        self.assertEqual(selection['task']['feature_names'], list(adapter.FEATURES))
        held_out = result['development_input.json']['records'] + result['test_input.json']['records']
        self.assertTrue({r['id'] for r in selection['rows']}.isdisjoint(r['id'] for r in held_out))
        for row in held_out:
            raw = by_number[row['source_row']]
            self.assertEqual(set(row), {'source_row', 'id', 'group', 'x', 'y'})
            self.assertEqual(row['y'], raw['y'])
            self.assertEqual(row['x'], list(map(float, raw['x'])))

    def test_prepare_preserves_existing_output_and_binds_each_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            train = root / 'train.csv'
            train.write_bytes(self.train)
            materials = root / 'unique_m.csv'
            materials.write_bytes(self.materials)
            source = root / 'source.json'
            write(source, self.source)
            out = root / 'prepared'
            payloads = adapter.build_payloads(self.records, self.source, 1103, FIXTURE_MINIMUMS, 4)
            with (patch.object(adapter, 'parse_superconductivity', return_value=self.records),
                  patch.object(adapter, 'build_payloads', return_value=payloads)):
                receipt = adapter.prepare(train, materials, source, 1103, out)
            config = read(out / 'config.json')
            self.assertEqual(receipt['training'], 'NOT_RUN')
            self.assertEqual(config['task_id'], 'EXT-03')
            self.assertEqual(config['feature_count'], 81)
            self.assertEqual(config['mutation_feature_index'], 0)
            self.assertEqual(config['models'], ['ridge', 'random_forest'])
            self.assertEqual(config['budgets'], [128, 512])
            self.assertEqual(config['training'], 'NOT_RUN')
            self.assertEqual(len(config['inputs']), 8)
            self.assertTrue(validate_config(config, out))
            self.assertEqual(receipt['config_sha256'], sha256((out / 'config.json').read_bytes()).hexdigest())
            orig = {p.name: p.read_bytes() for p in out.iterdir()}
            with self.assertRaisesRegex(FileExistsError, 'OUTPUT_ALREADY_EXISTS'):
                adapter.prepare(train, materials, source, 1103, out)
            self.assertEqual(orig, {p.name: p.read_bytes() for p in out.iterdir()})


if __name__ == '__main__':
    unittest.main()
