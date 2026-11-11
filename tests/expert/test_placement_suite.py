from pathlib import Path
import copy,json
import numpy as np
import pytest
from contracts.experiments import apply_scope,checked_scope
from modules.experiments.placements import load_suite,varied_objects
ROOT=Path(__file__).resolve().parents[2]

def test_retention_record_only_does_not_change_task_facts():
    source={'checks':[{'passed':True}],'action_passed':True,'retention_passed':False,'full_task_passed':False}
    full=apply_scope(copy.deepcopy(source),'full_task');action=apply_scope(copy.deepcopy(source),'action_only')
    assert full['status']=='failed' and action['status']=='succeeded'
    assert action['experiment_passed'] and action['retention_passed'] is False and action['full_task_passed'] is False
    assert source=={'checks':[{'passed':True}],'action_passed':True,'retention_passed':False,'full_task_passed':False}

@pytest.mark.parametrize('changes',[{'action_passed':False},{'checks':[{'passed':False}]},{'checks':[]}])
def test_record_only_cannot_hide_failed_action_or_integrity(changes):
    value={'checks':[{'passed':True}],'action_passed':True,'retention_passed':False,'full_task_passed':False};value.update(changes)
    assert apply_scope(value,'action_only')['experiment_passed'] is False

def test_unknown_acceptance_scope_is_rejected():
    with pytest.raises(ValueError):checked_scope('ignore_all_failures')

def test_five_declared_cases_are_independent_and_bounded():
    suite,digest,base=load_suite(ROOT,'connector_small_v1');original=copy.deepcopy(base)
    assert len(suite['cases'])==5 and len(digest)==64
    for case in suite['cases']:
        objects=varied_objects(base,case)
        for i,obj in enumerate(objects):
            delta=np.array(obj['pose']['position_m'])-base['objects'][i]['pose']['position_m']
            assert np.allclose(delta[:2],case['xy_offsets_m'][i]) and delta[2]==0
            assert np.all(np.abs(delta[:2])<=.010000001)
            assert abs(np.linalg.norm(obj['pose']['quaternion_wxyz'])-1)<1e-8
            assert obj['collision']==base['objects'][i]['collision']
    assert base==original
    assert base['evaluation']['retention']['required'] is True

def test_unknown_suite_rejected():
    with pytest.raises(ValueError):load_suite(ROOT,'arbitrary_script')

@pytest.mark.parametrize('error,stage,expected',[
 ('Demo-retarget IK arm=1: rotation=0.15rad',True,'planning_control'),
 ('Invalid object in Py_Graph',None,'runtime_environment'),
 ('physical grasp failed',False,'physics_contact')])
def test_failure_category_keeps_planning_distinct_from_contact(error,stage,expected):
    from modules.expert_validation.service import failure_category
    report={'stage_results':{'grasp':{'passed':stage}}}
    assert failure_category({'report':{'error':error}},report)==expected
