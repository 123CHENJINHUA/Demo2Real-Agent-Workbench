import json, shutil, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.api import create_app
from backend.store import Store
from orchestration.providers.compatible import Provider
from orchestration.scheduler import Scheduler
from orchestration.workflow import STAGES

PROJECT=Path(__file__).resolve().parents[2]

@pytest.fixture
def project(tmp_path):
    root=tmp_path/'project';root.mkdir();(root/'config').mkdir()
    (root/'config/environments.yaml').write_text('schema_version: 1\nenvironments: {}\n')
    # Copy production source into the isolated project. No symlinks or old-project imports.
    for package in ('backend','orchestration','application','workers','modules','contracts'):
        if (PROJECT/package).exists():shutil.copytree(PROJECT/package,root/package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if (PROJECT/'config/policies.yaml').exists():shutil.copy2(PROJECT/'config/policies.yaml',root/'config/policies.yaml')
    (root/'knowledge').mkdir()
    (root/'knowledge/taxonomy.yaml').write_text('categories: {workflow_integrity: {}}\nstages: [runtime]\ndiagnosis_states: [unknown]\n')
    return root

def test_api_session_origin_and_offline_scope(project):
    with TestClient(create_app(project,start_scheduler=False)) as client:
        assert client.get('/api/health').json()['hardware_commands_enabled'] is False
        token=client.get('/api/session').json()['token'];headers={'X-Session-Token':token}
        assert client.get('/api/robots/status').status_code==404
        for action in ('connect','enable','shadow'):
            assert client.post('/api/robots/'+action,json={},headers=headers).status_code==405
        assert not any(path.startswith('/api/robots/') for path in client.get('/openapi.json').json()['paths'])
        assert client.get('/api/session',headers={'Origin':'https://evil.example'}).status_code==403
        assert client.put('/api/settings/provider',json={'model':'x'},headers=headers).status_code==200
        assert client.get('/api/settings/provider').json()['configured'] is False
        assert client.post('/api/settings/provider/test',json={},headers=headers).json()['status']=='needs_configuration'
        assert client.get('/api/fs',params={'path':'relative/path'}).status_code==400
        source=project/'input';source.mkdir();(source/'metadata.json').write_text('{}')
        assert client.post('/api/runs',json={'source_path':str(source)}).status_code==403
        run=client.post('/api/runs',json={'source_path':str(source),'name':'真实离线导入'},headers=headers).json()
        assert len(run['tasks'])==len(STAGES) and run['status']=='queued'
        assert client.post('/api/runs/'+run['id']+'/cancel',json={},headers=headers).json()['status']=='cancelled'
        assert client.post('/api/runs/'+run['id']+'/retry',json={},headers=headers).json()['status']=='queued'
        events=client.get('/api/runs/'+run['id']+'/events').json()
        assert events['cursor']>0 and any(e['type']=='run.created' for e in events['events'])
        case=client.post('/api/incidents',json={'summary':'测试离线输入缺失'},headers=headers)
        assert case.status_code==200 and case.json()['record']['hardware_verified'] is False

def test_run_events_can_start_from_the_latest_page(project):
    with TestClient(create_app(project, start_scheduler=False)) as client:
        store = client.app.state.store
        rid = store.create_run('事件回放夹具', {}, [])['id']
        with store.connect() as db:
            db.executemany('INSERT INTO events(run_id,type,payload,created_at) VALUES(?,?,?,?)',
                           [(rid, 'progress', '{}', 1.0)] * 510)
        first = client.get(f'/api/runs/{rid}/events').json()
        latest = client.get(f'/api/runs/{rid}/events', params={'tail': True}).json()
        assert len(first['events']) == len(latest['events']) == 500
        assert first['events'][0]['type'] == 'run.created'
        assert latest['cursor'] == latest['events'][-1]['id'] > first['cursor']
        assert latest['events'][0]['id'] > first['events'][0]['id']
        later = store.event(rid, None, 'run.retried', {})
        delta = client.get(f'/api/runs/{rid}/events', params={'after': latest['cursor'], 'tail': True}).json()
        assert [event['id'] for event in delta['events']] == [later]


def test_run_previews_select_registered_artifacts_per_run(project):
    with TestClient(create_app(project, start_scheduler=False)) as client:
        store = client.app.state.store
        demo = store.create_run('demo', {}, [])
        scene = store.create_run('scene', {'preview': True}, [], demo_run_id=demo['id'])
        rgbd = store.create_run('rgbd', {}, [])
        empty = store.create_run('empty', {}, [])

        def register(run, path, media_type='image/png'):
            return store.add_artifact(run['id'], None, {'path': path, 'media_type': media_type, 'sha256': 'recorded'})

        prefix = demo['output_directory']
        expected_frame = register(demo, f'{prefix}/data_audit/video_00/frame_002.jpg', 'image/jpeg')
        register(demo, f'{prefix}/data_audit/video_00/frame_005.jpg', 'image/jpeg')
        register(demo, f'{prefix}/data_audit/video_01/frame_002.jpg', 'image/jpeg')
        register(demo, f'{prefix}/files/uploads/scene_build/build_99/scene.png')
        prefix = scene['output_directory']
        expected_scene = register(scene, f'{prefix}/scene_build/build_100/scene.png')
        register(scene, f'{prefix}/scene_build/build_99/scene.png')
        register(empty, f'{prefix}/scene_build/build_100/scene.png')
        prefix = rgbd['output_directory']
        expected_rgbd = register(rgbd, f'{prefix}/simulation/isaac_100/trial_0/vision/visualization/frame_10.0/panels.png')
        register(rgbd, f'{prefix}/simulation/isaac_100/trial_0/vision/visualization/frame_9.0/panels.png')

        assert 'previews' not in client.get('/api/runs').json()
        response = client.get('/api/runs', params={'previews': True}).json()
        assert response['previews'] == {
            demo['id']: {'artifact_id': expected_frame, 'kind': 'demonstration'},
            scene['id']: {'artifact_id': expected_scene, 'kind': 'scene'},
            rgbd['id']: {'artifact_id': expected_rgbd, 'kind': 'rgbd'},
        }


def test_store_restart_marks_running_interrupted(project):
    store=Store(project/'.runtime/workbench.sqlite3');run=store.create_run('recover',{},STAGES)
    store.transition(run['tasks'][0]['id'],'running',pid=123)
    store.execute("UPDATE runs SET status='running' WHERE id=?",(run['id'],))
    store.recover();detail=store.get_run(run['id'])
    assert detail['status']=='interrupted' and detail['tasks'][0]['status']=='interrupted'
    assert detail['tasks'][0]['pid'] is None

def test_run_preserves_selected_specialist_count(project):
    source=project/'input';source.mkdir()
    with TestClient(create_app(project,start_scheduler=False)) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        payload={'source_path':str(source)}
        for value in (None,2,3,4):
            response=client.post('/api/runs',json=payload|{'specialist_count':value},headers=headers)
            assert response.status_code==200,response.text
            rid=response.json()['id']
            assert client.get('/api/runs/'+rid).json()['input'].get('specialist_count')==value
        for value in (1,5,True,'3'):
            assert client.post('/api/runs',json=payload|{'specialist_count':value},headers=headers).status_code==400

def test_browse_and_import_demo_outside_home_and_project(project,monkeypatch):
    home=project.parent/'home';(home/'Downloads').mkdir(parents=True)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    source=project.parent/'外置数据'/'示教 01';source.mkdir(parents=True)
    (source/'episode.json').write_text('{"frames":3}')
    with TestClient(create_app(project,start_scheduler=False)) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        assert client.get('/api/fs').json()['path']==str(home)
        assert client.get('/api/fs',params={'path':'/'}).json()['parent'] is None
        result=client.get('/api/fs',params={'path':str(source.parent)}).json()
        assert result['parent']==str(project.parent)
        assert any(item['path']==str(source) and item['is_dir'] for item in result['entries'])
        assert client.get('/api/fs',params={'path':str(project/'.runtime')}).status_code==403
        assert client.post('/api/runs',json={'source_path':str(project.parent)},headers=headers).status_code==400
        response=client.post('/api/runs',json={'source_path':str(source)},headers=headers)
        assert response.status_code==200,response.text
        run=response.json()
        assert run['input']['source_path']==str(source)
        assert not (source/'manifest.json').exists()
        scheduler=Scheduler(project,client.app.state.store)
        scheduler.start()
        try:
            copy=project/run['output_directory']/'demo_import/dataset/episode.json'
            deadline=time.monotonic()+15
            while not copy.is_file() and time.monotonic()<deadline:
                time.sleep(.1)
            assert copy.read_bytes()==(source/'episode.json').read_bytes()
        finally:scheduler.stop()

def test_new_demo_can_select_no_model_one_model_or_mixed_models(project):
    from application.asset_review import run as audit_geometry
    from modules.demo_import import run as import_demo

    source=project.parent/'示教';source.mkdir()
    (source/'episode.json').write_text('{"frames":3}')
    library=project.parent/'物体模型';library.mkdir()
    obj=library/'工件.OBJ';obj.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    stl=library/'支架.stl';stl.write_text('solid part\nvertex 0 0 0\nvertex 2 0 0\nvertex 0 2 0\nendsolid part\n')
    other=library/'notes.txt';other.write_text('not a model')
    (library/'另一个目录').mkdir()
    inventory=project/'assets';inventory.mkdir();shutil.copyfile(obj,inventory/'unselected.obj')
    with TestClient(create_app(project,start_scheduler=False)) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        files=client.get('/api/fs',params={'path':str(library),'models':True}).json()['entries']
        assert {item['name'] for item in files}=={obj.name,stl.name,'另一个目录'}
        fields={'source_path':str(source)}
        invalid=client.post('/api/runs',json=fields|{'model_sources':[str(other)]},headers=headers)
        assert invalid.status_code==400
        for selected in ([],[obj],[obj,stl]):
            desired=[str(path) for path in selected]
            response=client.post('/api/runs',json=fields|{'model_sources':desired},headers=headers)
            assert response.status_code==200,response.text
            task=response.json()
            assert task['input']['model_sources']==desired
            folder=project/task['output_directory']
            context={'root':project,'input':task['input'],'output_dir':folder/'demo_import',
                     'emit':lambda *args:None,'check_cancel':lambda:None}
            imported=import_demo(context)
            report=audit_geometry(context|{'output_dir':folder/'geometry','dependencies':{'demo_import':imported}})
            assert len(report['object_models'])==len(selected)
            for original,asset in zip(selected,report['object_models']):
                copy=project/asset['path']
                assert copy.is_relative_to(folder/'geometry/models') and copy.name==original.name
                assert copy.read_bytes()==original.read_bytes()
            assert import_demo(context)['reused_verified_import']
            assert report['status']=='completed'
            assert report['asset_selection_scope']==('explicit_task_assets' if selected else 'no_task_assets')
            assert [item['sha256'] for item in report['assets']]==[item['sha256'] for item in report['object_models']]
            assert all(item['status']=='audited' for item in report['assets'])
        old=client.post('/api/runs',json=fields|{'asset_paths':['/assets/not-in-project.obj']},headers=headers).json()
        store=client.app.state.store
        route='/api/runs/'+old['id']+'/object-models'
        assert client.put(route,json={'model_sources':[str(obj)]},headers=headers).status_code==409
        for stage in old['tasks']:
            store.transition(stage['id'],'succeeded' if stage['stage']=='demo_import' else 'blocked')
        store.execute('UPDATE runs SET status=? WHERE id=?',('failed',old['id']))
        updated=client.put(route,json={'model_sources':[str(obj),str(stl)]},headers=headers).json()
        assert updated['status']=='failed' and updated['input']['model_sources']==[str(obj),str(stl)]
        assert 'asset_paths' not in updated['input']
        assert next(t['status'] for t in updated['tasks'] if t['stage']=='demo_import')=='succeeded'


def test_worker_writes_child_artifacts_under_source_demo(project):
    source=project/'input';source.mkdir();(source/'episode.json').write_text('{"frames":3}')
    store=Store(project/'.runtime/workbench.sqlite3')
    stages=[{'id':'demo_import','role':'CPU import fixture','deps':[]}]
    demo=store.create_run('demo',{'source_path':str(source)},stages)
    child=store.create_run('derived',{'source_path':str(source)},stages,demo_run_id=demo['id'])
    scheduler=Scheduler(project,store);scheduler.start()
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            detail=store.get_run(child['id'])
            if detail['status'] in ('succeeded','blocked','failed'):break
            time.sleep(.1)
        assert detail['status']=='succeeded',detail
        directory=f"runs/{demo['id']}/derived/{child['id']}"
        assert detail['output_directory']==directory
        assert detail['jobs'][0]['log_path']==directory+'/demo_import/worker.log'
        artifacts={a['path'] for a in detail['artifacts']}
        assert directory+'/demo_import/manifest.json' in artifacts
        assert directory+'/demo_import/result.json' in artifacts
        assert (project/directory/'demo_import/dataset/episode.json').read_bytes()==(source/'episode.json').read_bytes()
        assert not (project/'runs'/child['id']).exists()
    finally:scheduler.stop()

def test_recovery_training_inherits_demo_folder_from_grouped_and_legacy_batches(project,monkeypatch):
    from application import placement_runs

    with TestClient(create_app(project,start_scheduler=False)) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        store=client.app.state.store
        demo=store.create_run('demo',{},[])
        case=store.create_run('collection case',{'source_run_id':demo['id']},[])
        legacy=project/'runs'/'placement_123';legacy.mkdir(parents=True)
        (legacy/'submitted.json').write_text(json.dumps({'runs':[{'run_id':case['id']}]}))
        suite={'id':'fixture','cases':[{'id':'cpu-fixture','title':'fixture case'}]}
        monkeypatch.setattr(placement_runs,'load_suite',lambda root,identifier:(suite,'fixture digest',{'source':{'run_id':demo['id']}}))
        grouped=placement_runs.create_suite(project,store,'fixture')
        assert grouped['output_directory']==f"runs/{demo['id']}/experiments/{grouped['batch_id']}"
        assert (project/grouped['output_directory']/'suite.json').is_file()
        grouped_case=store.get_run(grouped['runs'][0]['run_id'])
        assert grouped_case['output_directory']==f"runs/{demo['id']}/derived/{grouped_case['id']}"
        batches=('placement_123',grouped['batch_id'])
        response=client.post('/api/learning/recovery',json={'collection_batches':batches},headers=headers)
        assert response.status_code==200,response.text
        run=response.json()
        assert run['input']['source_run_id']==demo['id']
        assert run['output_directory']==f"runs/{demo['id']}/derived/{run['id']}"
        other=store.create_run('other demo',{},[])
        other_case=store.create_run('other collection case',{'source_run_id':other['id']},[])
        (legacy/'submitted.json').write_text(json.dumps({'runs':[{'run_id':other_case['id']}]}))
        response=client.post('/api/learning/recovery',json={'collection_batches':batches},headers=headers)
        assert response.status_code==200,response.text
        mixed=response.json()
        assert mixed['output_directory']==f"runs/{mixed['id']}"

def test_real_subprocess_import_and_blocked_semantics(project):
    source=project/'input';source.mkdir();(source/'episode.json').write_text(json.dumps({'frames':3}))
    store=Store(project/'.runtime/workbench.sqlite3');run=store.create_run('subprocess',{'source_path':str(source)},STAGES)
    scheduler=Scheduler(project,store);scheduler.start()
    try:
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            detail=store.get_run(run['id'])
            if detail['status'] in ('blocked','failed','succeeded'):break
            time.sleep(.15)
        assert detail['status']=='blocked',detail
        first=detail['tasks'][0]
        assert first['status']=='succeeded',first
        imported=project/first['result']['dataset_path']/'episode.json'
        assert imported.read_bytes()==(source/'episode.json').read_bytes()
        assert detail['artifacts']
        assert detail['jobs'] and all(job['status']!='running' for job in detail['jobs'])
        assert all(t['status']!='succeeded' for t in detail['tasks'] if t['stage'] in ('task_analysis','simulation','learning','deployment'))
    finally:scheduler.stop()

def test_real_subprocess_cancel_and_retry(project):
    # Explicit slow test fixture checks process supervision, not task performance.
    (project/'modules/demo_import/__init__.py').write_text("import time\ndef run(ctx):\n ctx['emit']('progress',{'completed':0,'total':1})\n time.sleep(30)\n return {'status':'completed'}\n")
    store=Store(project/'.runtime/workbench.sqlite3');run=store.create_run('cancel',{},STAGES)
    scheduler=Scheduler(project,store);scheduler.start()
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if store.get_run(run['id'])['tasks'][0]['status']=='running':break
            time.sleep(.1)
        scheduler.cancel(run['id']);detail=store.get_run(run['id'])
        assert detail['status']=='cancelled'
        assert not scheduler.processes
        scheduler.retry(run['id']);assert store.get_run(run['id'])['tasks'][0]['status']=='queued'
    finally:scheduler.stop()

def test_provider_real_http_and_secret_redaction(project):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append({'path':self.path,'auth':self.headers.get('Authorization'),'body':json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'choices':[{'message':{'content':'fixture actual HTTP response'}}]}).encode())
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        provider=Provider(project);value=provider.save({'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'fixture-model','api_key':'backend-only-key'})
        assert 'api_key' not in value and value['has_api_key']
        result=provider.request([{'role':'user','content':'test'}]);assert result['status']=='succeeded'
        assert received[0]['path']=='/v1/chat/completions' and received[0]['auth']=='Bearer backend-only-key'
        assert provider.path.stat().st_mode & 0o077==0
    finally:server.shutdown();server.server_close();thread.join()


def test_diagnostics_are_explicit_and_supervisor_is_singleton(project):
    with TestClient(create_app(project,start_scheduler=False)) as client:
        token=client.get('/api/session').json()['token'];headers={'X-Session-Token':token}
        assert client.post('/api/diagnostics',json={'kind':'arbitrary_shell'},headers=headers).status_code==400
        run=client.post('/api/diagnostics',json={'kind':'connector_bench'},headers=headers).json()
        assert run['input']['diagnostic_only'] is True
        assert len(run['tasks'])==1 and run['tasks'][0]['stage']=='simulation'
    store=Store(project/'.runtime/workbench.sqlite3')
    first=Scheduler(project,store);first.start()
    try:
        with pytest.raises(RuntimeError,match='第二个调度器'):Scheduler(project,store).start()
    finally:first.stop()


def test_cancel_cleans_nested_separate_session_process(project):
    import os, signal
    fixture = """import os, signal, subprocess, sys, time
def run(ctx):
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'],start_new_session=True)
 (ctx['output_dir']/'nested.pid').write_text(str(child.pid))
 try:
  while child.poll() is None:time.sleep(.1)
 finally:
  if child.poll() is None:
   os.killpg(child.pid,signal.SIGTERM)
   try:child.wait(timeout=3)
   except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=3)
 return {'status':'completed'}
"""
    (project/'modules/demo_import/__init__.py').write_text(fixture)
    store=Store(project/'.runtime/workbench.sqlite3');run=store.create_run('nested-cancel',{},STAGES)
    scheduler=Scheduler(project,store);scheduler.start()
    nested_pid=None
    try:
        pidfile=project/'runs'/run['id']/'demo_import/nested.pid'
        deadline=time.monotonic()+8
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.1)
        assert pidfile.exists()
        nested_pid=int(pidfile.read_text())
        scheduler.cancel(run['id'])
        with pytest.raises(ProcessLookupError):os.kill(nested_pid,0)
        assert store.get_run(run['id'])['jobs'][0]['status']=='cancelled'
    finally:
        scheduler.stop()
        if nested_pid:
            try:os.kill(nested_pid,signal.SIGKILL)
            except ProcessLookupError:pass


def test_policy_selection_deployments_and_task_json_budget(project):
    with TestClient(create_app(project,start_scheduler=False)) as client:
        token=client.get('/api/session').json()['token'];headers={'X-Session-Token':token}
        active=client.get('/api/policies/active')
        assert active.status_code==200 and active.json()['hardware_motion_triggered'] is False
        switched=client.put('/api/policies/active',json={'role':'supervisor','plugin_id':'rule_supervisor_v1'},headers=headers)
        assert switched.status_code==200 and switched.json()['active']['supervisor']=='rule_supervisor_v1'
        assert client.put('/api/policies/active',json={'role':'motion','plugin_id':'rule_supervisor_v1'},headers=headers).status_code==400
        assert client.get('/api/deployments').json()['real_motion_authorized'] is False
        source=project/'input';source.mkdir()
        assert client.post('/api/runs',json={'source_path':str(source),'learning':[]},headers=headers).status_code==400
        assert client.post('/api/runs',json={'source_path':str(source),'goal':'x'*66000},headers=headers).status_code==413
        run=client.post('/api/runs',json={'source_path':str(source),'learning':{'epochs':3},'evaluation':{},'deployment':{}},headers=headers).json()
        assert run['input']['learning']['epochs']==3
        training=client.post('/api/diagnostics',json={'kind':'supervisor_bc'},headers=headers).json()
        assert training['input']['scope']=='supervised_skill_classification_only' and training['tasks'][0]['stage']=='learning'
        assert client.post('/api/diagnostics',json={'kind':'supervisor_bc','learning':{'dataset_path':'/etc/passwd'}},headers=headers).status_code==400


def test_failure_archive_classification_and_immutable_snapshots(project):
    from application.runtime import archive_failure
    (project/'knowledge/taxonomy.yaml').write_text('categories: {workflow_integrity: {}, physics_contact: {}}\nstages: [runtime, simulation_test]\ndiagnosis_states: [unknown]\n')
    log=project/'worker.log';log.write_text('first failure')
    summary=project/'summary.json';summary.write_text('{"retained":false}')
    record=archive_failure(project,'run-test','simulation','retention failed','worker.log',{'failure_category':'physics_contact','failure_stage':'simulation_test','evidence_paths':['summary.json']})
    assert record['primary_category']=='physics_contact' and record['mode']=='simulation'
    log.write_text('next attempt log')
    snapshots=[project/e['path'] for e in record['evidence']]
    assert any(p.read_text()=='first failure' for p in snapshots)
    assert all(p.exists() for p in snapshots)
    fallback=archive_failure(project,'run-test','bad','unknown','worker.log',{'failure_category':'invented','failure_stage':'invented'})
    assert fallback['primary_category']=='workflow_integrity' and fallback['stage']=='runtime'


def test_incidents_use_latest_file_revision_deduplicate_sql_and_show_cli(project):
    from modules.experience import record_incident
    (project/'knowledge/taxonomy.yaml').write_text('categories: {workflow_integrity: {}, physics_contact: {}}\nstages: [runtime, simulation_test]\ndiagnosis_states: [unknown]\n')
    with TestClient(create_app(project,start_scheduler=False)) as client:
        token=client.get('/api/session').json()['token'];headers={'X-Session-Token':token}
        posted=client.post('/api/incidents',json={'summary':'initial SQL-linked failure'},headers=headers)
        assert posted.status_code==200
        original=posted.json()['record'];identifier=original['incident_id']
        # Immutable file revision made outside the API supersedes SQL's old category/status.
        updated=record_incident(project,original|{'primary_category':'physics_contact','stage':'simulation_test','status':'partially_verified','summary':'updated physical retention failure','revision_reason':'physics investigation'})
        assert updated['revision']==2
        # The worker's nested reference must deduplicate just like direct POST details.
        client.app.state.store.incident('some-run','some-task','workflow_integrity','stale nested SQL',{'experience_record':original})
        cli=record_incident(project,{'observed_behavior':'CLI-only post-release retention failed','summary':'CLI-only partial validation failure','primary_category':'physics_contact','stage':'simulation_test','status':'open'})
        orphan_id=client.app.state.store.incident(None,None,'workflow_integrity','archive failed; preserve SQL',{'archive_error':'test evidence unavailable'})
        items=client.get('/api/incidents').json()['incidents']
        assert len(items)==3
        latest=[item for item in items if item['id']==identifier]
        assert len(latest)==1 and latest[0]['category']=='physics_contact'
        assert latest[0]['status']=='partially_verified' and latest[0]['summary']=='updated physical retention failure'
        assert latest[0]['record']['revision']==2
        assert any(item['id']==cli['incident_id'] and item['source']=='knowledge' for item in items)
        assert any(item['id']==orphan_id and item['source']=='sqlite' for item in items)


def test_history_groups_verified_legacy_findings_and_appends_without_overwriting(project):
    from modules.experience import record_incident, search

    (project/'knowledge/taxonomy.yaml').write_text('categories: {workflow_integrity: {}, physics_contact: {}}\nstages: [runtime, asset_modeling]\ndiagnosis_states: [unknown]\n')
    with TestClient(create_app(project,start_scheduler=False)) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        store=client.app.state.store
        run=store.create_run('History fixture',{},[])
        folder=project/'runs'/run['id'];folder.mkdir(parents=True)
        snapshot=folder/'failure_result.json'
        snapshot.write_text(json.dumps({'result':{'code':'mesh_clearance_fixture','message':'Cavity clearance needs review','primary_category':'physics_contact','stage':'asset_modeling'}}))
        reports=[record_incident(project,{'observed_behavior':'Partial validation failed','summary':'Partial validation failed','run_id':run['id'],'evidence':[snapshot.relative_to(project).as_posix()]}) for _ in range(2)]
        response=client.get('/api/incidents',params={'page':1,'limit':1,'category':'physics_contact','query':'clearance'}).json()
        assert response['total']==1 and response['report_count']==2
        row=response['incidents'][0]
        assert row['finding_code']=='mesh_clearance_fixture' and row['stage']=='asset_modeling'
        assert {item['id'] for item in row['occurrences']}=={item['incident_id'] for item in reports}
        assert len(search(project,'clearance'))==1

        created=client.post('/api/incidents',json={'summary':'Observed clearance','observed_behavior':'Grinding noise','expected_behavior':'Smooth contact','run_id':run['id'],'evidence':[snapshot.relative_to(project).as_posix()]},headers=headers).json()
        identifier=created['id']
        original=project/created['record']['record_path'];first=original.read_bytes()
        payload={'expected_revision':1,'note':'Retest found a tight bearing','check':'Measure clearance before replay'}
        endpoint=f'/api/incidents/{identifier}'
        appended=client.post(endpoint+'/revisions',json=payload,headers=headers)
        assert appended.status_code==200,appended.text
        assert client.post(endpoint+'/revisions',json=payload,headers=headers).status_code==409
        assert original.read_bytes()==first
        detail=client.get(endpoint).json()
        assert [item['revision'] for item in detail['revisions']]==[2,1]
        assert detail['record']['diagnosis']['state']=='unknown'
        assert detail['record']['status']=='open'
        assert detail['record']['revision_note']==payload['note']
        assert detail['record']['reusable_checklist']==[payload['check']]
        assert detail['record']['evidence']==created['record']['evidence']
        assert detail['evidence_verification'][0]['verified']
        assert client.post('/api/incidents',json={'summary':'Invalid evidence','evidence':['runs/../.runtime/secrets/session_token']},headers=headers).status_code==400
