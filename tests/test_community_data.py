"""Small deterministic checks for input isolation and source pinning."""
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('community_prepare', REPO/'scripts/experiments/prepare_data.py')
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)

class CommunityDataTests(unittest.TestCase):
    def fixture(self, duplicate=False):
        rows=[]
        for i in range(40):
            v=0 if duplicate and i==39 else i
            rows.append(','.join([str(i%2)]+[str(v*13+j) for j in range(13)]))
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('wine.data','\n'.join(rows)+'\n')
        blob=stream.getvalue()
        source={'dataset':'wine_classification','member':'wine.data','rows':40,'features':13,
                'target':'wine cultivar','dataset_url':'https://archive.ics.uci.edu/dataset/109/wine',
                'archive_sha256':hashlib.sha256(blob).hexdigest(),'download_limit_bytes':1000000}
        roles={'D0':list(range(0,10)), 'pool':list(range(10,30)), 'dev':list(range(30,35)), 'test':list(range(35,40))}
        config={'task_type':'classification','seed':1103,'k':5,'allowed_labels':['0','1'],
                'primary_metric':'LogLoss','target_model':'LogisticRegression','roles':roles}
        return blob,source,config

    def test_source_pin_fails_closed(self):
        blob,source,_=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'wine_classification.zip';path.write_bytes(blob+b'tamper')
            with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
                prep.get_archive(source,tmp)

    def test_deterministic_members_and_controlled_changes(self):
        blob,source,cfg=self.fixture()
        a=prep.make_payloads(cfg,source,blob);b=prep.make_payloads(cfg,source,blob)
        self.assertEqual(a,b)
        self.assertEqual(len(a['injection_truth']['records']),4)
        self.assertEqual(len(a['audit_reference']['records']),20)
        self.assertTrue(all(a['selection_input']['rows'][i] is None for i in cfg['roles']['dev']+cfg['roles']['test']))
        self.assertEqual({x['kind'] for x in a['injection_truth']['records']},{'feature','label'})

    def test_duplicate_group_across_splits_is_rejected(self):
        blob,source,cfg=self.fixture(duplicate=True)
        with self.assertRaisesRegex(ValueError,'group leakage'):
            prep.make_payloads(cfg,source,blob)

    def test_reused_member_across_roles_is_rejected(self):
        blob,source,cfg=self.fixture();cfg['roles']['test'][-1]=cfg['roles']['D0'][0]
        with self.assertRaisesRegex(ValueError,'member coverage'):
            prep.make_payloads(cfg,source,blob)

    def test_all_tasks_have_three_unique_pinned_configs(self):
        files=list((REPO/'experiments/community/configs').glob('EXP-*/*.json'))
        self.assertEqual(len(files),18)
        for file in files:
            c=json.loads(file.read_text(encoding='utf8'))
            self.assertEqual(file.parent.name,c['task_id'])
            self.assertEqual(file.stem,str(c['seed']))
            self.assertEqual(c['primary_metric'],'LogLoss' if c['task_type']=='classification' else 'MAE')
            self.assertEqual(len(c['inputs']),6)
            self.assertTrue(all(len(i['sha256'])==64 for i in c['inputs']))
            self.assertEqual(len({i['path'] for i in c['inputs']}),6)
            self.assertEqual(len(c['expected_methods']),10 if c['task_type']=='classification' else 11)

    def test_unknown_task_and_seed_rejected(self):
        for task,seed in [('EXP-00',1103),('EXP-01',1),('../x',1103)]:
            with self.assertRaises(ValueError):prep.load_config(task,seed)

if __name__=='__main__':
    unittest.main()
