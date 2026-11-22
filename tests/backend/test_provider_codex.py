"""Fake CLI tests validate transport and isolation, never model/task accuracy."""
import json, os, shutil, sys, time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.api import create_app
from orchestration.providers.compatible import Provider
from orchestration.providers.codex_cli import CodexCliProvider, DISABLED_FEATURES, TEST_SCHEMA

FAKE = r'''import json, os, signal, subprocess, sys, time
from pathlib import Path
args=sys.argv[1:];home=Path(os.environ['CODEX_HOME']);home.mkdir(parents=True,exist_ok=True)
root=home.parent.parent
with (home.parent/'fake_invocations.jsonl').open('a') as log:
 log.write(json.dumps({'args':args,'home':os.environ.get('HOME'),'codex_home':str(home),'sqlite_home':os.environ.get('CODEX_SQLITE_HOME'),'has_api_key':'OPENAI_API_KEY' in os.environ,'cwd':os.getcwd()})+'\n')
if '--version' in args:
 print('codex-cli 0.166.0-test-fixture');sys.exit(0)
if 'login' in args and 'status' in args:
 logged=(home/'auth.json').is_file() if os.environ.get('WORKBENCH_FAKE_DEVICE_MODE') else os.environ.get('WORKBENCH_FAKE_CODEX_AUTH','1')=='1'
 print('Logged in using ChatGPT' if logged else 'Not logged in');sys.exit(0 if logged else 1)
if 'login' in args and '--device-auth' in args:
 print('Visit https://auth.openai.com/codex/device and enter AAAA-BBBB',flush=True)
 while not (Path.cwd()/'fixture_authorized').exists():time.sleep(.02)
 (home/'auth.json').write_text('fictional new account credential')
 sys.exit(1 if os.environ.get('WORKBENCH_FAKE_DEVICE_MODE')=='fail' else 0)
mode=os.environ.get('WORKBENCH_FAKE_CODEX_MODE','success')
print(json.dumps({'type':'thread.started','thread_id':'fixture-only','model':'fixture-reported-model'}),flush=True)
if mode=='hang':
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
 (home.parent/'fake_child.pid').write_text(str(child.pid))
 def stop(sig,frame):
  try:child.wait(timeout=2)
  except subprocess.TimeoutExpired:child.kill();child.wait()
  sys.exit(143)
 signal.signal(signal.SIGTERM,stop)
 time.sleep(60)
if mode=='nonzero':
 print(json.dumps({'type':'error','message':'fixture error'}),flush=True);sys.exit(2)
if mode in ('tool','code_execution'):
 print(json.dumps({'type':'item.started','item':{'type':'code_execution' if mode=='code_execution' else 'command_execution','command':'fixture never executed'}}),flush=True);time.sleep(2);sys.exit(2)
prompt=sys.stdin.read();schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
value={'ok':True}
if 'assignments' in schema.get('properties',{}):
 slots=schema['properties']['assignments']
 count=slots['minItems'] if slots['minItems']==slots['maxItems'] else 3
 roles=['agent_intent','agent_validation','agent_geometry','agent_observation']
 value={'assignments':[{'role':role,'focus':'Fixture task focus','reason':'Fixture scope'} for role in roles[:count]]}
 if 'understanding' in schema['properties']:
  value['understanding']={'task_summary':'Fixture intent','phases':[{'name':'Fixture transfer','evidence_refs':[]}]}
if 'findings' in schema.get('properties',{}):
 if mode=='specialist_failure' and '"role": "agent_validation"' in prompt:
  print(json.dumps({'type':'error','message':'fixture specialist failed'}),flush=True);sys.exit(2)
 refs=schema['properties']['findings']['items']['properties']['evidence_refs']['items']['enum']
 value={'summary':'Fixture specialist report','findings':[{'observation':'fixture evidence','evidence_refs':[refs[0]]}],'unknowns':[],'checks':['Independent validation required']}
if 'task_summary' in schema.get('properties',{}):
 refs=[line.split('Evidence reference: ',1)[1].strip() for line in prompt.splitlines() if 'Evidence reference: ' in line]
 value={'task_summary':'fixture proposal, no actual model analysis','objects':[],'phases':[{'name':'fixture phase','evidence_refs':[refs[0]] if refs else [],'start_time_s':None,'end_time_s':None}],'success_conditions':['independent validation required'],'unknowns':['fixture only'],'required_assets':[],'confidence':0.1}
 if mode=='bad_reference':value['phases'][0]['evidence_refs']=['outside/unprovided.jpg']
raw='not json' if mode=='malformed' else json.dumps(value)
Path(args[args.index('-o')+1]).write_text(raw)
print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'Observable fixture summary, not hidden reasoning.'}}),flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':raw}}),flush=True)
if mode=='failed_final':print(json.dumps({'type':'turn.failed','error':{'message':'fixture turn failure despite final JSON'}}),flush=True)
if mode=='error_final':print(json.dumps({'type':'error','message':'fixture error despite final JSON'}),flush=True)
if mode!='missing_completed':print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':3}}),flush=True)
'''

@pytest.fixture
def cli_project(tmp_path,monkeypatch):
    root=tmp_path/'project';(root/'config').mkdir(parents=True)
    executable=root/'fake_codex'
    executable.write_text('#!'+sys.executable+'\n'+FAKE);executable.chmod(0o700)
    (root/'config/environments.yaml').write_text('schema_version: 1\nenvironments:\n  codex_executable: '+json.dumps(str(executable))+'\n')
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_AUTH','1')
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_MODE','success')
    return root

def calls(root):
    path=root/'.runtime/fake_invocations.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize('action',['authorize','fail','cancel'])
def test_switch_account_only_commits_a_successful_official_login(cli_project,monkeypatch,action):
    from modules.model_accounts.codex_login import CodexLoginManager

    monkeypatch.setenv('WORKBENCH_FAKE_DEVICE_MODE',action)
    manager=CodexLoginManager(cli_project)
    active=cli_project/'.runtime/codex-home/auth.json'
    active.parent.mkdir(parents=True)
    active.write_text('fictional original account credential')
    assert manager.start()['status']=='authenticated'
    try:
        state=manager.start(switch_account=True)
        for _ in range(150):
            if state.get('user_code'):
                break
            time.sleep(.02)
            state=manager.status()
        assert state['status']=='pending' and state['verification_url']=='https://auth.openai.com/codex/device'
        assert state['user_code']=='AAAA-BBBB' and state['login_command'] is None
        assert active.read_text()=='fictional original account credential'
        if action=='cancel':
            ended=manager.cancel()
        else:
            (cli_project/'fixture_authorized').touch()
            manager.process.wait(timeout=4)
            ended=manager.status()
        expected={'authorize':'authenticated','fail':'failed','cancel':'cancelled'}[action]
        assert ended['status']==expected and ended['authenticated']
        assert active.read_text()==f'fictional {"new" if action=="authorize" else "original"} account credential'
        if action=='authorize':
            assert active.stat().st_mode&0o777==0o600
        assert manager.status()==ended
        assert list((cli_project/'.runtime/codex-auth').glob('switch-*'))==[]
    finally:
        manager.close()

def test_codex_facade_does_not_require_api_key_and_isolates_runtime(cli_project,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','must-not-be-inherited')
    provider=Provider(cli_project)
    state=provider.save({'kind':'codex_cli','model':'','timeout':8})
    assert state['configured'] is True and state['requires_api_key'] is False
    assert state['codex']['auth_home']==str(cli_project/'.runtime/codex-home')
    emitted=[]
    result=provider.request([{'role':'user','content':'Return JSON ok'}],schema=TEST_SCHEMA,emit=lambda k,p:emitted.append((k,p)))
    assert result['status']=='succeeded' and json.loads(result['content'])=={'ok':True}
    assert result['model']=='fixture-reported-model' and result['model_source']=='cli_reported'
    assert result['usage']['input_tokens']==10
    assert any(kind=='model.codex_event' for kind,_ in emitted)
    reasoning=[payload for kind,payload in emitted if payload.get('item_type')=='reasoning']
    assert reasoning and all('text' not in payload for payload in reasoning)
    execution=next(c for c in calls(cli_project) if 'exec' in c['args'])
    args=execution['args']
    assert execution['home']==os.environ.get('HOME')
    assert execution['has_api_key'] is False
    assert execution['codex_home']==str(cli_project/'.runtime/codex-home')
    assert execution['sqlite_home']==str(cli_project/'.runtime/codex-sqlite')
    assert '--ignore-user-config' in args and '--ephemeral' in args and '--ignore-rules' not in args
    assert not any(arg.startswith('model_reasoning_effort=') for arg in args)
    assert args[args.index('--sandbox')+1]=='read-only' and args[args.index('-a')+1]=='never'
    assert '--dangerously-bypass-approvals-and-sandbox' not in args
    assert args[-2:]==['--','-']
    assert all(args[args.index('--disable',i)+1] for i in [args.index('--disable')])
    disabled={args[i+1] for i,value in enumerate(args) if value=='--disable'}
    assert set(DISABLED_FEATURES)<=disabled
    paths=[cli_project/a['path'] for a in result['artifacts']]
    assert any(p.name=='events.jsonl' and 'turn.completed' in p.read_text() for p in paths)

def test_codex_needs_project_login_never_falls_back_to_inherited_key(cli_project,monkeypatch):
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_AUTH','0');monkeypatch.setenv('OPENAI_API_KEY','global-key-not-allowed')
    provider=Provider(cli_project);state=provider.save({'kind':'codex_cli'})
    assert state['configured'] is False
    assert provider.request([{'role':'user','content':'test'}])['status']=='needs_configuration'
    assert not any('exec' in c['args'] for c in calls(cli_project))

@pytest.mark.parametrize('mode',['nonzero','malformed','tool','code_execution','failed_final','error_final','missing_completed'])
def test_codex_errors_are_real_failures_with_artifacts(cli_project,monkeypatch,mode):
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_MODE',mode)
    result=CodexCliProvider(cli_project,{'timeout':5}).request([{'role':'user','content':'test'}])
    assert result['status']=='failed' and result['artifacts']
    assert all((cli_project/a['path']).is_file() for a in result['artifacts'])

def test_codex_timeout_and_cancel_reap_process_group(cli_project,monkeypatch):
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_MODE','hang')
    result=CodexCliProvider(cli_project,{'timeout':.4}).request([{'role':'user','content':'test'}])
    assert result['status']=='failed' and '超时' in result['error']
    child=int((cli_project/'.runtime/fake_child.pid').read_text())
    with pytest.raises(ProcessLookupError):os.kill(child,0)
    started=time.monotonic()
    def cancel():
        if time.monotonic()-started>.4:raise InterruptedError('fixture cancellation')
    with pytest.raises(InterruptedError):CodexCliProvider(cli_project,{'timeout':5}).request([{'role':'user','content':'test'}],check_cancel=cancel)
    child=int((cli_project/'.runtime/fake_child.pid').read_text())
    with pytest.raises(ProcessLookupError):os.kill(child,0)

def test_codex_multimodal_stage_schema_and_reference_rejection(cli_project,monkeypatch):
    from application.registry import execute_stage
    root=cli_project;out=root/'runs/fixture/task_analysis';out.mkdir(parents=True)
    frame=root/'runs/fixture/data_audit/front/frame_000.jpg';frame.parent.mkdir(parents=True);frame.write_bytes(b'fixture-image-only-not-used-for-accuracy')
    Provider(root).save({'kind':'codex_cli','timeout':5})
    refs=str(frame.relative_to(root));video='videos/observation.images.front/chunk-000/file-000.mp4'
    dependencies={'demo_import':{'metadata':{'large_unrelated_metadata':'x'*30000}},'data_audit':{'sample_frames':[{'path':refs,'source_video':video,'requested_timestamp_seconds':13.25,'timestamp_precision':'requested seek only'}],'videos':[{'path':video,'sampled_interval_seconds':[10,28.5]}]}}
    ctx={'root':root,'run_id':'fixture','output_dir':out,'input':{},'dependencies':dependencies,'emit':lambda *args:None,'check_cancel':lambda:None}
    result=execute_stage('task_analysis',ctx)
    assert result['status']=='succeeded' and result['proposal']['hardware_verified'] is False
    execution=next(c for c in calls(root) if 'exec' in c['args'])
    assert str(frame) in execution['args'] and '-i' in execution['args']
    prompt_path=Path(execution['cwd'])/'prompt.txt';prompt=prompt_path.read_text()
    assert 'selected_frame_evidence:' in prompt and '"episode_relative_seconds": 3.25' in prompt
    assert '"requested_timestamp_seconds": 13.25' in prompt
    assert prompt.index('selected_frame_evidence:')<prompt.index('Additional offline evidence')
    assert 'cross-view synchronization are unverified' in prompt and 'does not prove a hidden latch' in prompt
    schema=json.loads(Path(execution['args'][execution['args'].index('--output-schema')+1]).read_text())
    assert schema['additionalProperties'] is False and set(schema['required'])==set(schema['properties'])
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_MODE','bad_reference')
    result=execute_stage('task_analysis',ctx)
    assert result['status']=='failed' and 'JSON契约' in result['error']

def test_codex_provider_api_switch_and_status_require_no_key(cli_project):
    with TestClient(create_app(cli_project,start_scheduler=False)) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        assert client.get('/api/settings/provider').json()['kind']=='openai_compatible'
        configured=client.put('/api/settings/provider',json={'kind':'codex_cli','model':'gpt-6.2-sol','reasoning_effort':'high'},headers=headers)
        assert configured.status_code==200 and configured.json()['configured']
        assert configured.json()['reasoning_effort']=='high'
        status=client.get('/api/settings/provider/codex/status').json()
        assert status['authenticated'] and status['available']
        invalid=client.put('/api/settings/provider',json={'reasoning_effort':['high']},headers=headers)
        assert invalid.status_code==400
        result=client.post('/api/settings/provider/test',json={},headers=headers).json()
        assert result['status']=='succeeded'
        assert result['requested_model']=='gpt-6.2-sol' and result['requested_reasoning_effort']=='high'
        assert result['model']=='fixture-reported-model'
        args=next(call['args'] for call in calls(cli_project) if 'exec' in call['args'])
        assert args[args.index('--model')+1]=='gpt-6.2-sol'
        assert 'model_reasoning_effort="high"' in args
        assert client.put('/api/settings/provider',json={'reasoning_effort':''},headers=headers).json()['reasoning_effort']==''
        assert client.post('/api/settings/provider/codex/login/start',json={}).status_code==403
        assert client.get('/api/settings/provider/codex/login').json()['authenticated']
        assert 'api_key' not in client.get('/api/settings/provider').json()


@pytest.mark.parametrize('count',[None,4])
def test_supervisor_dispatches_registered_roles_and_retries_only_failed_specialist(cli_project,monkeypatch,count):
    from backend.store import Store
    from orchestration.scheduler import Scheduler
    from orchestration.workflow import delegated_stages

    project=Path(__file__).resolve().parents[2]
    for directory in ('application','backend','contracts','modules','orchestration','workers'):
        shutil.copytree(project/directory,cli_project/directory,ignore=shutil.ignore_patterns('__pycache__'))
    Provider(cli_project).save({'kind':'codex_cli','timeout':5})
    store=Store(cli_project/'.runtime/workbench.sqlite3')
    run=store.create_run('Harness transport fixture',{'specialist_count':count} if count is not None else {},[
        {'id':'data_audit','role':'Fixture frames'},
        {'id':'agent_dispatch','role':'Fixture dispatch','kind':'llm','deps':['data_audit']},
        {'id':'task_analysis','role':'Fixture synthesis','kind':'llm','deps':['agent_dispatch']},
    ])
    rid=run['id']
    directory=cli_project/run['output_directory']
    frame=directory/'data_audit/front/frame_000.jpg'
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b'explicit fixture image, no semantic accuracy claim')
    store.transition(rid+':data_audit','succeeded',result={'status':'succeeded'})
    monkeypatch.setenv('WORKBENCH_FAKE_CODEX_MODE','specialist_failure')

    def wait_for_run():
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            result=store.get_run(rid)
            if result['status'] in ('failed','succeeded','blocked'):return result
            time.sleep(.1)
        pytest.fail('Fixture workflow did not finish')

    scheduler=Scheduler(cli_project,store)
    scheduler.start()
    try:
        tasks={task['stage']:task for task in wait_for_run()['tasks']}
        assert tasks['agent_validation']['status']=='failed'
        assert tasks['task_analysis']['status']=='blocked'
        assert not (directory/'task_analysis/model_response.json').exists()
        roles=['agent_intent','agent_validation','agent_geometry']
        if count==4:roles.append('agent_observation')
        assert tasks['task_analysis']['deps']==['agent_dispatch',*roles]
        understanding=tasks['agent_dispatch']['result']['understanding']
        assert understanding['task_summary']=='Fixture intent'
        assert [phase['name'] for phase in understanding['phases']]==['Fixture transfer']
        assert {'agent_intent','agent_validation','agent_geometry','agent_observation'}&tasks.keys()==set(roles)
        with pytest.raises(ValueError,match='本次要求 2 个'):
            delegated_stages(tasks['agent_dispatch']['result'],2)
        invalid=json.loads(json.dumps(tasks['agent_dispatch']['result']))
        invalid['plan']['assignments'][0]['role']='arbitrary_command'
        with pytest.raises(ValueError,match='未注册'):
            delegated_stages(invalid)

        monkeypatch.setenv('WORKBENCH_FAKE_CODEX_MODE','success')
        scheduler.retry(rid)
        completed=wait_for_run()
        assert completed['status']=='succeeded',completed
        tasks={task['stage']:task for task in completed['tasks']}
        assert all(tasks[stage]['attempt']==(2 if stage=='agent_validation' else 1) for stage in ['agent_dispatch',*roles,'task_analysis'])
        artifact_paths={item['path'] for item in completed['artifacts']}
        proposal=json.loads((directory/'task_analysis/task_proposal.json').read_text())
        reports=proposal['analysis_team']['members']
        assert len(reports)==len(roles) and all(member['report_path'] in artifact_paths for member in reports)
        assert all(member['model']=='fixture-reported-model' for member in reports)
        assert proposal['analysis_team']['plan_path'] in artifact_paths
        assert len(store.query("SELECT id FROM events WHERE run_id=? AND type='agents.dispatched'",(rid,)))==1
        assert tasks['agent_dispatch']['progress']=={'phase':'validating_model_output'}
        assert tasks['agent_validation']['progress']=={'phase':'validating_model_output'}
        assert tasks['task_analysis']['progress']=={'phase':'validating_model_output'}
        prompts=[path.read_text() for path in (directory/'task_analysis/codex_cli').glob('*/prompt.txt')]
        assert len(prompts)==1 and 'Specialist reports:' in prompts[0] and 'Fixture specialist report' in prompts[0]
        assert 'Fixture intent' in prompts[0] and 'Fixture transfer' in prompts[0]
    finally:
        scheduler.stop()
