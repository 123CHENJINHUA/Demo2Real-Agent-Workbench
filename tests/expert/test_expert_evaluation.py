import copy,json
from pathlib import Path
import numpy as np
import pytest
from modules.expert_validation import evaluate_trace

@pytest.fixture
def fixture(tmp_path):
    xml='<robot>'+''.join('<joint name="joint%d" type="revolute"><limit lower="-3" upper="3"/></joint>'%i for i in range(1,8))+'</robot>'
    (tmp_path/'robot.urdf').write_text(xml);spec={'content_hash':'a'*64,'revision':1,'robot':{'arms':[{'urdf_path':'robot.urdf'},{'urdf_path':'robot.urdf'}]},'evaluation':{'action':{'depth_window_m':[.0222,.0236]}}};(tmp_path/'spec.json').write_text(json.dumps(spec));(tmp_path/'manifest.json').write_text('{"scene_path":"build/scene.usda"}')
    plan={'spec_path':'spec.json','scene_manifest_path':'manifest.json'};result={'expert_perception':'simulator_ground_truth','hardware_commands_sent':0,'hardware_verified':False,'loaded_scene_path':'build/scene.usda','task_spec_hash':'a'*64,'spec_revision':1,'action_passed':True,'full_task_passed':True}
    n=4;trace={'time':np.arange(n)/60,'phase':np.array(['dex_grasp']*n),'objects':np.tile(np.eye(4),(n,2,1,1)),'joints':np.zeros((n,2,7)),'jaws':np.zeros((n,2,2)),'commanded_joints':np.zeros((n,2,7)),'contact_forces_N':np.zeros((n,4,2,3)),'latch_deflections_m':np.zeros((n,2))}
    return tmp_path,plan,result,trace

def test_self_reported_success_cannot_pass_unexecuted_action(fixture):
    report=evaluate_trace(*fixture);assert report['status']=='failed';assert report['action_passed'] is False;assert report['full_task_passed'] is False
    assert report['stage_results']['grasp']['passed'] is False;assert report['stage_results']['insertion']['passed'] is None

def test_trace_shapes_and_clock_are_checked(fixture):
    root,plan,result,trace=fixture;trace['time'][2]=trace['time'][1]
    report=evaluate_trace(root,plan,result,trace);assert any(c['id']=='monotonic_simulation_clock' and not c['passed'] for c in report['checks'])
    trace.pop('commanded_joints');report=evaluate_trace(root,plan,result,trace);assert not report['full_task_passed'];assert not report['checks'][0]['passed']

@pytest.mark.parametrize('field,value,check',[('hardware_commands_sent',1,'no_hardware_commands'),('expert_perception','rgbd','ground_truth_teacher_scope'),('spec_revision',2,'binding_spec_revision'),('loaded_scene_path','different.usda','loaded_exact_scene')])
def test_wrong_scope_or_scene_cannot_inherit_success(fixture,field,value,check):
    root,plan,result,trace=fixture;result[field]=value;report=evaluate_trace(root,plan,result,trace)
    assert any(c['id']==check and not c['passed'] for c in report['checks']);assert report['full_task_passed'] is False
