import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from application import action_expert, action_replay, action_training, registry
from contracts.action_program import validate_acceptance, validate_program
from modules.evaluation.action_program import assess
from modules.evaluation.action_trace import TrialScreen, failed_lift, first_fall, first_support_loss, measurable_prefix, search_focus
from modules.learning.action_program import actions, clearance_window, gripper_openings, recorded_timing
from modules.learning.waypoint_expert import WaypointExpert
from modules.motion_replay.kinematics import ToolKinematics
from modules.simulation.isaac.motion_rollout import MotionRollouts


def step(name, anchor, end, arms):
    return {'name':name,'anchor':anchor,'end_fraction':end,'arms':arms,'instruction':name,
            'adjustment_m':[0.,0.,0.],'rotation_adjustment_deg':[0.,0.,0.],'grippers':[]}


def waypoint(arm,position,*,fraction=1.,relative_to=None,opening=None):
    return {'arm':arm,'fraction':fraction,'relative_to':relative_to,'position_m':position,
            'orientation_xyz_deg':None,'opening_m':opening}


def rule(kind, phase, workpiece, value, relative_to=None, *, lower=None, upper=None):
    return {'kind':kind,'phase':phase,'criterion':phase,'object_id':workpiece,'value':value,
            'relative_to':relative_to,'lower':lower or [0.,0.,0.],'upper':upper or [0.,0.,0.],
            'description':kind}


def model():
    times=np.linspace(0,4,17)
    return {'times_s':times,'arms':['left','right'],'reference_rad':np.zeros((len(times),2,7)),
            'inverse':np.broadcast_to(np.eye(7,6),(len(times),2,7,6)),
            'gripper_opening_m':np.full((len(times),2),.05),
            'limits_rad':np.broadcast_to([-1.,1.],(2,7,2))}


def trace(workpiece, destination, shift=0.):
    times=np.linspace(0,5,21)
    part=np.column_stack((np.minimum(np.maximum(times-2,0)*.2,.2)+shift,np.zeros(len(times)),
                          np.minimum(times*.05,.1)))
    goal=np.broadcast_to([.2+shift,0.,.1],part.shape)
    return {'times_s':times,'object_ids':np.asarray([workpiece,destination]),
            'object_m':np.stack((part,goal),axis=1),
            'object_wxyz':np.broadcast_to([1.,0.,0.,0.],(len(times),2,4))}


def test_actions_are_selected_per_phase_and_arm():
    phases=[step('拿起箱子','carton',.5,['right']),step('放到台面','stand',1.,['left'])]
    phases[0]['grippers']=[{'arm':'right','opening_m':0.}]
    phases[1]['rotation_adjustment_deg']=[0.,0.,10.]
    program={'strategy':'跟随示教换手搬运','phases':phases,'variation_groups':[['carton'],['stand']],
             'experience':[{'case_id':'CASE-DEMO','decision':'skip','reason':'历史案例使用不同支撑结构'}]}
    validate_program(program,['carton','stand'],['left','right'],['CASE-DEMO'])
    with pytest.raises(ValueError,match='未经检索'):
        validate_program(program,['carton','stand'],['left','right'],['CASE-OTHER'])
    learned=model()
    command=actions(learned,phases,{'carton':[.025,0.,0.],'stand':[0.,-.02,0.]},np.zeros((2,6)))
    np.testing.assert_allclose(command[8,:,0],[0.,.025])
    np.testing.assert_allclose(command[-1,0,[1,5]],[-.02,np.deg2rad(10)*.14])
    np.testing.assert_allclose(gripper_openings(learned,phases)[0],[.05,0.])
    np.testing.assert_allclose(gripper_openings(learned,phases)[8],[.05,0.])
    np.testing.assert_allclose(gripper_openings(learned,phases)[9],[.05,.05])
    np.testing.assert_allclose(gripper_openings(learned,phases,retarget=False)[8],[.05,.05])


def test_assessment_uses_task_order_and_the_live_goal():
    phases=[step('抬起箱子','carton',.5,['right']),step('移至平台','stand',1.,['right'])]
    program={'phases':phases}
    rows=[rule('rise',0,'carton',.08),rule('region',1,'carton',0.,'stand',
                                          lower=[-.02]*3,upper=[.02]*3)]
    criteria=['箱子被抬起','箱子在平台目标区域']
    verification=validate_acceptance({'checks':rows},['carton','stand'],criteria,2)
    assert assess(trace('carton','stand',.16),program,verification,{},4.)['passed']
    phases[0]['end_fraction']=.2
    assert not assess(trace('carton','stand',.16),program,verification,{},4.)['passed']
    with pytest.raises(ValueError,match='完整动作流程'):
        validate_acceptance({'checks':[rows[0],rows[1] | {'phase':0}]},['carton','stand'],criteria,2)
    rows[1]=rule('unsupported',1,'carton',0.)
    assert not assess(trace('carton','stand'),program,verification,{},4.)['passed']


def test_region_feedback_uses_the_nearest_boundary_at_the_worst_sample():
    program={'phases':[step('到达目标','stand',1.,['right'])]}
    acceptance={'checks':[rule('region',0,'part',0.,'stand',lower=[-1.,-1.,.16],upper=[1.]*3)]}
    rotated=np.sqrt(.5)
    recorded={'times_s':np.array([0.,.75,1.]),'object_ids':np.array(['part','stand']),
              'object_m':np.array([[[0.,0.,.3],[0.,0.,0.]],[[0.,-.137,0.],[0.,0.,0.]],[[0.,0.,.2],[0.,0.,0.]]]),
              'object_wxyz':np.tile([1.,0.,0.,0.],(3,2,1))}
    recorded['object_wxyz'][1,1]=[rotated,rotated,0.,0.]
    check,=assess(recorded,program,acceptance,{},1.)['checks']
    assert not check['passed']
    assert check['region_sample']['time_s']==.75
    assert check['measured']==pytest.approx(.023)
    assert check['correction_world_m']==pytest.approx([0.,-.023,0.])


def test_archived_trials_reveal_drop_after_lift_and_reject_altered_trace(tmp_path):
    output=tmp_path/'simulation'
    directory=output/'action_program_1'
    directory.mkdir(parents=True)
    program={'phases':[step('搬起工件','part',1.,['right'])]}
    acceptance={'checks':[rule('rise',0,'part',.01),rule('region',0,'part',0.,lower=[-1.,-1.,.2],upper=[1.]*3)]}
    table={'position_m':[0.,0.,.05],'size_m':[1.,1.,.1]}
    spec={'objects':[{'id':'part'}],'scene':{'table':table}}
    manifest={'spec_snapshot_path':'spec.json'}
    files={'program.json':program,'acceptance.json':acceptance,'spec.json':spec,'manifest.json':manifest}
    hashes={}
    for name,document in files.items():
        path=tmp_path/name
        path.write_text(json.dumps(document))
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    bundle={'hashes':hashes,'program_path':'program.json','acceptance_path':'acceptance.json','manifest':'manifest.json'}
    (directory/'experiment.json').write_text(json.dumps(bundle))
    recorded={'times_s':np.array([0.,.6,1.]),'object_ids':np.array(['part']),
              'object_m':np.array([[[0.,0.,.2]],[[0.,0.,.22]],[[0.,0.,.05]]]),
              'object_wxyz':np.tile([1.,0.,0.,0.],(3,1,1))}
    measured=assess(recorded,program,acceptance,{},1.)
    assert failed_lift(recorded,program,table,1.,measured,0)['kind']=='lift_loss'
    assert failed_lift(recorded,program,table,1.,{'checks':measured['checks'][:1]},0) is None
    path=directory/'recorded.npz'
    np.savez(path,**recorded)
    row={'trial':'候选 1','case':{'id':'demonstrated_layout'},'assessment':measured,
         'trace':path.name,'trace_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    index=directory/'episodes.json'
    index.write_text(json.dumps([row]))
    result={'episode_report_path':str(index.relative_to(tmp_path)),'selected_candidate':row['trial']}
    trial,=action_replay.trial_review(tmp_path,output,result)
    observation=trial['observations']
    rise,=observation['world_rises']
    assert rise['peak_rise_m']==pytest.approx(.02) and rise['below_table_after_peak_s']==1.
    assert observation['samples']['below_table_part']['world_objects']['part']['xyz_m'][2]==.05
    path.write_bytes(b'altered file')
    with pytest.raises(ValueError,match='哈希'):
        action_replay.trial_review(tmp_path,output,result)


def test_contact_window_changes_the_approach_without_changing_other_phases():
    learned=model()
    phases=[step('接近箱子','carton',.5,['right']),step('搬起箱子','carton',1.,['right'])]
    window=clearance_window(learned,phases,{'phase':0,'time_s':1.})
    offset=np.array([[0.,0.,0.],[0.,0.,.05]])
    command=actions(learned,phases,{},np.zeros((2,6)),start_offset_m=offset,offset_window_s=window)
    np.testing.assert_allclose(command[0],0.)
    np.testing.assert_allclose(command[4,1,:3],offset[1])
    np.testing.assert_allclose(command[8:],0.)
    np.testing.assert_allclose(command[:,0],0.)


def test_recorded_timing_reports_the_actuator_and_episode_relative_close():
    times=np.linspace(30.,34.,41)
    right=np.interp(times,[30.,31.6,31.9,34.],[.04,.04,.01,.01])
    tape={'timestamps_s':times,'arms':np.asarray(['left','right']),
          'gripper_feedback':np.column_stack((np.full(41,.01),right))}
    timing=recorded_timing(tape)
    assert timing['duration_s']==4.
    assert timing['measured_gripper_changes']['left']==[]
    closing,=timing['measured_gripper_changes']['right']
    assert closing['direction']=='closing' and closing['change_mm']<-.01
    assert closing['start_fraction']<=.4<closing['end_fraction']


def test_waypoint_search_updates_gripper_from_measured_rollouts_and_freezes_it(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[2]
    urdf=root/'assets/nero/nero.urdf'
    pose={'position_m':[0.,0.,0.],'quaternion_wxyz':[1.,0.,0.,0.]}
    arms=[{'id':arm,'base_pose':pose,'urdf_path':str(urdf)} for arm in ('left','right')]
    learned=model()
    learned['reference_rad'][0,1]=[.1,.3,.1,-.4,.2,.2,.1]
    learned['reference_rad'][1:]=.8
    solver=ToolKinematics(urdf,pose)
    start=solver.tool(learned['reference_rad'][:1,1])[0][0]
    goal=start+np.array([.014,.009,.01])
    part={'position_m':goal.tolist(),'quaternion_wxyz':[1.,0.,0.,0.]}
    phase=step('试开夹爪','part',1.,['right']) | {
        'waypoints':[waypoint('right',[0.,0.,0.],relative_to='part',opening=.01)]}
    program={'phases':[phase],'variation_groups':[]}
    acceptance={'criteria':['开口达到 45 毫米'],'checks':[rule('gripper_gap',0,None,.045)]}
    manifest={'spec_snapshot_path':'spec.json','scene_path':'scene.usda',
              'settled_poses':{'part':part},'robots':[{'root_path':'/World/nero2','id':'right'}]}
    spec={'objects':[{'id':'part','size_m':[.04]*3,'scale':[1.]*3}],
          'scene':{'table':{'position_m':[0.,0.,-.5],'size_m':[1.,1.,.1]}},'robot':{'arms':arms}}
    for filename,document in [('program.json',program),('acceptance.json',acceptance),('manifest.json',manifest),('spec.json',spec)]:
        (tmp_path/filename).write_text(json.dumps(document))
    np.savez(tmp_path/'tape.npz',arms=np.asarray(['left','right']))
    offsets=np.zeros((1,7))
    expert=WaypointExpert(tmp_path,arms,learned,program['phases'],manifest['settled_poses'],manifest['robots'],{'part':[.04]*3})
    commands,_,_=expert.command({'part':[.02,0.,0.]},offsets)
    achieved=solver.tool(commands[-1:,1])[0][0]
    np.testing.assert_allclose(achieved,goal+[.02,0.,0.],atol=.003)
    assert not np.array_equal(commands,learned['reference_rad'])

    class FakeRollouts:
        contacts=None

        def __init__(self,*args,**kwargs):
            pass

        def execute(self,offsets,model,commands,*,stop=None):
            gaps=model['gripper_opening_m']
            actual=np.concatenate((commands,gaps[:,:,None]/2.,-gaps[:,:,None]/2.),axis=2)
            actual[:,0,7:]=0.
            size=len(commands)
            return {'object_ids':np.asarray(['part']),'times_s':model['times_s'],
                    'object_m':np.broadcast_to(goal,(size,1,3)),'actual_joints':actual}

    monkeypatch.setattr(action_training,'MotionRollouts',FakeRollouts)
    monkeypatch.setattr(action_training,'fit',lambda *args:learned)
    out=tmp_path/'sim'
    out.mkdir()
    result=action_training.train(tmp_path,out,{'manifest':'manifest.json','program_path':'program.json',
                                 'acceptance_path':'acceptance.json','trajectory':'tape.npz'},None)
    learning=json.loads((out/'learning.json').read_text())
    candidates=learning['candidates']
    assert result['learning_mode']=='scene_waypoint_search' and result['status']=='succeeded' and learning['frozen']
    assert len(candidates)>=3
    assert candidates[1]['input_feedback']['check']['kind']=='gripper_gap'
    assert candidates[1]['parent']==candidates[0]['label']
    assert candidates[1]['partial_progress']>candidates[0]['partial_progress']
    with np.load(out/'action_expert.npz',allow_pickle=False) as checkpoint:
        assert checkpoint['waypoint_corrections'][0,6]>.035
    rows=json.loads((out/'episodes.json').read_text())
    main=next(row for row in rows if row['mode']=='independent')
    with np.load(out/main['trace'],allow_pickle=False) as data:
        assert data['commanded_gripper_m'][-1,1]>.045


def test_imported_expert_plans_a_slide_and_freezes_separate_acceptance(tmp_path, monkeypatch):
    objects=['slider','stop']
    proposal={'task_summary':'把滑块推到止挡','phases':[{'name':'推动滑块'}],
              'success_conditions':['滑块抵达示教终点']}
    spec={'source':{},'objects':[{'id':key} for key in objects],
          'robot':{'arms':[{'id':'left'},{'id':'right'}]},'scene':{'table':{}},'evaluation':{}}
    manifest={'spec_snapshot_path':'spec.json','task_spec_hash':'fixture','scene_path':'scene.usda','settled_poses':{}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    program={'strategy':'沿导轨推动','phases':[step('推动滑块','stop',1.,['left']) |
              {'waypoints':[waypoint('left',[0.,0.,.025],relative_to='stop')]}],
             'variation_groups':[],'experience':[{'case_id':'SLIDE','decision':'use','reason':'核对支撑与路径'}]}
    inputs=[]
    events=[]
    def provider(ctx, prompt, schema, **kwargs):
        inputs.append((ctx['stage'],prompt,ctx['dependencies']))
        if ctx['stage']=='action_program':
            return {'status':'succeeded'},program,[]
        return {'status':'succeeded'},{'checks':[rule('travel',0,'slider',.06)]},[]

    monkeypatch.setattr(action_expert,'load_snapshot',lambda *_:spec)
    monkeypatch.setattr(action_expert,'retrieve_experience',lambda ctx:{'cases':[{'case_id':'SLIDE'}],
                                                                       'checklist_path':'experience.json'})
    monkeypatch.setattr(action_expert,'previous_scene',lambda *args, **kwargs:{'views':[]})
    monkeypatch.setattr(action_expert,'request_with_evidence',provider)
    monkeypatch.setattr(action_expert,'preflight',lambda ctx:{'status':'blocked','reason':'fixture rig disconnected'})
    deps={'task_spec':spec,'scene_build':{'manifest_path':'manifest.json'},'task_analysis':{'proposal':proposal},
          'experience':{'cases':['SLIDE']}}
    ctx={'root':tmp_path,'run_id':'fixture','input':{},'output_dir':tmp_path/'simulation','dependencies':deps,
         'check_cancel':lambda:None,'emit':lambda kind,payload:events.append((kind,payload))}
    result=registry.execute_stage('simulation',ctx)
    assert result['status']=='blocked' and result['action_program']==program
    assert [stage for stage,_,_ in inputs]==['action_program','action_acceptance']
    assert 'SLIDE' in inputs[0][1] and 'SLIDE' not in inputs[1][1] and 'experience' not in inputs[1][2]
    acceptance=json.loads((tmp_path/result['action_acceptance_path']).read_text())
    assert acceptance['criteria']==proposal['success_conditions']
    assert ('expert.plan_ready',{'message':'已生成本次示教的动作计划','program':program}) in events


@pytest.mark.parametrize('fall_in',['none','all','holdout','initial_contact','scene_retest','approach_repair','support_loss'])
def test_training_uses_frozen_parameters_in_separate_scene_configurations(tmp_path,monkeypatch,fall_in):
    workpiece,destination='carton','stand'
    out=tmp_path/'action_program'
    out.mkdir()
    phases=[step('抬起箱子',workpiece,.5,['left']),step('移到平台',destination,1.,['right'])]
    if fall_in in ('initial_contact','scene_retest','approach_repair','support_loss'):
        phases[0]['contact_free']=True
    program={'phases':phases,'variation_groups':[[workpiece],[destination]]}
    spec={'objects':[{'id':key,'size_m':[.08]*3,'scale':[1.]*3} for key in (workpiece,destination)],
          'scene':{'table':{'size_m':[1.,1.,.1],'position_m':[0.,0.,0.]}},
          'robot':{'arms':[{'id':'left'},{'id':'right'}]}}
    if fall_in=='support_loss':
        spec['objects'][0]['preview_support_id']=destination
    manifest={'spec_snapshot_path':'spec.json','scene_path':'scene.usda','task_spec_hash':'scene-version',
              'robots':[{'id':'right','root_path':'/World/nero2'}],
              'settled_poses':{key:{'position_m':[x,0.,.1],'quaternion_wxyz':[1.,0.,0.,0.]}
                               for key,x in [(workpiece,0.),(destination,.2)]}}
    acceptance={'criteria':['抬起箱子','到达平台'],'checks':[rule('rise',0,workpiece,.08),
                rule('region',1,workpiece,0.,destination,lower=[-.02]*3,upper=[.02]*3)]}
    for filename,document in [('program.json',program),('acceptance.json',acceptance),('manifest.json',manifest),('spec.json',spec)]:
        (tmp_path/filename).write_text(json.dumps(document))
    np.savez(tmp_path/'tape.npz',arms=np.asarray(['left','right']))

    executions=[]
    hit={'kind':'unexpected_robot_contact','phase':0,'initial_window':True,'time_s':.05,
         'actors':['/World/nero2/gripper_link1','/World/Objects/carton'],'object_id':workpiece}
    learned=model()
    learned['tool_m']=np.broadcast_to([0.,0.,.2],(len(learned['times_s']),2,3))

    class FakeRollouts:
        def __init__(self,*args,record_contacts):
            assert record_contacts
            self.contacts=SimpleNamespace(rows=[])

        def execute(self, offsets, model, commands, *, stop=None):
            data=trace(workpiece,destination,offsets.get(destination,[0.])[0])
            self.contacts.rows=[]
            if fall_in=='all' or (fall_in=='holdout' and np.linalg.norm(offsets.get(destination,[0.]))>=.023):
                data['object_m'][0,0,2]=.075
                data['object_m'][1:,0,2]=0.
            if fall_in=='support_loss':
                data['object_m'][1:,0,1]=.1
            for i,(time,points) in enumerate(zip(data['times_s'],data['object_m'])):
                if i==1 and (fall_in=='initial_contact' or (fall_in=='approach_repair' and commands[0,1,2]<.04)):
                    self.contacts.rows=[(.05,*hit['actors'],.0061)]
                def snapshot():
                    return {key:value if key=='object_ids' else value[:i+1] for key,value in data.items()}
                if stop and stop(float(time),points,snapshot):
                    data=snapshot()
                    break
            executions.append((bool(stop),float(data['times_s'][-1])))
            return data

    monkeypatch.setattr(action_training,'MotionRollouts',FakeRollouts)
    monkeypatch.setattr(action_training,'fit',lambda *args:learned)
    report=action_training.train(tmp_path,out,{'manifest':'manifest.json','trajectory':'tape.npz',
                 'program_path':'program.json','acceptance_path':'acceptance.json',
                 'scene_recheck_phase':0 if fall_in=='scene_retest' else None,
                 'approach_contact':hit if fall_in=='approach_repair' else None},None)
    episodes=json.loads((tmp_path/report['episode_report_path']).read_text())
    learning=json.loads((out/'learning.json').read_text())
    assert not report['hardware_verified']
    assert report['simulated_runs']==len(executions)
    if fall_in=='support_loss':
        assert report['status']=='blocked' and len(executions)==len(learning['candidates'])==1
        assert episodes[0]['termination']['kind']=='initial_support_loss'
        assert report['scene_recheck']['support_id']==destination
        assert report['scene_recheck']['scene_manifest_path']=='manifest.json'
        assert report['validation_status']=='not_started'
        return
    if fall_in=='initial_contact':
        assert report['scene_recheck']['kind']=='unexpected_robot_contact'
        assert report['scene_recheck']['source_scene_hash']=='scene-version'
        assert report['scene_recheck']['time_s']==.05
        assert executions==[(True,.25)]
        assert report['validation_status']=='not_started'
        return
    if fall_in=='all':
        assert report['status']=='failed' and report['validation_status']=='not_started'
        assert report['test_total']==0 and not report['generalization_evaluated']
        assert not learning['frozen'] and not (out/'action_expert.npz').exists()
        assert executions==[(True,.25)]*4
        assert all(row['case']['id']=='demonstrated_layout' and row['termination']['kind']=='early_fall' for row in episodes)
        assert all(not check['evaluated'] and not check['passed'] for row in episodes for check in row['assessment']['checks'])
        with np.load(out/episodes[0]['trace']) as saved:
            assert saved['times_s'].tolist()==[0.,.25] and len(saved['commanded_joint_rad'])==2
        return

    if fall_in=='approach_repair':
        assert report['status']=='succeeded' and report['selected_candidate']=='接近修正 2'
        assert episodes[0]['termination']['kind']=='unexpected_robot_contact'
        assert learning['frozen'] and len(learning['candidates'])==2
        with np.load(out/'action_expert.npz') as saved:
            np.testing.assert_allclose(saved['start_offset_m'],[[0.,0.,0.],[0.,0.,.05]])
        nominal=next(row for row in episodes if row['trial']=='独立验证' and row['case']['id']=='demonstrated_layout')
        with np.load(out/nominal['trace']) as saved:
            np.testing.assert_allclose(saved['commanded_joint_rad'][0,1,2],.05)
            np.testing.assert_allclose(saved['commanded_joint_rad'][8:,1,2],0.)
        return

    successful=fall_in in ('none','scene_retest')
    assert report['status']==('succeeded' if successful else 'failed') and report['demonstrated_layout_passed']
    assert report['approach_verified']==(fall_in=='scene_retest')
    assert report['holdout_total']==3 and report['holdout_passed']==(3 if successful else 0)
    assert hashlib.sha256((tmp_path/report['checkpoint_path']).read_bytes()).hexdigest()==report['checkpoint_sha256']
    heldout=[row for row in episodes if row['trial']=='独立验证' and row['case']['id'].startswith('holdout_')]
    assert len(heldout)==3 and all(row['assessment']['passed']==successful for row in heldout)
    assert all(row['termination'] is None for row in heldout)
    assert all(np.linalg.norm(delta)>=.023 for row in heldout for delta in row['case']['offsets'].values())
    assert learning['selected']=='方案 1' and learning['frozen']
    assert len(learning['candidates'])==1
    assert executions==[(True,5.)]*4+[(False,5.)]*7
    assert report['reused_runs']==1 and report['stopped_runs']==0
    reused=next(row for row in episodes if 'reused_from' in row)
    source=episodes[reused['reused_from']]
    assert reused['trial']=='录制轨迹对照' and source['trial']=='独立验证'
    assert reused['trace_sha256']==source['trace_sha256'] and reused['trace']==source['trace']


def test_screen_halts_at_completed_failed_phase_without_evaluating_the_rest():
    data=trace('carton','stand')
    phases=[step('抬箱','carton',.5,['left']),step('放置','stand',1.,['left'])]
    program={'phases':phases}
    acceptance={'checks':[rule('rise',0,'carton',.2),rule('travel',1,'carton',.08)]}
    table={'position_m':[0.,0.,0.],'size_m':[1.,1.,.05]}
    screen=TrialScreen(data['object_ids'],program,acceptance,table,{},4.)
    time=data['times_s']
    assert not screen.observe(time[0],data['object_m'][0],lambda:pytest.fail('check read before phase boundary'))
    window={key:value if key=='object_ids' else value[:9] for key,value in data.items()}
    assert screen.observe(time[8],data['object_m'][8],lambda:window)
    assert screen.termination=={'kind':'phase_check_failed','phase':0,'time_s':2.}
    # Even a raw trace that contains later samples cannot grant credit beyond the gated prefix.
    checked=assess(data,program,acceptance,{},4.,through_phase=screen.through_phase)
    assert checked['checks'][0]['evaluated'] and checked['checks'][0]['measured'] is not None
    assert not checked['checks'][1]['evaluated'] and checked['checks'][1]['measured'] is None


def test_rollout_stops_physics_steps_when_the_screen_terminates():
    ticks=[]
    runner=MotionRollouts.__new__(MotionRollouts)
    runner.hz=60
    runner.contacts=None
    runner.fixed_ids=set()
    runner.world=SimpleNamespace(reset=ticks.clear,step=lambda **_:ticks.append(1))
    runner.geom=SimpleNamespace(XformCache=lambda:None)
    runner.fingers=[]
    runner.manifest={'settled_poses':{'part':{'position_m':[0.,0.,.85],'quaternion_wxyz':[1,0,0,0]}}}
    noop=lambda *args,**kwargs:None
    runner.objects={'part':SimpleNamespace(set_world_pose=noop,set_linear_velocity=noop,set_angular_velocity=noop,
        get_world_pose=lambda:(np.array([0.,0.,.85 if len(ticks)<12 else .4]),np.array([1,0,0,0])),
        get_linear_velocity=lambda:np.zeros(3))}
    runner.action=lambda **kwargs:kwargs
    robot=SimpleNamespace(dof_names=[f'joint{i}' for i in range(1,8)]+['gripper_joint1','gripper_joint2'],num_dof=9,
        set_solver_position_iteration_count=noop,set_solver_velocity_iteration_count=noop,set_joint_positions=noop,
        set_joint_velocities=noop,apply_action=noop,get_joint_positions=lambda **_:np.zeros(9))
    runner.robots=[robot,robot]
    policy={'phases':[step('抬起','part',1.,['left'])]}
    table={'position_m':[0.,0.,.725],'size_m':[1.,1.,.05]}
    screen=TrialScreen(['part'],policy,{'checks':[rule('rise',0,'part',.05)]},table,{},1.)
    settings={'times_s':np.linspace(0.,1.,11),'gripper_opening_m':np.zeros((11,2))}
    result=runner.execute({},settings,np.zeros((11,2,7)),stop=screen.observe)
    assert len(ticks)==12 and result['times_s'].tolist()==[0.,.1,.2]
    assert screen.termination['kind']=='early_fall'


def test_premature_fall_is_detected_before_the_planned_lift():
    tape=trace('carton','stand')
    tape['object_m'][:,0,2]=.85
    tape['object_m'][2:,0,2]=.2
    phases=[step('张开夹爪','carton',.5,['left']),step('抬升','carton',1.,['left'])]
    acceptance={'checks':[rule('rise',1,'carton',.08)]}
    table={'position_m':[0.,0.,.725],'size_m':[1.,1.,.05]}
    detected=first_fall(tape,{'phases':phases},acceptance,table,4.)
    assert detected['phase']==0 and detected['time_s']==.5
    checked={'checks':[{'phase':0,'kind':'gripper_gap','passed':True},
                       {'phase':1,'kind':'rise','passed':False}], 'early_fall':detected}
    assert measurable_prefix(checked,2)==0 and search_focus([checked],2)==0
    acceptance['checks'].append(rule('region',0,'carton',0.,lower=[-1,-1,0],upper=[1,1,1]))
    assert first_fall(tape,{'phases':phases},acceptance,table,4.) is None


def test_supported_part_topple_is_detected_above_the_table_only_before_contact():
    tape=trace('carton','stand')
    tape['object_m'][:]=[[0.,0.,.93],[0.,0.,.77]]
    tape['object_m'][3:,0,2]=.761
    tape['object_wxyz']=tape['object_wxyz'].copy()
    tape['object_wxyz'][3:,0]=[.70710678,.70710678,0.,0.]
    phases=[step('接近','carton',.5,['right']) | {'contact_free':True},
            step('搬起','carton',1.,['right'])]
    program={'phases':phases}
    support={'carton':'stand'}
    shapes={'carton':[.18,.02,.27]}
    loss=first_support_loss(tape,program,support,shapes,4.)
    assert loss['time_s']==.75 and loss['support_id']=='stand'
    assert loss['phase']==0 and loss['rotation_deg']==pytest.approx(90.)
    phases[0]['contact_free']=False
    assert first_support_loss(tape,program,support,shapes,4.) is None
    phases[0].update(contact_free=True,end_fraction=.1)
    assert first_support_loss(tape,program,support,shapes,4.) is None


def test_approach_reports_forceful_robot_contact_and_allows_intentional_push():
    phases=[step('保持距离','carton',.5,['right']) | {'contact_free':True},
            step('推箱子','carton',1.,['right']) | {'contact_free':False}]
    table={'position_m':[0.,0.,.725],'size_m':[1.,1.,.05]}
    finger='/World/nero2/gripper_link1'
    box='/World/Objects/carton'
    rows=[(.01,finger,box,0.),(.02,'/World/Objects/stand',box,.05)]
    screen=TrialScreen(['carton','stand'],{'phases':phases},{'checks':[]},table,{},1.,
                       contact_rows=lambda:rows,robot_roots=['/World/nero2'])
    positions=np.array([[0.,0.,.85],[0.,0.,.75]])
    assert not screen.observe(.025,positions,None)
    rows.append((.05,box,finger,.0061))
    assert screen.observe(.083,positions,None)
    assert screen.termination=={'kind':'unexpected_robot_contact','phase':0,'time_s':.05,
                               'actors':[finger,box],'object_id':'carton','impulse_ns':.0061,'initial_window':True}
    assert screen.through_phase==-1
    rows[:]=[(.75,box,finger,.015)]
    deliberate=TrialScreen(['carton'],{'phases':phases},{'checks':[]},table,{},1.,
                           contact_rows=lambda:rows,robot_roots=['/World/nero2'])
    assert not deliberate.observe(.8,positions[:1],None)


@pytest.mark.parametrize('outcome',['no_change','contact_again','clear_approach','repair_corrected'])
def test_scene_recovery_rebinds_and_keeps_candidate_for_later_replan(tmp_path,monkeypatch,outcome):
    source={'manifest_path':'original/scene_manifest.json','task_spec_hash':'original'}
    corrected={'manifest_path':'corrected/scene_manifest.json','task_spec_hash':'corrected'}
    feedback={'kind':'early_fall' if outcome=='no_change' else 'unexpected_robot_contact',
              'phase':0,'time_s':.05,'source_scene_hash':'original',
              'scene_manifest_path':source['manifest_path'],'initial_window':False}
    later=feedback | {'scene_manifest_path':corrected['manifest_path'],'source_scene_hash':'corrected',
                      'time_s':2.09,'initial_window':False}
    original={'status':'failed','reason':'现场接近异常','scene_recheck':feedback,
              'episode_report_path':'first/episodes.json','report_path':'first/expert_learning_report.json'}
    events=[]
    visits=[]

    def repair(ctx,scene,validation,*,motion_feedback,supervisor_instruction):
        assert scene==source and validation['checks'][0]['details']==motion_feedback==feedback
        assert supervisor_instruction=='复核现场支撑'
        visits.append('scene')
        result={'repair_report_path':'repair_history.json'}
        if outcome=='no_change':
            return result | {'status':'blocked'}
        return result | {'status':'succeeded','resolved_scene_build':corrected}

    def retest(ctx,initial):
        assert initial is original
        assert ctx['dependencies']['scene_build']==corrected
        assert ctx['scene_recheck_phase']==0
        visits.append('retest')
        return {'status':'failed','approach_verified':outcome=='clear_approach',
                'scene_recheck':later if outcome=='repair_corrected' else None,'episode_report_path':'retry/episodes.json'}

    monkeypatch.setattr(action_expert,'repair_scene',repair)
    monkeypatch.setattr(action_expert,'_run_rebound',retest)
    context={'root':tmp_path,'output_dir':tmp_path/'simulation',
        'dependencies':{'scene_build':source},'emit':lambda event,payload:events.append(event),
        'check_cancel':lambda:None}
    decision={'instruction':'复核现场支撑','diagnosis':'支撑关系异常'}
    result,working=action_expert.repair_and_resume(context,original,decision)
    assert visits==(['scene'] if outcome=='no_change' else ['scene','retest'])
    if outcome=='repair_corrected':
        def replan(ctx, *, previous):
            assert ctx['dependencies']['scene_build']==corrected
            assert ctx['recovery_feedback']['contact']==later
            return {'status':'succeeded','approach_verified':True,'episode_report_path':'replan/episodes.json'}
        monkeypatch.setattr(action_expert,'_run_once',replan)
        assert 'scene.published' not in events
        result,working=action_expert.replan_action(working,result,decision)
    published=outcome in ('clear_approach','repair_corrected')
    assert ('scene.published' in events)==published
    assert result.get('resolved_scene_build')==(corrected if published else None)
    assert result['scene_recheck_result']['source_episode_report_path']==original['episode_report_path']
    assert result['scene_recheck_result']['repair_report_path']=='repair_history.json'
    if outcome=='repair_corrected':
        assert result['scene_recheck_result']['approach_retry_status']=='succeeded'
