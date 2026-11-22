from copy import deepcopy
import hashlib
import json

import pytest

from application import action_expert, registry, scene_review, supervision
from backend.store import Store
from orchestration.scheduler import Scheduler


def make_context(tmp_path,events):
    return {'root':tmp_path,'output_dir':tmp_path/'stage','run_id':'fixture','input':{},
            'dependencies':{},'check_cancel':lambda:None,'emit':lambda kind,payload:events.append((kind,payload))}


def model_choice(decision, instruction='', question=''):
    return {'status':'succeeded','content':{'decision':decision,'diagnosis':'当前证据说明问题',
            'evidence':'本次回合或阶段报告','instruction':instruction,'question':question}}


def test_supervisor_routes_scene_then_action_from_new_runtime_evidence(tmp_path,monkeypatch):
    actions=iter([model_choice('repair_scene','使用匹配画面复核工件支撑'),
                  model_choice('replan_action','按新版场景重算工具目标')])
    prompts=[]
    events=[]
    calls=[]

    class Provider:
        def __init__(self,root): pass
        def public(self): return {'configured':True}
        def request(self,messages,**kwargs):
            prompts.append(messages[0]['content'])
            return next(actions)

    corrected={'manifest_path':'candidate.json','task_spec_hash':'candidate'}
    initial={'status':'failed','reason':'初始场景回合未通过','experiment_path':'first.json',
             'action_program':{'phases':[]},'action_acceptance_path':'acceptance.json','episode_report_path':'first/episodes.json'}
    observation={'trace':'fixture-trace.npz','observations':{'phase':2,'world_rises':[{'below_table_after_peak_s':1.5}]}}

    def scene(ctx,failure,decision):
        assert failure==initial and '支撑' in decision['instruction']
        assert decision['verified_trial_review']==[observation]
        calls.append('scene')
        return initial | {'reason':'修正版上工具扫到物体'}, ctx | {'dependencies':{'scene_build':corrected}}

    def replan(ctx,failure,decision):
        assert ctx['dependencies']['scene_build']==corrected
        assert '扫到' in failure['reason']
        assert decision['verified_trial_review']==[observation]
        calls.append('action')
        return {'status':'succeeded','evidence':'verified'},ctx

    monkeypatch.setattr(supervision,'Provider',Provider)
    monkeypatch.setattr(supervision,'trial_review',lambda *args:[observation])
    monkeypatch.setattr(action_expert,'run',lambda ctx:initial)
    monkeypatch.setattr(action_expert,'repair_and_resume',scene)
    monkeypatch.setattr(action_expert,'replan_action',replan)
    ctx=make_context(tmp_path,events) | {'dependencies':{
        'task_spec':{'id':'fixture'},'scene_build':{'manifest_path':'original.json','task_spec_hash':'original'}}}
    result=registry.execute_stage('simulation',ctx)
    assert result['status']=='succeeded' and calls==['scene','action']
    assert '修正版上工具扫到物体' in prompts[1] and 'candidate.json' in prompts[1]
    assert 'below_table_after_peak_s' in prompts[0]
    report=json.loads((tmp_path/result['supervision_report_path']).read_text())
    assert [row['decision']['decision'] for row in report['rounds']]==['repair_scene','replan_action']
    assert report['rounds'][0]['outcome']['status']=='failed'
    assert report['rounds'][1]['outcome']['status']=='succeeded'
    handoffs=[payload for kind,payload in events if kind=='agent.handoff_started']
    replies={payload['exchange_id']:payload for kind,payload in events if kind=='agent.handoff_finished'}
    assert [(row['sender'],row['recipient']) for row in handoffs]==[
        ('动作专家 Agent','总控 Agent'),('总控 Agent','场景 Agent'),
        ('动作专家 Agent','总控 Agent'),('总控 Agent','动作专家 Agent')]
    assert '按新版场景重算工具目标' in handoffs[-1]['request']
    assert replies[handoffs[1]['exchange_id']]['status']=='failed'
    assert replies[handoffs[-1]['exchange_id']]['status']=='succeeded'


def scene_failure_fixture(root, name, *, history=False):
    manifest=name+'_manifest.json'
    validation=name+'_validation.json'
    spec_hash=name+'_spec_hash'
    start={'position_m':[0.,0.,.5],'quaternion_wxyz':[1.,0.,0.,0.]}
    rolled={'position_m':[.02,0.,.5],'quaternion_wxyz':[.7071067812,.7071067812,0.,0.]}
    document={'task_spec_hash':spec_hash,'spec_snapshot_path':name+'_snapshot.json','scene_path':name+'.usda',
              'objects':[{'id':'workpiece','preview_support_id':'support'}],
              'settling_samples':[{'time_s':0.,'objects':{'workpiece':start,'support':start}},
                                  {'time_s':.5,'objects':{'workpiece':rolled,'support':start}}]}
    raw=json.dumps(document).encode()
    (root/manifest).write_bytes(raw)
    checks=[{'id':'settled:workpiece','passed':False,'details':{'angular_speed_rad_s':2.}}]
    checked={'status':'failed','manifest_path':manifest,'task_spec_hash':spec_hash,'checks':checks,
             'report_path':validation,'artifact_sha256':{manifest:hashlib.sha256(raw).hexdigest()}}
    (root/validation).write_text(json.dumps(checked))
    if history:
        repair=name+'_repair_history.json'
        prior={'attempts':[{'manifest_path':manifest,'validation':validation}]}
        (root/repair).write_text(json.dumps(prior))
        return checked | {'status':'blocked','report_path':repair,'repair_report_path':repair,'validation_report_path':repair}
    return checked


def test_supervisor_uses_each_failed_candidate_and_its_own_report(tmp_path,monkeypatch):
    initial=scene_failure_fixture(tmp_path,'first_candidate',history=True)
    next_failure=scene_failure_fixture(tmp_path,'second_candidate')
    original={'manifest_path':'published.json','task_spec_hash':'published','spec_path':'published_snapshot.json'}
    choices=iter([model_choice('repair_scene','检查实际失败的候选'),model_choice('retry_stage','独立复测新候选')])
    calls=[]
    events=[]

    class Provider:
        def __init__(self,root): pass
        def public(self): return {'configured':True}
        def request(self,messages,**kwargs):
            calls.append(messages[0]['content'])
            return next(choices)

    def repair(ctx,built,validated,*,supervisor_instruction):
        assert built['manifest_path']=='first_candidate_manifest.json'
        assert validated['report_path']=='first_candidate_validation.json'
        assert validated['repair_report_path']==initial['repair_report_path']
        return next_failure

    def retry(ctx,failure,decision):
        assert ctx['dependencies']['scene_build']['manifest_path']=='second_candidate_manifest.json'
        assert failure['validation_report_path']=='second_candidate_validation.json'
        return {'status':'succeeded'},ctx

    monkeypatch.setattr(supervision,'Provider',Provider)
    monkeypatch.setattr(scene_review,'repair',repair)
    monkeypatch.setattr(scene_review,'load_snapshot',lambda *args:{'revision':2,'scene_approximation':{'source_task_spec_hash':'original'}})
    ctx=make_context(tmp_path,events) | {'dependencies':{'scene_build':original}}
    result=supervision.supervise('scene_validation',ctx,initial,retry_stage=retry,capabilities=scene_review.recovery_actions)
    assert result['status']=='succeeded' and ctx['dependencies']['scene_build']==original
    rounds=json.loads((tmp_path/result['supervision_report_path']).read_text())['rounds']
    first,second=(row['input'] for row in rounds)
    assert first['scene']==initial['manifest_path'] and second['scene']==next_failure['manifest_path']
    assert first['settling_review'][0]['maximum_rotation_from_first_sample_deg']==pytest.approx(90.)
    assert all('settling_review' in prompt for prompt in calls)
    handoff=next(p for kind,p in events if kind=='agent.handoff_started' and p['recipient']=='场景 Agent')
    assert handoff['evidence_path']=='first_candidate_validation.json'


@pytest.mark.parametrize('stale_field',['manifest_path','manifest_checksum'])
def test_inconsistent_scene_evidence_cannot_repair_from_old_scene(tmp_path,stale_field):
    failure=scene_failure_fixture(tmp_path,'candidate')
    if stale_field=='manifest_path':
        failure['manifest_path']='published.json'
    else:
        with (tmp_path/failure['manifest_path']).open('ab') as stream:
            stream.write(b' ')
    ctx=make_context(tmp_path,[]) | {'dependencies':{'scene_build':{'manifest_path':'published.json','spec_path':'old.json'}}}
    bound,working=scene_review.bind_failure(ctx,failure)
    assert bound['scene_binding_error'] and working is ctx
    assert not scene_review.recovery_actions(working,bound)


@pytest.mark.parametrize('selected',['ask_user','unregistered','retry_stage'])
def test_supervisor_stops_on_unknown_action_and_keeps_original_evidence(tmp_path,monkeypatch,selected):
    issue={'status':'blocked','reason':'文件证据不可用'}
    calls=[]

    class Provider:
        def __init__(self,root): pass
        def public(self): return {'configured':True}
        def request(self,messages,**kwargs):
            return model_choice(selected,'核对当前任务的输入文件','哪份网格是实际使用的工件？')

    def retry(ctx,previous,decision):
        calls.append(decision)
        return issue,ctx

    monkeypatch.setattr(supervision,'Provider',Provider)
    result=supervision.supervise('geometry',make_context(tmp_path,[]),issue,retry_stage=retry)
    report=json.loads((tmp_path/result['supervision_report_path']).read_text())
    assert result['reason']==issue['reason']
    assert len(calls)==(1 if selected=='retry_stage' else 0)
    if selected=='ask_user':
        assert result['status']=='waiting_input' and result['question']['agent']=='总控 Agent'
    elif selected=='unregistered':
        assert report['rounds'][0]['decision']['decision']=='stop'
    else:
        assert len(report['rounds'])==2 and report['rounds'][-1]['outcome']['status']=='blocked'


@pytest.mark.parametrize('changed_phase',[False,True])
def test_action_replan_reuses_frozen_acceptance_without_asking_for_new_conditions(tmp_path,monkeypatch,changed_phase):
    pose={'position_m':[0.,0.,.1],'quaternion_wxyz':[1.,0.,0.,0.]}
    manifest={'spec_snapshot_path':'spec.json','task_spec_hash':'spec','settled_poses':{'item':pose}}
    spec={'source':{},'objects':[{'id':'item'}],'scene':{'table':{}},
          'robot':{'arms':[{'id':'right','base_pose':pose}]}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'experiment.json').write_text('{}')
    original={'strategy':'按当前目标移动','variation_groups':[],'experience':[],'phases':[
        {'name':'接近目标','instruction':'到达目标','end_fraction':1.,'anchor':'item','arms':['right'],
         'adjustment_m':[0.,0.,0.],'rotation_adjustment_deg':[0.,0.,0.],'grippers':[],'contact_free':True,
         'waypoints':[{'arm':'right','fraction':1.,'relative_to':'item','position_m':[0.,0.,.06],
                       'orientation_xyz_deg':None,'opening_m':.03}]}]}
    changed=deepcopy(original)
    changed['phases'][0]['waypoints'][0]['position_m']=[0.,.015,.06]
    if changed_phase:
        changed['phases'][0]['name']='修改了独立验收对应的阶段'
    ctx=make_context(tmp_path,[]) | {'dependencies':{'scene_build':{'manifest_path':'manifest.json'},
                                    'task_analysis':{'proposal':{'task_summary':'移动到目标'}}}}
    previous={'status':'failed','action_program':original,'experiment_path':'experiment.json',
              'action_acceptance_path':'acceptance.json','reason':'尚未接近目标'}
    calls=[]

    def provide(ctx,prompt,schema,**kwargs):
        calls.append(ctx['stage'])
        assert 'previous_action_program' in prompt
        return {'status':'succeeded'},changed,[]

    def bind(ctx,root,out,manifest_ref,raw_manifest,spec,tracking,**kwargs):
        assert kwargs['replanned'] is True
        assert tracking['action_acceptance_path']==previous['action_acceptance_path']
        assert tracking['action_program']==changed
        return {'status':'succeeded'}

    monkeypatch.setattr(action_expert,'load_snapshot',lambda *args:spec)
    monkeypatch.setattr(action_expert,'retrieve_experience',lambda ctx:{'cases':[],'checklist_path':'experience.json'})
    monkeypatch.setattr(action_expert,'previous_scene',lambda *args,**kwargs:{'views':[]})
    monkeypatch.setattr(action_expert,'preflight',lambda ctx:{})
    monkeypatch.setattr(action_expert,'request_with_evidence',provide)
    monkeypatch.setattr(action_expert,'_bind_and_run',bind)
    result=action_expert._run_once(ctx,previous=previous)
    assert result['status']==('failed' if changed_phase else 'succeeded')
    assert calls==['action_program']
    if changed_phase:
        assert result['action_acceptance_path']==previous['action_acceptance_path']
        assert result['action_program']==original


def test_scheduler_records_worker_launch_failure_instead_of_leaving_it_queued(tmp_path):
    store=Store(tmp_path/'workbench.sqlite3')
    run=store.create_run('fixture',{},[{'id':'geometry','role':'资产','kind':'tool','deps':[]}])
    scheduler=Scheduler(tmp_path,store,interpreter_selector=lambda root,stage:str(root/'missing-python'))
    scheduler.tick()
    updated=store.get_run(run['id'])
    assert updated['status']=='failed' and updated['tasks'][0]['status']=='failed'
    rows=store.query("SELECT payload FROM events WHERE run_id=? AND type='supervisor.runtime_failure'",(run['id'],))
    assert len(rows)==1 and '执行进程启动失败' in rows[0]['payload']['message']


def test_run_detail_keeps_recent_collaboration_ahead_of_generic_model_events(tmp_path):
    store=Store(tmp_path/'workbench.sqlite3')
    run=store.create_run('fixture',{},[{'id':'geometry','role':'资产','kind':'tool','deps':[]}])
    stage=run['tasks'][0]['id']
    start=store.event(run['id'],stage,'agent.handoff_started',{'exchange_id':'transfer'})
    finish=store.event(run['id'],stage,'agent.handoff_finished',{'exchange_id':'transfer','status':'succeeded'})
    for _ in range(4):
        store.event(run['id'],stage,'model.codex_event',{'type':'progress'})
    assert [row['id'] for row in store.get_run(run['id'])['collaboration_events']]==[start,finish]
