"""CPU admission/integrity tests; no Isaac, models or device requests."""
from pathlib import Path
import copy,json,tempfile,unittest
from unittest.mock import patch
import numpy as np
from contracts.tasks import canonical_hash
from modules.planning import expert_binding as binding

ROOT=Path(__file__).resolve().parents[2]

class GeometryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.profile=json.loads((ROOT/'tasks/connector/profile.json').read_text())
        self.spec=copy.deepcopy(self.profile['defaults']);self.spec['profile_id']=binding.PROFILE_ID
    def test_registered_template(self):binding._supported(self.spec,self.profile)
    def test_only_poses_can_change(self):
        self.spec['objects'][0]['mass_kg']*=2
        with self.assertRaisesRegex(ValueError,'Only Connector'):binding._supported(self.spec,self.profile)
    def test_obstacles_and_camera_changes_rejected(self):
        self.spec['scene']['obstacles']=[{'geometry_type':'box'}]
        with self.assertRaisesRegex(ValueError,'changed scene'):binding._supported(self.spec,self.profile)
        self.spec=copy.deepcopy(self.profile['defaults']);self.spec['profile_id']=binding.PROFILE_ID
        self.spec['robot']['arms'][0]['camera']['housing_size_m'][0]+=.01
        with self.assertRaisesRegex(ValueError,'changed robot'):binding._supported(self.spec,self.profile)
    def test_pose_bounds_and_quaternion_sign(self):
        self.spec['objects'][0]['pose']['quaternion_wxyz']=[-v for v in self.spec['objects'][0]['pose']['quaternion_wxyz']]
        binding._supported(self.spec,self.profile)
        self.spec['objects'][0]['pose']['position_m'][0]+=.051
        with self.assertRaisesRegex(ValueError,'50 mm'):binding._supported(self.spec,self.profile)
    def test_frame_and_success_rule_changes_rejected(self):
        for field in ('frames','evaluation'):
            spec=copy.deepcopy(self.spec);spec[field]=[] if field=='frames' else {}
            with self.assertRaisesRegex(ValueError,'changed '+field):binding._supported(spec,self.profile)

class FrozenPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates=sorted((ROOT/'runs').glob('*/expert_plan/expert_plan.json'))
        cls.reference=None
        for path in reversed(candidates):
            try:
                plan=binding.load_plan(ROOT,path)
                cls.reference=plan;break
            except (ValueError,KeyError,OSError):pass
        if cls.reference is None:raise unittest.SkipTest('Requires a checked project scene/source expert plan; no GPU fixture is synthesized')
    def setUp(self):
        parent=ROOT/'.runtime/tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=parent,prefix='expert-plan-test-');self.folder=Path(self.temporary.name)
        plan=self.reference
        self.inp={k:plan[k] for k in ('spec_path','scene_manifest_path','demo_path','scene_validation_path')}
        self.inp.update(spec_hash=plan['task_spec_hash'],spec_revision=plan['spec_revision'])
    def tearDown(self):self.temporary.cleanup()
    def context(self,inp=None):return dict(root=ROOT,input=inp or self.inp,output_dir=self.folder/'plan',dependencies={})
    def save(self,name,value):
        path=self.folder/name;path.write_text(json.dumps(value,allow_nan=False));return str(path.relative_to(ROOT))
    def test_create_and_reload_preserves_scope(self):
        with patch.object(binding,'_verify_lineage',return_value=self.reference['trajectory_source']['lineage']):
            result=binding.create_plan(self.context())
        self.assertEqual(result['status'],'succeeded',result)
        plan=binding.load_plan(ROOT,result['plan_path'])
        self.assertEqual(plan['expert_perception'],'simulator_ground_truth')
        self.assertFalse(plan['learned_policy']);self.assertEqual(plan['hardware_commands_sent'],0)
        self.assertTrue(plan['evaluation_rules']['action_and_retention_separate'])
    def test_explicit_source_and_revision_required(self):
        for key in ('demo_path','spec_revision'):
            inp=copy.deepcopy(self.inp);inp.pop(key)
            self.assertEqual(binding.create_plan(self.context(inp))['status'],'needs_configuration')
    def test_wrong_revision_is_rejected(self):
        inp=copy.deepcopy(self.inp);inp['spec_revision']+=1
        self.assertEqual(binding.create_plan(self.context(inp))['status'],'needs_configuration')
    def test_missing_input_hash_cannot_be_resealed(self):
        plan=copy.deepcopy(self.reference);plan['input_hashes'].pop(plan['demo_path']);plan['content_hash']=binding._seal(plan)
        with self.assertRaisesRegex(ValueError,'closure'):binding.load_plan(ROOT,self.save('missing.json',plan))
    def test_gt_cannot_be_relabelled_rgbd(self):
        plan=copy.deepcopy(self.reference);plan['expert_perception']='rgbd';plan['content_hash']=binding._seal(plan)
        with self.assertRaisesRegex(ValueError,'scope'):binding.load_plan(ROOT,self.save('rgbd.json',plan))
    def test_changed_evaluation_cannot_be_resealed(self):
        plan=copy.deepcopy(self.reference);plan['evaluation_rules']['retention']['required']=False;plan['content_hash']=binding._seal(plan)
        with self.assertRaisesRegex(ValueError,'evaluation'):binding.load_plan(ROOT,self.save('evaluation.json',plan))
    def test_changed_scene_path_cannot_be_resealed(self):
        plan=copy.deepcopy(self.reference);plan['scene_path']=plan['demo_path'];plan['content_hash']=binding._seal(plan)
        with self.assertRaisesRegex(ValueError,'semantic paths'):binding.load_plan(ROOT,self.save('scene.json',plan))
    def test_retention_flag_cannot_be_relaxed(self):
        plan=copy.deepcopy(self.reference);plan['evaluation_rules']['retention_required']=False;plan['content_hash']=binding._seal(plan)
        with self.assertRaisesRegex(ValueError,'evaluation rule flags'):binding.load_plan(ROOT,self.save('retention-flag.json',plan))
    def test_scripted_source_cannot_be_relabelled_learned(self):
        plan=copy.deepcopy(self.reference);plan['learned_policy']=True;plan['content_hash']=binding._seal(plan)
        with self.assertRaisesRegex(ValueError,'identity/reporting'):binding.load_plan(ROOT,self.save('learned.json',plan))
    def test_plan_bytes_tampering(self):
        plan=copy.deepcopy(self.reference);plan['binding_scope']='hardware'
        with self.assertRaisesRegex(ValueError,'hash/version'):binding.load_plan(ROOT,self.save('tampered.json',plan))

if __name__=='__main__':unittest.main()
