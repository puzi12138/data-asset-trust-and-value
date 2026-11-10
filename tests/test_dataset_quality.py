import copy
import math
import unittest

from data_asset_poc.assessment.binding import NORMALIZATION_VERSION, asset_digest, json_digest
from data_asset_poc.assessment.decision import decide
from data_asset_poc.assessment.quality import audit_dataset


class QualitySemantics(unittest.TestCase):
    def setUp(self):
        self.schema = {'feature_names':['x0','x1'], 'feature_types':['numeric','numeric'],
                       'task_type':'regression','label_kind':'continuous','target_id':'target_a'}
        self.task = {'task_type':'regression','label_kind':'continuous','target_id':'target_a',
                     'required_features':['x0','x1'],'metric':'mae',
                     'model':{'family':'Ridge','requires_numeric':True,'handles_missing':False},
                     'preprocessing':{'missing':'median_train_only'},'constraints':{'min_rows':1}}
        self.asset = {'normalization_version':NORMALIZATION_VERSION,'dataset_id':'fixture',
            'dataset_version':'v1','source_id':'source-a','source_version':None,
            'license_sha256':json_digest({'name':'fixture licence'}),
            'schema_sha256':json_digest(self.schema),'task_sha256':json_digest(self.task),
            'records':[{'id':'a','source_row':1,'group':'g1','x':[1.,None],'y':3.},
                       {'id':'b','source_row':2,'group':'g2','x':[2.,4.],'y':4.}]}

    def audit(self, asset=None, schema=None, task=None, **kwargs):
        return audit_dataset(asset or self.asset, schema or self.schema, task or self.task, **kwargs)

    def test_computable_is_not_representative_or_fact_verified(self):
        d=self.audit()['dimensions']
        self.assertEqual(d['diversity']['measurement_status'],'MEASURED')
        self.assertEqual(d['diversity']['evidence_state'],'UNKNOWN')
        self.assertEqual(d['content']['evidence_state'],'UNKNOWN')
        self.assertEqual(d['content']['evidence']['unknown'],2)
        self.assertEqual(d['content']['evidence']['logical_truth_interval'],[0.,1.])
        self.assertEqual(d['novelty']['measurement_status'],'INSUFFICIENT_REFERENCE')
        self.assertEqual(d['novelty']['evidence_state'],'UNKNOWN')

    def test_missing_and_schema_conflict_are_distinct(self):
        d=self.audit()['dimensions']['structure']
        self.assertEqual(d['metrics']['missing_value_cells'],1)
        self.assertEqual(d['metrics']['schema_conformity_fraction'],1.)
        self.assertEqual(d['metrics']['present_cell_type_conformity_fraction'],1.)
        self.assertEqual(d['metrics']['type_evaluation_coverage_fraction'],.75)
        changed=copy.deepcopy(self.asset);changed['records'][0]['x']=[1.]
        d=self.audit(asset=changed)['dimensions']['structure']
        self.assertEqual(d['metrics']['absent_field_slots'],1)
        self.assertEqual(d['evidence_state'],'CONFLICT')
        self.assertEqual(d['metrics']['missing_value_cells'],0)

    def test_type_errors_and_nonfinite_are_separately_counted(self):
        changed=copy.deepcopy(self.asset);changed['records'][0]['x']=['bad',float('nan')]
        d=self.audit(asset=changed)['dimensions']
        self.assertEqual(d['structure']['metrics']['type_mismatch_cells'],1)
        self.assertEqual(d['structure']['metrics']['nonfinite_cells'],1)
        self.assertEqual(d['structure']['evidence_state'],'CONFLICT')
        self.assertEqual(d['integrity']['measurement_status'],'NOT_COMPUTABLE')
        self.assertIsNone(d['integrity']['metrics']['canonical_asset_sha256'])

    def test_known_task_conflict_is_not_confused_with_missing_reference(self):
        a=self.audit()['dimensions']['task']
        self.assertEqual(a['evidence_state'],'UNKNOWN')
        self.assertEqual(a['details']['target_distribution']['evidence_state'],'UNKNOWN')
        self.assertEqual(a['details']['evaluation_metric']['evidence_state'],'VERIFIED')
        changed=copy.deepcopy(self.task);changed['metric']='accuracy';changed['target_id']='other_target'
        d=self.audit(task=changed)['dimensions']['task']
        self.assertEqual(d['evidence_state'],'CONFLICT')
        self.assertEqual(d['details']['evaluation_metric']['evidence_state'],'CONFLICT')
        self.assertEqual(d['details']['target_semantics']['evidence_state'],'CONFLICT')

    def test_schema_and_task_digests_are_the_canonical_binding(self):
        digest=asset_digest(self.asset)
        d=self.audit(expected_asset_digest=digest)['dimensions']['integrity']
        self.assertEqual(d['metrics']['canonical_asset_sha256'],digest)
        self.assertEqual(d['details']['asset_snapshot_match']['evidence_state'],'VERIFIED')
        self.assertEqual(d['details']['schema_binding']['evidence_state'],'VERIFIED')
        altered=copy.deepcopy(self.task);altered['target_id']='different'
        d=self.audit(expected_asset_digest=digest,task=altered)['dimensions']['integrity']
        self.assertEqual(d['details']['task_binding']['evidence_state'],'CONFLICT')

    def test_group_or_source_row_change_invalidates_expected_digest(self):
        expected=asset_digest(self.asset)
        for field,new_value in [('group','another'),('source_row',55)]:
            changed=copy.deepcopy(self.asset);changed['records'][0][field]=new_value
            d=self.audit(asset=changed,expected_asset_digest=expected)['dimensions']['integrity']
            self.assertEqual(d['details']['asset_snapshot_match']['evidence_state'],'CONFLICT')

    def test_external_novelty_needs_distinct_dataset_and_does_not_certify(self):
        ref={'dataset_id':'fixture','reference_id':'same-source','rows':[{'x':[1.,None]}]}
        d=self.audit(external_reference=ref)['dimensions']['novelty']
        self.assertEqual(d['measurement_status'],'INSUFFICIENT_REFERENCE')
        ref['dataset_id']='different'
        d=self.audit(external_reference=ref)['dimensions']['novelty']
        self.assertEqual(d['measurement_status'],'MEASURED')
        self.assertEqual(d['metrics']['external_exact_overlap_fraction'],.5)
        self.assertEqual(d['evidence_state'],'UNKNOWN')

    def test_explicit_target_reference_evaluates_only_scoped_ks_claim(self):
        ref={'id':'fixture-target-sample','values':[1.,2.],'feature':'x0','ks_max':0.}
        d=self.audit(target_reference=ref)['dimensions']['task']
        self.assertEqual(d['details']['target_distribution']['ks_distance'],0.)
        self.assertEqual(d['details']['target_distribution']['evidence_state'],'VERIFIED')
        ref['values']=[10.,11.]
        d=self.audit(target_reference=ref)['dimensions']['task']
        self.assertEqual(d['details']['target_distribution']['evidence_state'],'CONFLICT')

    def test_no_sources_or_self_declared_boolean_are_certified(self):
        d=self.audit(source_evidence={'institution_identified':{'state':'VERIFIED'}})['dimensions']['source']
        self.assertEqual(d['details']['institution_identified']['evidence_state'],'UNKNOWN')
        self.assertEqual(d['evidence_state'],'UNKNOWN')

    def test_invalid_file_hash_is_not_certification(self):
        f={'comparison_performed':True,'expected_sha256':'x','actual_sha256':'x'}
        d=self.audit(file_integrity=f)['dimensions']['integrity']
        self.assertEqual(d['details']['file_snapshot_match']['evidence_state'],'UNKNOWN')
        f.update(expected_sha256='a'*64,actual_sha256='b'*64)
        d=self.audit(file_integrity=f)['dimensions']['integrity']
        self.assertEqual(d['details']['file_snapshot_match']['evidence_state'],'CONFLICT')

    def test_same_x_different_labels_is_observed_not_automatic_error(self):
        changed=copy.deepcopy(self.asset);changed['records'][1]['x']=changed['records'][0]['x'][:]
        d=self.audit(asset=changed)['dimensions']['content']
        self.assertEqual(d['metrics']['duplicate_feature_different_target_groups'],1)
        self.assertEqual(d['evidence_state'],'UNKNOWN')

    def test_label_and_model_requirements_participate(self):
        changed=copy.deepcopy(self.asset);changed['records'][0]['y']='not-numeric'
        d=self.audit(asset=changed)['dimensions']['task']
        self.assertEqual(d['details']['observed_label_type']['evidence_state'],'CONFLICT')
        task=copy.deepcopy(self.task);task['preprocessing']={'missing':'none'}
        d=self.audit(task=task)['dimensions']['task']
        self.assertEqual(d['details']['model_input']['evidence_state'],'CONFLICT')

    def test_real_quality_output_binds_decision_and_missing_evidence_fails_closed(self):
        audited=self.audit();digest=audited['dimensions']['integrity']['metrics']['canonical_asset_sha256']
        self.assertEqual(digest,asset_digest(self.asset))
        context={'task_sha256':self.asset['task_sha256'],'model_id':'Ridge','model_version':'fixture',
                 'model_artifact_sha256':json_digest({'fixture':'model'}),'budget_sha256':json_digest({'budget':1})}
        certificate={'asset_sha256':digest,'binding_valid':True,'risk_upper':0.,'known_conflict_lower_bound':0.}
        result=decide(self.asset,context,certificate,None,{})
        self.assertEqual(result['decision'],'NEEDS_VERIFICATION')
        self.assertEqual(result['asset_sha256'],digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
