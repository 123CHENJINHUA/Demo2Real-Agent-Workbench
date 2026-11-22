from pathlib import Path
import copy,shutil
import pytest
from fastapi.testclient import TestClient
from backend.api import create_app
from backend.store import Store
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    import modules.task_specs as task_specs
    for name in ('config','knowledge'):shutil.copytree(ROOT/name,tmp_path/name)
    app=create_app(tmp_path,start_scheduler=False);store=Store(tmp_path/'.runtime/workbench.sqlite3');source=store.create_run('synthetic source',{},[]);spec={'revision':3,'content_hash':'a'*64,'profile_id':'connector_keyed_v1'}
    def pin(*args,**kwargs):
        if kwargs.get('expected_revision')!=spec['revision']:
            from contracts.tasks import SpecConflict
            raise SpecConflict('stale revision')
        return {'spec':copy.deepcopy(spec),'spec_path':'runs/synthetic/task_spec.json'}
    monkeypatch.setattr(task_specs,'pin',pin)
    scene=store.create_run('explicit synthetic scene fixture',{'source_run_id':source['id']},[{'id':'scene_build','role':'synthetic build fixture'},{'id':'scene_validation','role':'synthetic validation fixture'}])
    for task in scene['tasks']:
        result={'task_spec_hash':spec['content_hash'],'spec_revision':3}
        if task['stage']=='scene_build':result['manifest_path']='runs/synthetic/manifest.json'
        store.transition(task['id'],'succeeded',result=result)
    store.execute('UPDATE runs SET status=? WHERE id=?',('succeeded',scene['id']))
    with TestClient(app) as client:
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        yield client,headers,store,spec,scene

def test_expert_binds_only_checked_scene_and_explicit_reference(fixture):
    client,headers,store,spec,scene=fixture
    response=client.post('/api/runs/'+scene['id']+'/run-expert',json={'expected_revision':3,'spec_hash':'a'*64},headers=headers)
    assert response.status_code==200,response.text
    result=response.json();assert result['input']['expert_run'] is True
    assert result['output_directory']==f"runs/{scene['input']['source_run_id']}/derived/{result['id']}"
    assert result['input']['scene_run_id']==scene['id'];assert result['input']['demo_path']=='data/reference/connector/converted/episode_000.npz'
    assert result['input']['expert_perception']=='simulator_ground_truth';assert result['input']['hardware_commands_sent']==0
    assert [t['stage'] for t in result['tasks']]==['experience','scene_validation','expert_plan','expert_execution','expert_validation']

@pytest.mark.parametrize('payload,code',[
 ({},428),({'expected_revision':2,'spec_hash':'a'*64},409),
 ({'expected_revision':3,'spec_hash':'b'*64},409),
 ({'expected_revision':3,'spec_hash':'a'*64,'hardware':True},400),
 ({'expected_revision':3,'spec_hash':'a'*64,'demo_path':'/untrusted'},400)])
def test_expert_rejects_unknown_scope_and_stale_bindings(fixture,payload,code):
    client,headers,store,spec,scene=fixture
    assert client.post('/api/runs/'+scene['id']+'/run-expert',json=payload,headers=headers).status_code==code

def test_expert_rejects_non_connector_and_failed_scene(fixture):
    client,headers,store,spec,scene=fixture;url='/api/runs/'+scene['id']+'/run-expert';data={'expected_revision':3,'spec_hash':'a'*64}
    spec['profile_id']='rigid_pick_place_v1';assert client.post(url,json=data,headers=headers).status_code==409
    spec['profile_id']='connector_keyed_v1';store.execute('UPDATE runs SET status=? WHERE id=?',('failed',scene['id']));assert client.post(url,json=data,headers=headers).status_code==409
