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

def test_api_session_origin_and_robot_gates(project):
    with TestClient(create_app(project,start_scheduler=False)) as client:
        assert client.get('/api/health').json()['hardware_commands_enabled'] is False
        token=client.get('/api/session').json()['token'];headers={'X-Session-Token':token}
        assert client.post('/api/robots/enable',json={}).status_code==403
        assert client.post('/api/robots/enable',json={},headers=headers).status_code==403
        assert client.get('/api/session',headers={'Origin':'https://evil.example'}).status_code==403
        assert client.put('/api/settings/provider',json={'model':'x'},headers=headers).status_code==200
        assert client.get('/api/settings/provider').json()['configured'] is False
        assert client.post('/api/settings/provider/test',json={},headers=headers).json()['status']=='needs_configuration'
        assert client.get('/api/fs',params={'path':'/etc'}).status_code==403
        source=project/'input';source.mkdir();(source/'metadata.json').write_text('{}')
        run=client.post('/api/runs',json={'source_path':str(source),'name':'真实离线导入'},headers=headers).json()
        assert len(run['tasks'])==len(STAGES) and run['status']=='queued'
        assert client.post('/api/runs/'+run['id']+'/cancel',json={},headers=headers).json()['status']=='cancelled'
        assert client.post('/api/runs/'+run['id']+'/retry',json={},headers=headers).json()['status']=='queued'
        events=client.get('/api/runs/'+run['id']+'/events').json()
        assert events['cursor']>0 and any(e['type']=='run.created' for e in events['events'])
        case=client.post('/api/incidents',json={'summary':'测试离线输入缺失'},headers=headers)
        assert case.status_code==200 and case.json()['record']['hardware_verified'] is False

def test_store_restart_marks_running_interrupted(project):
    store=Store(project/'.runtime/workbench.sqlite3');run=store.create_run('recover',{},STAGES)
    store.transition(run['tasks'][0]['id'],'running',pid=123)
    store.execute("UPDATE runs SET status='running' WHERE id=?",(run['id'],))
    store.recover();detail=store.get_run(run['id'])
    assert detail['status']=='interrupted' and detail['tasks'][0]['status']=='interrupted'
    assert detail['tasks'][0]['pid'] is None

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
