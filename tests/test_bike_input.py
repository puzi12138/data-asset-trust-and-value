"""Exercise the Bike Sharing data adapter and its frozen runner boundary."""
import copy
import csv
from datetime import date, timedelta
import hashlib
import io
from pathlib import Path
import random
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from data_asset_poc.assessment.selection import group_cap
from scripts.external_experiments import common, prepare_bike as bike, regression_contract as contract


def fake_hours(day_count, hours_per_day=24):
    """Generate CSV fields with a target exactly recoverable from the excluded columns."""
    columns = 'instant dteday season yr mnth hr holiday weekday workingday weathersit temp atemp hum windspeed casual registered cnt'.split()
    with io.StringIO(newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(columns)
        source_row = 0
        for day in range(day_count):
            when = date(2011, 1, 1) + timedelta(days=day)
            for hour in range(hours_per_day):
                source_row += 1
                casual, registered = (hour + day % 9), (hour * 3 + day % 7)
                writer.writerow([source_row, when.isoformat(), 1, 0, when.month, hour,
                                 0, (when.weekday() + 1) % 7, int(when.weekday() < 5), 1,
                                 0.1 + day % 10 / 100, 0.2, 0.4, 0.6,
                                 casual, registered, casual + registered])
        return stream.getvalue().encode('ascii')


def source_record(raw):
    return {'dataset': 'Bike Sharing', 'source_url': bike.SOURCE_URL, 'download_url': bike.DOWNLOAD_URL,
            'attribution': 'Hadi Fanaee-T and João Gama (2013)', 'license': 'CC BY 4.0',
            'retrieved_at_utc': '2026-01-01T00:00:00Z',
            'raw_size_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest()}


class BikeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = fake_hours(80)
        cls.source = source_record(cls.raw)
        cls.rows = bike.parse_bike(cls.raw, expected_rows=80 * 24)
        cls.payloads = bike.build_payloads(cls.rows, cls.source, 1103)

    def test_30_day_boundaries_and_buffer(self):
        days = sorted({row['group'] for row in self.rows})[:30]
        self.assertEqual(bike.split_dates(reversed(days)),
                         {'D0': days[:4], 'buffer_after_D0': days[4:6],
                          'pool': days[6:15], 'buffer_after_pool': days[15:17],
                          'dev': days[17:20], 'buffer_after_dev': days[20:22], 'test': days[22:]})

    def test_date_roles_are_complete_and_seed_independent(self):
        first = self.payloads['MEMBERS.json']
        for seed in (2207, 3301):
            later = bike.build_payloads(self.rows, self.source, seed)['MEMBERS.json']
            self.assertEqual(later['roles'], first['roles'])
            self.assertEqual(later['date_boundaries'], first['date_boundaries'])
        roles = first['roles']
        self.assertEqual(sum(map(len, roles.values())), len(self.rows))
        groups = [set(row['group'] for row in role) for role in roles.values()]
        self.assertEqual(sum(map(len, groups)), len(set().union(*groups)))
        self.assertEqual(len(groups[-1]), 6)
        self.assertEqual(len({row['source_row'] for records in roles.values() for row in records}), len(self.rows))
        self.assertTrue(all(set(row) == {'source_row', 'id', 'group'} for rs in roles.values() for row in rs))

    def test_ordered_features_exclude_derived_counts(self):
        fields = contract.BIKE_FEATURES
        row = self.rows[25]
        raw_row = list(csv.DictReader(io.StringIO(self.raw.decode('ascii'))))[25]
        self.assertEqual(row['x'], [float(raw_row[key]) for key in fields])
        self.assertEqual(row['y'], int(raw_row['casual']) + int(raw_row['registered']))
        self.assertEqual(self.payloads['selection_input.json']['task']['feature_names'], fields)
        self.assertEqual(fields[8], 'temp')
        self.assertFalse({'casual', 'registered', 'cnt', 'instant', 'dteday'} & set(fields))
        self.assertEqual(row['record_identity'], f"{raw_row['dteday']}:{int(raw_row['hr']):02d}")
        self.assertEqual(row['group'], row['split_group'])
        self.assertEqual(self.rows[24]['group'], row['group'])
        self.assertNotEqual(self.rows[24]['id'], row['id'])

    def test_source_failure_and_strict_hour_schema(self):
        bike.validate_source(self.source, self.raw)
        for key, invalid, reason in (
            ('raw_sha256', '0' * 64, 'HASH'),
            ('raw_size_bytes', -1, 'SIZE'),
            ('license', 'Unknown', 'LICENSE'),
            ('source_url', 'https://example.com', 'URL'),
            ('retrieved_at_utc', '2026-01-01T00:00:00', 'RETRIEVAL_TIME'),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, reason):
                bike.validate_source({**self.source, key: invalid}, self.raw)
        with self.assertRaisesRegex(ValueError, 'ROW_COUNT'):
            bike.parse_bike(self.raw)
        for column, bad, reason in (
            (0, '1', 'instant'), (1, '2011-02-30', 'INVALID_RECORD'),
            (5, '0', 'DUPLICATE_HOUR'), (5, '24', 'INVALID_RECORD'),
            (10, 'nan', 'INVALID_RECORD'), (16, '-1', 'INVALID_RECORD'),
        ):
            lines = fake_hours(1, 2).decode('ascii').splitlines()
            cells = lines[2].split(',')
            cells[column] = bad
            lines[2] = ','.join(cells)
            with self.subTest(column=column, bad=bad), self.assertRaisesRegex(ValueError, reason):
                bike.parse_bike(('\n'.join(lines) + '\n').encode('ascii'), expected_rows=2)
        with self.assertRaisesRegex(ValueError, 'COLUMNS'):
            bike.parse_bike(self.raw.replace(b'windspeed,', b'', 1), expected_rows=len(self.rows))

    def test_short_date_range_or_pool_stops(self):
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_DAYS|EMPTY_DATE_ROLE'):
            bike.split_dates(r['group'] for r in self.rows[:7 * 24])
        raw = fake_hours(30)
        rows = bike.parse_bike(raw, expected_rows=30 * 24)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_POOL'):
            bike.build_payloads(rows, source_record(raw), 1103)

    def test_references_and_modifications_match_frozen_rng(self):
        inputs = self.payloads['selection_input.json']
        rows, roles = inputs['rows'], inputs['roles']
        originals = {row['id']: row for row in self.rows}
        d0, pool = roles['D0'], roles['pool']
        references = self.payloads['audit_reference.json']['records']
        truth = self.payloads['injection_truth.json']['records']
        self.assertEqual([r['index'] for r in references], random.Random(161103).sample(pool, 256))
        self.assertEqual([r['index'] for r in truth], random.Random(171103).sample(pool, len(pool) // 5))
        for r in references:
            original = originals[r['id']]
            self.assertEqual((r['x'], r['y']), (original['x'], original['y']))
        label_delta = 2 * statistics.pstdev(rows[i]['y'] for i in d0)
        temperatures = [rows[i]['x'][8] for i in d0]
        temp_new = max(temperatures) + 3 * statistics.pstdev(temperatures)
        for j, r in enumerate(truth):
            row = rows[r['index']]
            old = originals[row['id']]
            if j < len(truth) // 2:
                self.assertEqual((r['kind'], r['original'], row['x']), ('label', old['y'], old['x']))
                self.assertAlmostEqual(row['y'], old['y'] + label_delta)
            else:
                self.assertEqual((r['kind'], r['original'], row['y']), ('feature', old['x'][8], old['y']))
                self.assertAlmostEqual(row['x'][8], temp_new)
                self.assertEqual(row['x'][:8] + row['x'][9:], old['x'][:8] + old['x'][9:])
        modified = {r['index'] for r in truth}
        self.assertTrue(set(d0).isdisjoint(modified))
        self.assertTrue(all(rows[i] == originals[rows[i]['id']] for i in range(len(rows)) if i not in modified))
        self.assertEqual(bike.build_payloads(self.rows, self.source, 1103), self.payloads)
        self.assertNotEqual(bike.build_payloads(self.rows, self.source, 2207)['audit_reference.json'],
                            self.payloads['audit_reference.json'])

    def test_missing_pool_temperature_and_constant_d0(self):
        records = copy.deepcopy(self.rows)
        selection = self.payloads['selection_input.json']
        d0 = selection['roles']['D0']
        truth = self.payloads['injection_truth.json']['records']
        feature_index = next(v['index'] for v in truth if v['kind'] == 'feature')
        source_index = selection['rows'][feature_index]['source_row'] - 1
        records[source_index]['x'][8] = None
        for i in d0:
            row = records[selection['rows'][i]['source_row'] - 1]
            row['x'][8] = 0.2
            row['y'] = 10
        payloads = bike.build_payloads(records, self.source, 1103)
        self.assertEqual(payloads['preparation.json']['label_mutation_delta'], 2)
        self.assertAlmostEqual(payloads['selection_input.json']['rows'][feature_index]['x'][8], 3.2)
        self.assertIsNone(next(v['original'] for v in payloads['injection_truth.json']['records']
                               if v['index'] == feature_index))

    def test_output_freeze_and_runner_interoperate(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            raw, source, output = parent / 'hour.csv', parent / 'source.json', parent / 'prepared'
            raw.write_bytes(self.raw)
            common.write(source, self.source)
            with patch.object(bike, 'parse_bike', return_value=self.rows):
                self.assertEqual(bike.prepare(raw, source, 1103, output)['training'], 'NOT_RUN')
            cfg = common.read(output / 'config.json')
            self.assertTrue(common.validate_config(cfg, output))
            self.assertEqual(len(common.planned_arms(cfg)), 18)
            self.assertEqual({p['path'] for p in cfg['inputs']}, set(self.payloads))
            data = common.read(output / cfg['selection_input'])
            refs = common.read(output / cfg['audit_reference'])['records']
            members = common.read(output / 'MEMBERS.json')
            dev = common.read(output / cfg['development_input'])['records']
            test = common.read(output / cfg['test_input'])['records']
            self.assertEqual(set(data), {'rows', 'roles', 'task', 'asset_metadata'})
            self.assertEqual(set(data['roles']), {'D0', 'pool'})
            selection_ids = {r['id'] for r in data['rows']}
            for role, split in (('dev', dev), ('test', test), ('buffer', members['roles']['buffer'])):
                self.assertTrue(selection_ids.isdisjoint(r['id'] for r in split))
                self.assertEqual(members['roles'][role],
                                 [{k: r[k] for k in ('source_row', 'id', 'group')} for r in split])
            contract.validate_source_binding(data, members, self.source, cfg)
            rows, d0, pool, task, evidence, audit = contract.validate_selection(data, refs, cfg)
            self.assertEqual(len(evidence), 256)
            self.assertTrue(set(evidence.values()) == {'VERIFIED', 'CONFLICT'})
            self.assertTrue(any(rows[i]['id'] not in evidence for i in pool))
            contract.validate_roles(data, dev, test, cfg, members)
            original_by_id = {r['id']: r for r in self.rows}
            self.assertTrue(all(r == original_by_id[r['id']] for r in dev + test))
            sha_before = common.sha(output / 'config.json')
            with self.assertRaisesRegex(FileExistsError, 'OUTPUT_ALREADY_EXISTS'):
                bike.prepare(raw, source, 3301, output)
            self.assertEqual(common.sha(output / 'config.json'), sha_before)
            with (output / 'injection_truth.json').open('ab') as handle:
                handle.write(b' ')
            with self.assertRaisesRegex(ValueError, 'INPUT_HASH_MISMATCH:injection_truth.json'):
                common.validate_config(cfg, output)

    def test_daily_round_robin_and_cross_role_rejection(self):
        members = copy.deepcopy(self.payloads['MEMBERS.json'])
        data = self.payloads['selection_input.json']
        dev = copy.deepcopy(self.payloads['development_input.json']['records'])
        test = self.payloads['test_input.json']['records']
        day = data['rows'][0]['group']
        x = dev[0]['x'].copy()
        x[3] = 19
        dev[0].update(group=day, split_group=day, id='other-id',
                      record_identity=f'{day}:19', x=x)
        members['roles']['dev'][0].update(group=day, id='other-id')
        with self.assertRaisesRegex(ValueError, 'ROLE_GROUP_OR_ID_LEAKAGE_D0_dev'):
            contract.validate_roles(data, dev, test, {'task_id': 'EXT-04'}, members)
        sample = [{'id': f'{day}:{hour:02d}', 'group': day}
                  for day, hour in [('A', 8), ('A', 9), ('B', 8)]]
        chosen = [r['id'] for r in group_cap(sample, 3)]
        self.assertEqual(chosen, ['A:08', 'B:08', 'A:09'])
        self.assertEqual(len(set(chosen)), 3)


if __name__ == '__main__':
    unittest.main()
