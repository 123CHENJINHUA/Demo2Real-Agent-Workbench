import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from application.stage_diagnosis import LEGACY_ORIENTATION_QUESTION, question_for_failure, restore_pending_questions
from backend.api import create_app
from backend.store import Store
from contracts.task_dialog import UPSTREAM_BLOCKED
from orchestration.scheduler import Scheduler
from workers import run_stage


def test_action_replay_uses_current_attempt_and_verified_trace(tmp_path):
    app=create_app(tmp_path,start_scheduler=False)
    store=app.state.store
    run=store.create_run('回放夹具',{},[{'id':'simulation','role':'动作专家'}])
    rid=run['id']
    tid=rid+':simulation'
    store.transition(tid,'running',started_at=10.)
    out=tmp_path/run['output_directory']/'simulation/action_program_1'
    out.mkdir(parents=True)
    report=(out/'episodes.json').relative_to(tmp_path).as_posix()
    store.event(rid,tid,'expert.rollouts',{'episode_report_path':report})
    url=f'/api/runs/{rid}/action-replay'
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            pending=await client.get(url)
            assert pending.status_code==200 and pending.json()['episodes']==[]
            trace=out/'000_test.npz'
            times=np.array([0.,.5])
            np.savez(trace,times_s=times,object_ids=['part'],object_m=np.array([[[0.,0.,.9]],[[0.,0.,.2]]]),
                     actual_joints=np.zeros((2,2,9)),object_wxyz=np.broadcast_to([1.,0.,0.,0.],(2,1,4)),
                     finger_m=np.zeros((2,4,3)),commanded_gripper_m=np.zeros((2,2)),
                     contact_time_s=[.42],contact_actors=[['/World/Objects/part','/World/Robot/link5']],contact_impulse_ns=[.12])
            checks=[{'kind':'rise','phase':0,'object_id':'part','relative_to':None,'value':.06}]
            objects=[{'id':'part','label':'夹具方块','size_m':[.07,.07,.07],'scale':[1.,1.,1.]}]
            urdf=Path(__file__).resolve().parents[2]/'assets/nero/nero.urdf'
            (out/'robot.urdf').write_bytes(urdf.read_bytes())
            rel=lambda name:(out/name).relative_to(tmp_path).as_posix()
            pose={'position_m':[0.,0.,0.],'quaternion_wxyz':[1.,0.,0.,0.]}
            spec={'objects':objects,'robot':{'arms':[{'id':side,'urdf_path':rel('robot.urdf'),'base_pose':pose}
                                                    for side in ('left','right')]},
                  'scene':{'table':{'position_m':[0.,0.,.7],'size_m':[1.,1.,.1]}}}
            for name,document in [('acceptance.json',{'checks':checks}),('program.json',{'phases':[{'name':'抬升','end_fraction':1.}]}),
                                  ('manifest.json',{'spec_snapshot_path':rel('spec.json')}),('spec.json',spec)]:
                (out/name).write_text(json.dumps(document))
            hashes={path.relative_to(tmp_path).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in out.iterdir()}
            (out/'experiment.json').write_text(json.dumps({'hashes':hashes,'manifest':rel('manifest.json'),
                                                            'program_path':rel('program.json'),'acceptance_path':rel('acceptance.json')}))
            (out/'episodes.json').write_text(json.dumps([{'case':{'id':'demonstrated_layout','offsets':{}},'trial':'方案 1',
                'planned_duration_s':4.,'termination':{'kind':'early_fall','phase':0,'time_s':.5},
                'trace':trace.name,'trace_sha256':hashes[rel(trace.name)],'assessment':{'passed':False,'checks':checks,'score':0}}]))
            indexed=(await client.get(url)).json()['episodes'][0]
            assert indexed['index']==0 and indexed['termination']['time_s']==.5
            assert (await client.get(url+'/0?attempt=old')).status_code==404
            loaded=await client.get(url+'/0?attempt=action_program_1')
            assert loaded.status_code==200,loaded.text
            data=loaded.json()
            assert data['early_fall']['time_s']==.5 and data['objects'][0]['label']=='夹具方块'
            assert data['time']==[0.,.5] and data['phases'][0]['end_s']==4.
            assert len(data['arms'][0])==2 and data['contacts'][0]['impulse_ns']==.12
            trace.write_bytes(b'changed')
            assert (await client.get(url+'/0?attempt=action_program_1')).status_code==409
    asyncio.run(exercise())


def test_expert_internal_review_does_not_ask_operator(tmp_path):
    scope='offline_physx_task_action_program'
    diag={'decision':'review','next_action':'比对当前运行的轨迹'}
    result={'scope':scope,'diagnosis':diag,'reason':'验证失败'}
    assert question_for_failure('simulation',result) is None
    question=question_for_failure('simulation',result|{'diagnosis':{'decision':'needs_input','next_action':'请指出主要的目标物体'}})
    assert question['prompt']=='请指出主要的目标物体'
    store=Store(tmp_path/'workbench.sqlite3')
    run=store.create_run('fixture',{'source_path':'/fixture'},[{'id':'simulation','role':'动作专家'}])
    tid=run['id']+':simulation'
    store.transition(tid,'waiting_input',result=result,question={'agent':'动作专家 Agent','prompt':'是否按建议处理？','context':'内部复核'})
    restore_pending_questions(store)
    restored=store.get_run(run['id'])
    assert restored['tasks'][0]['status']=='blocked'
    assert not restored['questions'][0]['pending']


def test_delete_run_hides_only_selected_version_and_preserves_evidence(tmp_path):
    app = create_app(tmp_path,start_scheduler=False)
    store = app.state.store
    original = tmp_path/'recording'
    original.mkdir()
    footage = original/'sample.txt'
    footage.write_text('original')
    source = store.create_run('示教',{'source_path':str(original)},[])
    older = store.create_run('旧版',{'source_run_id':source['id']},[],demo_run_id=source['id'])
    newer = store.create_run('新版',{'source_run_id':source['id']},[],demo_run_id=source['id'])
    for run in (source,older,newer):
        store.execute("UPDATE runs SET status='succeeded' WHERE id=?",(run['id'],))
    preview_path = older['output_directory']+'/scene_build/build_1/scene.png'
    preview = tmp_path/preview_path
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b'fixture')
    artifact = store.register_artifact(tmp_path,older['id'],None,preview_path)
    incident = store.incident(older['id'],None,'fixture','应保留的经验')
    routes = '/api/runs/'
    async def exercise():
        token = (tmp_path/'.runtime/secrets/session_token').read_text()
        headers = {'X-Session-Token':token}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            assert (await client.delete(routes+older['id'])).status_code==403
            assert (await client.delete(routes+'missing',headers=headers)).status_code==404
            refused = await client.delete(routes+source['id'],headers=headers)
            assert refused.status_code==409 and '仍引用此版本' in refused.json()['detail']
            removed = await client.delete(routes+older['id'],headers=headers)
            assert removed.status_code==200 and removed.json()['status']=='archived'
            assert (await client.delete(routes+older['id'],headers=headers)).json()==removed.json()
    asyncio.run(exercise())
    reopened = Store(store.path)
    visible = reopened.list_runs()
    assert {run['id'] for run in visible}=={source['id'],newer['id']}
    assert older['id'] not in reopened.run_previews(visible)
    assert reopened.get_run(older['id']) is None and not reopened.run_exists(older['id'])
    assert preview.read_bytes()==b'fixture' and footage.read_text()=='original'
    assert reopened.query('SELECT id FROM artifacts WHERE id=?',(artifact,))
    assert reopened.query('SELECT id FROM incidents WHERE id=?',(incident,))
    with pytest.raises(ValueError,match='已删除'):
        reopened.create_run('读旧场景',{'settings':{'manifest':preview_path}},[])


def test_archive_checks_active_worker_and_path_dependencies(tmp_path):
    store = Store(tmp_path/'.runtime/workbench.sqlite3')
    scheduler = Scheduler(tmp_path,store)
    run = store.create_run('fixture',{},[])
    rid = run['id']
    with pytest.raises(ValueError,match='执行或排队'):
        scheduler.archive(rid)
    store.execute("UPDATE runs SET status='waiting_input' WHERE id=?",(rid,))
    scheduler.processes['fixture']={'run_id':rid}
    with pytest.raises(ValueError,match='执行'):
        scheduler.archive(rid)
    scheduler.processes.clear()
    consumer = store.create_run('消费场景',{'scene_manifest_path':run['output_directory']+'/scene_build/snapshot.json'},[])
    with pytest.raises(ValueError,match=consumer['id']):
        scheduler.archive(rid)
    store.execute("UPDATE runs SET status='failed' WHERE id=?",(consumer['id'],))
    scheduler.archive(consumer['id'])
    scheduler.archive(rid)
    with pytest.raises(KeyError):
        scheduler.retry(rid)


def test_waiting_stage_releases_worker_and_reply_restarts_only_affected_stages(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_scheduler=False)
    store = app.state.store
    scheduler = app.state.scheduler
    run = store.create_run('fixture', {'source_path':'/fixture'}, [
        {'id':'scene_build','role':'场景'},
        {'id':'simulation','role':'动作','deps':['scene_build']},
        {'id':'learning','role':'训练','deps':['simulation']},
        {'id':'parallel','role':'独立阶段'},
    ])
    rid = run['id']
    tasks = {task['stage']:task['id'] for task in run['tasks']}
    store.transition(tasks['scene_build'],'succeeded',attempt=1)
    store.transition(tasks['parallel'],'blocked',error='独立问题',attempt=1)
    store.transition(tasks['simulation'],'running',attempt=1)
    output = tmp_path/run['output_directory']/'simulation'
    output.mkdir(parents=True)
    (output/'result.json').write_text(json.dumps({
        'status':'waiting_input','reason':'需要确认最终姿态',
        'question':{'agent':'动作专家 Agent','prompt':'示教结束时面板如何放置？'},
    }))
    scheduler.processes[tasks['simulation']] = {
        'process':SimpleNamespace(poll=lambda:0),'log':(output/'worker.log').open('w'),
        'stage':'simulation','run_id':rid,'output_dir':output,'job_id':'fixture',
    }
    scheduler.tick()
    paused = store.get_run(rid)
    assert not scheduler.processes and paused['status']=='waiting_input'
    assert paused['tasks'][2]['status']=='queued' and paused['questions'][0]['pending']
    qid = paused['questions'][0]['id']
    url = f'/api/runs/{rid}/questions/{qid}/reply'
    async def exercise_api():
        token = (tmp_path/'.runtime/secrets/session_token').read_text()
        headers = {'X-Session-Token':token}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            assert (await client.post(url,json={'answer':'fixture'})).status_code==403
            assert (await client.post(url,json={'answer':' '},headers=headers)).status_code==400
            response = await client.post(url,json={'answer':'只插入 15 mm'},headers=headers)
            assert response.status_code==200
            assert response.json()['questions'][0]['answer']=='只插入 15 mm'
            assert (await client.post(url,json={'answer':'重复'},headers=headers)).status_code==409

    asyncio.run(exercise_api())
    reopened = Store(store.path).get_run(rid)
    assert [task['status'] for task in reopened['tasks']]==['succeeded','queued','queued','blocked']
    assert not reopened['questions'][0]['pending']

    received = []
    monkeypatch.setattr(run_stage.signal,'signal',lambda *_:None)
    monkeypatch.setattr(run_stage.argparse.ArgumentParser,'parse_args',lambda _:SimpleNamespace(root=str(tmp_path),run_id=rid,stage='simulation'))
    monkeypatch.setattr(run_stage,'execute_stage',lambda stage,ctx: received.extend(ctx['user_replies']) or {'status':'succeeded'})
    run_stage.main()
    assert received==[{'question':'示教结束时面板如何放置？','answer':'只插入 15 mm'}]


def test_legacy_clearance_failure_does_not_create_a_measurement_question(tmp_path):
    store = Store(tmp_path/'.runtime/workbench.sqlite3')
    run = store.create_run('legacy fixture', {'source_path':'/fixture'}, [
        {'id':'simulation','role':'动作'},
        {'id':'learning','role':'训练','deps':['simulation']},
        {'id':'evaluation','role':'评估','deps':['learning']},
    ])
    rid = run['id']
    store.execute("UPDATE runs SET status='blocked' WHERE id=?",(rid,))
    store.transition(rid+':simulation','needs_configuration',attempt=2,
                     result={'reason':'场景局部稳定已通过，目标入槽还需核对。'})
    for stage in ('learning','evaluation'):
        store.transition(rid+':'+stage,'blocked',error=UPSTREAM_BLOCKED)
    restore_pending_questions(store)
    restore_pending_questions(store)
    state = store.get_run(rid)
    assert state['status']=='blocked' and not state['questions']
    assert [task['status'] for task in state['tasks']]==['needs_configuration','blocked','blocked']


def test_known_insert_interference_closes_old_precision_question(tmp_path):
    store = Store(tmp_path/'.runtime/workbench.sqlite3')
    run = store.create_run('fixture',{'source_path':'/fixture'},[
        {'id':'simulation','role':'动作'},
        {'id':'learning','role':'学习','deps':['simulation']},
        {'id':'evaluation','role':'验收','deps':['learning']},
    ])
    rid = run['id']
    question = {'agent':'动作专家 Agent','prompt':'请提供精确角度和深度','report_path':'insertion.json'}
    checks = [{'id':'upright_insertion_clearance','passed':False,'details':{
        'slot_label':'目标槽 A','source_geometry':{'status':'source_interference'}}}]
    output = {'status':'waiting_input','checks':checks,'report_path':'insertion.json',
              'diagnosis':{'decision':'needs_input','next_action':question['prompt']}}
    assert question_for_failure('simulation',output) is None
    store.execute("UPDATE runs SET status='waiting_input' WHERE id=?",(rid,))
    store.transition(rid+':simulation','waiting_input',question=question,result=output | {'question':question},attempt=1)
    restore_pending_questions(store)
    restore_pending_questions(store)
    revised = store.get_run(rid)
    assert revised['status']=='blocked' and all(task['status']=='blocked' for task in revised['tasks'])
    historical = revised['questions'][0]
    assert not historical['pending'] and historical['answer'] is None and historical['prompt']==question['prompt']
    result = revised['tasks'][0]['result']
    assert result['unsupported_capability']=='alternative_insertion_pose'
    assert result['checks']==checks and result['report_path']=='insertion.json'
    assert len(store.query("SELECT id FROM events WHERE run_id=? AND type='agent.question_closed'",(rid,)))==1


def test_existing_pending_orientation_question_is_reworded_in_place(tmp_path):
    store = Store(tmp_path/'.runtime/workbench.sqlite3')
    run = store.create_run('fixture',{'source_path':'/fixture'},[{'id':'scene_validation','role':'场景'}])
    rid = run['id']
    question = {'agent':'场景校验 Agent','prompt':LEGACY_ORIENTATION_QUESTION,'context':'旧上下文','report_path':'report.json'}
    store.execute("UPDATE runs SET status='waiting_input' WHERE id=?",(rid,))
    result = {'status':'waiting_input','question':question,'visual_comparison_status':'orientation_unverified',
              'visual_comparison_path':'report.json','checks':[{'id':'visual_orientation:holder','passed':False}]}
    store.transition(rid+':scene_validation','waiting_input',question=question,result=result,attempt=1)
    original = store.questions(rid)[0]
    restore_pending_questions(store)
    restore_pending_questions(store)
    returned = store.get_run(rid)
    new = returned['questions'][0]
    assert new['id']==original['id'] and new['pending'] and new['answer'] is None
    assert '翻转' not in new['prompt'] and '目标位置' in new['prompt']
    assert new['report_path']==original['report_path'] and returned['status']=='waiting_input'
    assert returned['tasks'][0]['result']['checks']==result['checks']
    assert returned['tasks'][0]['result']['question']['prompt']==new['prompt']
    assert [event['payload']['previous_prompt'] for event in store.query(
        "SELECT payload FROM events WHERE run_id=? AND type='agent.question_revised'",(rid,))]==[LEGACY_ORIENTATION_QUESTION]


def test_active_preview_is_published_before_worker_completion(tmp_path):
    store = Store(tmp_path/'.runtime/workbench.sqlite3')
    run = store.create_run('fixture', {}, [{'id':'scene_build','role':'场景'},{'id':'simulation','role':'动作'}])
    rid = run['id']
    prefix = run['output_directory']
    baseline = f'{prefix}/scene_build/build_10/scene.png'
    revised = f'{prefix}/simulation/repair/build_20/scene.png'
    rejected = f'{prefix}/simulation/repair/build_30/scene.png'
    manifest = revised.replace('scene.png','scene_manifest.json')
    for path in (baseline,revised,rejected,manifest):
        (tmp_path/path).parent.mkdir(parents=True,exist_ok=True)
        (tmp_path/path).write_text('fixture')
    old_id = store.register_artifact(tmp_path,rid,rid+':scene_build',baseline)
    store.transition(rid+':scene_build','succeeded',result={'preview_path':baseline})
    store.transition(rid+':simulation','running')
    store.register_artifact(tmp_path,rid,rid+':simulation',rejected)
    assert store.run_previews([run])[rid]['artifact_id']==old_id
    store.transition(rid+':scene_build','succeeded',result={'preview_path':baseline,'requires_visual_review':True})
    assert rid not in store.run_previews([run])
    assert store.get_run(rid)['active_scene']['review_pending']
    run_stage.publish_scene(store,tmp_path,tmp_path/prefix,rid,run['tasks'][1],{
        'build':{'preview_path':revised,'manifest_path':manifest,'task_spec_hash':'fixture-revision'}})
    updated = store.get_run(rid)
    assert updated['tasks'][1]['status']=='running'
    assert updated['active_scene']['build']['preview_path']==revised
    artifact = next(row for row in updated['artifacts'] if row['path']==revised)
    assert store.run_previews([run])[rid]['artifact_id']==artifact['id']
    with pytest.raises(ValueError,match='当前任务'):
        run_stage.publish_scene(store,tmp_path,tmp_path/prefix,rid,run['tasks'][1],{
            'build':{'preview_path':'runs/sibling/scene.png','manifest_path':'runs/sibling/scene_manifest.json'}})
