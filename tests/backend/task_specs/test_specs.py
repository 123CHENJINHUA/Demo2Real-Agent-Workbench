import copy, hashlib, json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.api import create_app
from contracts.tasks import SpecConflict,SpecPrecondition,canonical_hash
from modules.task_specs import generate,get,save,freeze,pin,load_snapshot
from orchestration.workflow import STAGES

def write(root,path,value):
    target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value) if isinstance(value,(dict,list)) else value)
    return str(target.relative_to(root))
def sha(root,path):return hashlib.sha256((root/path).read_bytes()).hexdigest()

@pytest.fixture
def fixture(tmp_path):
    root=tmp_path/'project';root.mkdir();task='task_example'
    write(root,'config/environments.yaml','schema_version: 1\nenvironments: {}\n')
    mesh=write(root,'assets/part.obj','v 0 0 0\nv .01 0 0\nv 0 .01 0\nf 1 2 3\n')
    frame=write(root,'runs/'+task+'/data_audit/frame_000.jpg','explicit test-only frame bytes')
    manifest=write(root,'runs/'+task+'/demo_import/manifest.json',{'files':[]})
    proposal=write(root,'runs/'+task+'/task_analysis/task_proposal.json',{'proposal':{'task_summary':'fixture manipulation hypothesis','objects':[{'name':'part'}],'phases':[{'name':'approach','evidence_refs':[frame]}],'success_conditions':['action criterion to review'],'unknowns':['metric depth unverified']}})
    geometry=write(root,'runs/'+task+'/geometry/geometry_audit.json',{'asset_selection_scope':'explicit_task_assets','assets':[{'path':mesh,'sha256':sha(root,mesh),'status':'audited'}]})
    profile={'id':'fixture_profile','title':'Explicit engineering fixture, not a task success','asset_signatures':{mesh:sha(root,mesh)},'defaults':{'robot':{'asset_path':None},'objects':[{'id':'part','asset_path':mesh,'geometry_type':'mesh','pose':{'position_m':[0,0,.81],'quaternion_wxyz':[1,0,0,0]},'mass_kg':.05,'collision':{'type':'convex_hull','measured':False}}],'scene':{'table':{'size_m':[1,1,.05],'position_m':[0,0,.75]}},'frames':[],'evaluation':{'action':{'criteria':['release'],'verified':False},'retention':{'required':True,'verified':False}},'assumptions':['Explicit unmeasured engineering defaults']}}
    write(root,'tasks/fixture/profile.json',profile)
    deps={'demo_import':{'manifest_path':manifest},'task_analysis':{'report_path':proposal},'geometry':{'report_path':geometry}}
    return root,task,deps,profile

def test_unbound_unknowns_and_explicit_profile_provenance(fixture):
    root,task,deps,profile=fixture
    draft=generate(root,task,dependencies={k:v for k,v in deps.items() if k!='geometry'})
    assert draft['spec']['profile_id'] is None
    assert draft['spec']['objects'][0]['pose']['position_m'] is None
    assert draft['spec']['unresolved'] and draft['spec']['contact_execution_authorized'] is False
    selected=generate(root,task,dependencies=deps,profile_id=profile['id'],expected_revision=1)
    spec=selected['spec'];assert spec['revision']==2 and spec['profile_assignment']['method']=='explicit_profile_selection'
    assert spec['field_provenance']['/objects/0/mass_kg']['validation_scope']=='registered_profile_assumption_not_demo_reconstruction'
    assert spec['evaluation']['action']!=spec['evaluation']['retention']
    assert spec['content_hash']==canonical_hash(dict(reversed(list(spec.items()))))
    assert spec['dependency_hashes']['assets']['assets/part.obj']==sha(root,'assets/part.obj')

def test_unique_full_mesh_signature_can_bind_only_template_draft(fixture):
    root,task,deps,profile=fixture
    matched=generate(root,task,dependencies=deps)
    assert matched['spec']['profile_id']=='fixture_profile'
    assert matched['spec']['profile_assignment']['method']=='hash_matched_template_draft'
    assert matched['spec']['state']=='draft' and not matched['spec']['contact_execution_authorized']
    assert any('template draft only' in text for text in matched['spec']['assumptions'])
    alternate=copy.deepcopy(profile);alternate['id']='other_profile';write(root,'tasks/other/profile.json',alternate)
    ambiguous=generate(root,'other_task',dependencies=deps)
    assert ambiguous['spec']['profile_id'] is None and len(ambiguous['spec']['profile_candidates'])==2

def test_append_only_etag_conflicts_freeze_and_edit_invalidate(fixture):
    root,task,deps,profile=fixture
    first=generate(root,task,dependencies=deps,profile_id=profile['id']);old=(root/first['spec_path']).read_bytes()
    edited=copy.deepcopy(first['spec']);edited['objects'][0]['pose']['position_m'][0]=.1
    second=save(root,task,edited,expected_revision=1,etag=first['etag'])
    assert second['spec']['state']=='draft' and second['spec']['content_hash']!=first['spec']['content_hash']
    assert second['spec']['field_provenance']['/objects/0/pose/position_m/0']['source']=='user_edit'
    assert (root/first['spec_path']).read_bytes()==old
    with pytest.raises(SpecConflict):save(root,task,edited,expected_revision=1)
    with pytest.raises(SpecPrecondition):save(root,task,edited)
    with pytest.raises(ValueError):freeze(root,task,expected_revision=2,scope='hardware')
    frozen=freeze(root,task,expected_revision=2,scope='offline_contract')
    assert frozen['spec']['state']=='frozen' and frozen['spec']['revision']==3
    assert frozen['spec']['content_hash']==second['spec']['content_hash']
    snapshot=root/frozen['frozen_snapshot_path'];original=snapshot.read_bytes()
    assert freeze(root,task,expected_revision=3,scope='offline_contract')['frozen_snapshot_path']==frozen['frozen_snapshot_path']
    edited=copy.deepcopy(frozen['spec']);edited['objects'][0]['mass_kg']=.06
    fourth=save(root,task,edited,expected_revision=3)
    assert fourth['spec']['revision']==4 and fourth['spec']['state']=='draft'
    assert fourth['spec']['validation']['physical']=='not_verified' and snapshot.read_bytes()==original
    assert load_snapshot(root,first['spec_path'],first['spec']['content_hash'])['revision']==1

def test_asset_changes_missing_evidence_and_validation_claims_are_rejected(fixture):
    root,task,deps,profile=fixture
    first=generate(root,task,dependencies=deps,profile_id=profile['id'])
    bad=copy.deepcopy(first['spec']);bad['objects'][0]['pose']['quaternion_wxyz']=[0,0,0,0]
    with pytest.raises(ValueError):save(root,task,bad,expected_revision=1)
    bad=copy.deepcopy(first['spec']);bad['evaluation']['action']['verified']=True
    with pytest.raises(ValueError):save(root,task,bad,expected_revision=1)
    bad=copy.deepcopy(first['spec']);bad['phases'][0]['evidence_refs']=['missing/frame.jpg']
    with pytest.raises(ValueError):save(root,task,bad,expected_revision=1)
    bad=copy.deepcopy(first['spec']);bad['objects'][0]['collision']=[]
    with pytest.raises(ValueError):save(root,task,bad,expected_revision=1)
    bad=copy.deepcopy(first['spec']);bad['robot']['arms']=[{'id':'left','base_pose':{'position_m':[0,0,0],'quaternion_wxyz':[0,0,0,0]}}]
    with pytest.raises(ValueError):save(root,task,bad,expected_revision=1)
    (root/'assets/part.obj').write_text('changed after audit')
    with pytest.raises(ValueError,match='changed'):freeze(root,task,expected_revision=1,scope='offline_contract')
    assert get(root,task)['spec']['revision']==1

def test_concurrent_edits_allow_exactly_one_winner(fixture):
    root,task,deps,profile=fixture;first=generate(root,task,dependencies=deps,profile_id=profile['id'])
    def edit(value):
        spec=copy.deepcopy(first['spec']);spec['objects'][0]['mass_kg']=value
        try:return save(root,task,spec,expected_revision=1)['spec']['revision']
        except SpecConflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(edit,[.08,.09]))
    assert results.count(2)==1 and results.count('conflict')==1

def test_api_historical_proposal_no_model_recall_and_pinned_scene_jobs(fixture,monkeypatch):
    root,task,deps,profile=fixture
    monkeypatch.setattr('orchestration.providers.compatible.Provider.request',lambda *args,**kwargs:pytest.fail('task spec generation must not repeat LLM inference'))
    with TestClient(create_app(root,start_scheduler=False)) as client:
        # Seed a historical run with existing evidence, exactly as a completed analysis does.
        store=client.app.state.store;run=store.create_run('historical',{},STAGES);rid=run['id']
        for stage,result in deps.items():store.transition(rid+':'+stage,'succeeded',result=result)
        headers={'X-Session-Token':client.get('/api/session').json()['token']}
        assert client.get('/api/task-profiles').json()['profiles'][0]['id']==profile['id']
        assert client.get('/api/runs/'+rid+'/task-spec').status_code==404
        first_response=client.post('/api/runs/'+rid+'/task-spec',json={'profile_id':profile['id']},headers=headers)
        assert first_response.status_code==200,first_response.text
        first=first_response.json();assert first_response.headers['etag']==first['etag']
        assert client.post('/api/runs/'+rid+'/task-spec',json={'profile_id':profile['id']},headers=headers).status_code==428
        draft=copy.deepcopy(first['spec']);draft['objects'][0]['mass_kg']=.12
        second=client.put('/api/runs/'+rid+'/task-spec',json={'spec':draft,'expected_revision':1},headers=headers|{'If-Match':first['etag']}).json()
        assert second['spec']['revision']==2
        assert client.put('/api/runs/'+rid+'/task-spec',json={'spec':draft,'expected_revision':1},headers=headers).status_code==409
        freeze_response=client.post('/api/runs/'+rid+'/task-spec/freeze',json={'expected_revision':2,'scope':'offline_contract'},headers=headers)
        assert freeze_response.status_code==200
        frozen=freeze_response.json();assert frozen['spec']['state']=='frozen'
        build=client.post('/api/runs/'+rid+'/build-scene',json={'expected_revision':3,'preview':True},headers=headers).json()
        assert [task['stage'] for task in build['tasks']]==['experience','functional_geometry','scene_build','scene_validation']
        assert build['input']['spec_path']==frozen['spec_path'] and build['input']['contact_execution_authorized'] is False
        assert load_snapshot(root,build['input']['spec_path'])['revision']==3
        store.transition(build['id']+':scene_build','succeeded',result={'task_spec_hash':'0'*64,'spec_revision':3})
        assert client.get('/api/runs/'+rid+'/task-spec').json()['scene_builds'][0]['stale'] is True
        store.transition(build['id']+':scene_build','succeeded',result={'task_spec_hash':frozen['spec']['content_hash'],'spec_revision':3})
        changed=copy.deepcopy(frozen['spec']);changed['objects'][0]['mass_kg']=.13
        assert client.put('/api/runs/'+rid+'/task-spec',json={'spec':changed,'expected_revision':3},headers=headers).status_code==200
        state=client.get('/api/runs/'+rid+'/task-spec').json()
        assert state['scene_builds'][0]['run_id']==build['id'] and state['scene_builds'][0]['stale']
        assert client.post('/api/runs/'+rid+'/build-scene',json={'expected_revision':4,'preview':False},headers=headers).status_code==400
        artifacts=store.get_run(rid)['artifacts'];assert artifacts
        assert client.get('/api/artifacts/'+artifacts[0]['id']).status_code==200

def test_contact_expert_not_triggered_by_scene_preview(fixture):
    from application.registry import execute_stage
    root,task,deps,profile=fixture
    result=execute_stage('simulation',{'root':root,'run_id':task,'input':{},'output_dir':root/'runs'/task/'simulation','dependencies':{'task_spec':{'spec_path':'pinned revision'}},'check_cancel':lambda:None})
    assert result['status']=='needs_configuration' and result['contact_execution_authorized'] is False


def test_shared_asset_inventory_does_not_bind_an_unrelated_demonstration(fixture):
    root,task,deps,profile=fixture
    report=root/deps['geometry']['report_path'];audit=json.loads(report.read_text());audit['asset_selection_scope']='project_inventory_unbound';report.write_text(json.dumps(audit))
    result=generate(root,task,dependencies=deps)
    assert result['spec']['profile_id'] is None
    assert result['spec']['profile_assignment']['method']=='unbound'
    assert len(result['spec']['profile_candidates'])==1
    assert result['spec']['profile_candidates'][0]['automatic_selection'] is False


def test_task_references_and_revision_directories_cannot_escape_project(fixture):
    root,task,deps,profile=fixture
    outside=root.parent/'outside.obj';outside.write_text('outside fixture project')
    bad=copy.deepcopy(profile);bad['defaults']['objects'][0]['asset_path']=str(outside)
    write(root,'tasks/fixture/profile.json',bad)
    with pytest.raises(ValueError,match='within project'):generate(root,task,dependencies=deps,profile_id=bad['id'])
    destination=root.parent/'not_project';destination.mkdir()
    parent=root/'runs/escaped';parent.mkdir()
    (parent/'task_spec').symlink_to(destination,target_is_directory=True)
    with pytest.raises(ValueError,match='symlinks'):generate(root,'escaped',dependencies={})
    assert list(destination.iterdir())==[]


def test_frames_form_rigid_acyclic_graph_and_unknowns_remain_explicit(fixture):
    from contracts.tasks import validate_task_spec
    root,task,deps,profile=fixture
    first=generate(root,task,dependencies=deps,profile_id=profile['id'])
    identity=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
    valid=copy.deepcopy(first['spec']);valid['frames']=[{'id':'tip','parent':'part','transform':identity},{'id':'axis','parent':'tip','transform':[[0,-1,0,0],[1,0,0,0],[0,0,1,0],[0,0,0,1]]}]
    assert validate_task_spec(valid) is valid
    changes=[]
    duplicate=copy.deepcopy(valid);duplicate['frames'].append(copy.deepcopy(duplicate['frames'][0]));changes.append(duplicate)
    cycle=copy.deepcopy(valid);cycle['frames'][0]['parent']='axis';changes.append(cycle)
    missing=copy.deepcopy(valid);missing['frames'][0]['parent']='unbound_name';changes.append(missing)
    shear=copy.deepcopy(valid);shear['frames'][0]['transform'][0][1]=.1;changes.append(shear)
    reflection=copy.deepcopy(valid);reflection['frames'][0]['transform'][0][0]=-1;changes.append(reflection)
    ambiguous=copy.deepcopy(valid);ambiguous['frames'][0]['id']='part';changes.append(ambiguous)
    for bad in changes:
        with pytest.raises(ValueError):validate_task_spec(bad)
    unknown=copy.deepcopy(valid);unknown['frames'][0].update(parent=None,transform=None)
    saved=save(root,task,unknown,expected_revision=1)
    assert saved['spec']['frames'][0]['parent'] is None and saved['spec']['frames'][0]['transform'] is None
    assert any('frames.tip.parent is unknown' in value for value in saved['spec']['unresolved'])
    frozen=freeze(root,task,expected_revision=2,scope='offline_contract')
    assert frozen['spec']['content_hash']==saved['spec']['content_hash']
    assert frozen['spec']['contact_execution_authorized'] is False


def test_primitive_dimensions_reject_bad_values_and_do_not_invent_unknowns(fixture):
    from contracts.tasks import validate_task_spec
    root,task,deps,profile=fixture
    first=generate(root,task,dependencies=deps,profile_id=profile['id'])
    box=copy.deepcopy(first['spec']);box['objects'][0].update(asset_path=None,geometry_type='box',size_m=[.04,.03,.02],collision={'type':'box'})
    assert validate_task_spec(box) is box
    for bad_size in ([0,.03,.02],[-.04,.03,.02],[True,.03,.02],[float('nan'),.03,.02]):
        bad=copy.deepcopy(box);bad['objects'][0]['size_m']=bad_size
        with pytest.raises(ValueError):validate_task_spec(bad)
    for name in ('two-parts','12part','part/slash','part with space'):
        bad=copy.deepcopy(box);bad['objects'][0]['id']=name
        with pytest.raises(ValueError,match='USD-safe'):validate_task_spec(bad)
    cylinder=copy.deepcopy(first['spec']);cylinder['objects'][0].update(asset_path=None,geometry_type='cylinder',radius_m=None,height_m=None,collision={'type':'cylinder'})
    saved=save(root,task,cylinder,expected_revision=1)
    assert saved['spec']['objects'][0]['radius_m'] is None
    assert any('radius_m is unknown' in v for v in saved['spec']['unresolved'])
    for field in ('radius_m','height_m'):
        for value in (-1,0,False,float('inf')):
            bad=copy.deepcopy(cylinder);bad['objects'][0][field]=value
            with pytest.raises(ValueError):validate_task_spec(bad)
    known=copy.deepcopy(saved['spec']);known['objects'][0].update(radius_m=.02,height_m=.05)
    resolved=save(root,task,known,expected_revision=2)
    assert not any(v.startswith('schema_unknown: objects.part.') for v in resolved['spec']['unresolved'])
    unknown_box=copy.deepcopy(box);unknown_box['objects'][0]['size_m']=None
    unknown=save(root,task,unknown_box,expected_revision=3)
    assert unknown['spec']['objects'][0]['size_m'] is None
    assert any('size_m is unknown' in v for v in unknown['spec']['unresolved'])


def test_profile_fixture_provenance_and_unresolved_are_retained(fixture):
    root,task,deps,profile=fixture
    profile.update(synthetic_test_fixture=True,unresolved=['Root profile dimension remains unmeasured'])
    profile['defaults']['unresolved']=['Default camera mounting remains unverified']
    write(root,'tasks/fixture/profile.json',profile)
    result=generate(root,task,dependencies=deps,profile_id=profile['id'])
    assert result['spec']['source']['synthetic_test_fixture'] is True
    assert 'Root profile dimension remains unmeasured' in result['spec']['unresolved']
    assert 'Default camera mounting remains unverified' in result['spec']['unresolved']
    assert result['spec']['contact_execution_authorized'] is False and result['spec']['hardware_verified'] is False
    original=(root/result['spec_path']).read_bytes()
    bad=copy.deepcopy(profile);bad['synthetic_test_fixture']='false';write(root,'tasks/fixture/profile.json',bad)
    with pytest.raises(ValueError,match='explicit boolean'):generate(root,'invalid_fixture',dependencies=deps,profile_id=profile['id'])
    assert (root/result['spec_path']).read_bytes()==original
