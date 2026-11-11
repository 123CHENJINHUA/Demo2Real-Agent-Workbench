"""CPU boundary tests; all scene/render data here are explicit synthetic fixtures."""
import copy,hashlib,json,struct,zlib
from pathlib import Path
import pytest
from contracts.tasks import canonical_hash
from modules.scene_building.service import checked_spec,run as build_scene
from modules.scene_validation.service import validate_manifest


def write(root,relative,content):
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(content if isinstance(content,bytes) else content.encode())
    return path


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def png_bytes():
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    # Tiny, real, nonconstant RGB PNG; not an Isaac render.
    rows=b'\x00'+bytes([0,0,0,255,0,0,0,255,0])+b'\x00'+bytes([0,0,255,255,255,255,90,40,130])
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',3,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')


@pytest.fixture
def fixture(tmp_path):
    root=tmp_path/'scene_fixture';root.mkdir()
    asset=write(root,'assets/fixture.txt','explicit synthetic geometry declaration')
    evidence=write(root,'evidence/frame.txt','explicit synthetic evidence, no sensor assertion')
    profile=write(root,'tasks/fixture/profile.json','{"id":"fixture","synthetic_test_fixture":true}')
    spec={'schema_version':'task-spec/v1','task_id':'synthetic_scene','revision':1,'state':'draft','profile_id':'fixture',
          'source':{'synthetic_test_fixture':True,'run_id':'synthetic_scene'},'robot':{},
          'objects':[{'id':'block','asset_path':None,'geometry_type':'box','size_m':[.04,.04,.04],'scale':[1,1,1],
                      'pose':{'position_m':[0,0,.82],'quaternion_wxyz':[1,0,0,0]},'mass_kg':.05,'collision':{'type':'box'}}],
          'scene':{'table':{'size_m':[1,1,.05],'position_m':[0,0,.775]}},'frames':[],'phases':[],
          'evaluation':{'action':{'verified':False},'retention':{'verified':False}},
          'assumptions':['CPU validator fixture; no actual physics/render success asserted'],'unresolved':[],
          'dependency_hashes':{'assets':{'assets/fixture.txt':digest(asset)},'evidence':{'evidence/frame.txt':digest(evidence)},
                               'profile':{'path':'tasks/fixture/profile.json','sha256':digest(profile)}},
          'field_provenance':{},'contact_execution_authorized':False,'hardware_verified':False}
    spec['content_hash']=canonical_hash(spec)
    snapshot=write(root,'build/task_spec.json',json.dumps(spec))
    write(root,'build/scene.usda','#usda 1.0\n# synthetic unit fixture, never loaded as actual scene\n')
    write(root,'build/scene.png',png_bytes())
    manifest={'schema_version':'scene-manifest/v1','task_spec_hash':spec['content_hash'],'spec_revision':1,'profile_id':'fixture',
              'spec_snapshot_path':'build/task_spec.json','scene_path':'build/scene.usda','preview_path':'build/scene.png',
              'scope':'scene_and_local_settling_only','contact_execution_authorized':False,'hardware_verified':False,
              'objects':copy.deepcopy(spec['objects']),
              'initial_poses':{'block':copy.deepcopy(spec['objects'][0]['pose'])},'settled_poses':{'block':copy.deepcopy(spec['objects'][0]['pose'])},
              'dynamics':{'block':{'linear_velocity_m_s':[0,0,0],'angular_velocity_rad_s':[0,0,0]}},
              'world_visual_bounds':{'block':{'min':[-.02,-.02,.8],'max':[.02,.02,.84]}},'robots':[],
              'cameras':[{'id':'overview','path':'/World/PreviewCamera','width':3,'height':2,'K':[[1,0,1.5],[0,1,1],[0,0,1]],
                          'rgb_path':'build/scene.png','maximum_projection_error_px':0.,'rgb_std':80.,'valid_depth_fraction':1.,
                          'T_world_camera_optical':[[1,0,0,0],[0,1,0,0],[0,0,1,1],[0,0,0,1]]}],
              'connector_pair_joints':[],'hardware_commands_sent':0,'robot_motion_controller_loaded':False,'full_task_executed':False}
    audit={'status':'passed','outside':[],'missing':[],'rigid_bodies':['/World/Objects/block'],'physics_scenes':['/World/physicsScene'],
           'cameras':['/World/PreviewCamera'],'layers':[str(root/'build/scene.usda')]}
    return root,spec,snapshot,manifest,audit


def persist(snapshot,spec):
    spec['content_hash']=canonical_hash(spec);snapshot.write_text(json.dumps(spec))


def rejected_or_failed(call):
    try:value=call()
    except ValueError:return
    assert value['status']=='failed',value


def test_preview_fixture_never_upgrades_action_or_hardware_scope(fixture):
    root,spec,snapshot,manifest,audit=fixture
    report=validate_manifest(root,manifest,audit)
    assert report['status']=='succeeded'
    assert report['full_task_evaluated'] is False
    assert report['action_evaluation']=='not_executed' and report['retention_evaluation']=='not_executed'
    assert report['hardware_verified'] is False and report['contact_execution_authorized'] is False
    _,loaded,missing=checked_spec(root,snapshot.relative_to(root))
    assert not missing and loaded['state']=='draft' and loaded['contact_execution_authorized'] is False


@pytest.mark.parametrize('reference',['assets/fixture.txt','evidence/frame.txt','tasks/fixture/profile.json'])
def test_builder_invalidates_every_bound_dependency(fixture,reference):
    root,spec,snapshot,manifest,audit=fixture
    (root/reference).write_text('mutated after task definition')
    with pytest.raises(ValueError,match='changed'):checked_spec(root,snapshot.relative_to(root))


def test_changed_task_bytes_without_new_hash_are_rejected(fixture):
    root,spec,snapshot,manifest,audit=fixture
    spec['objects'][0]['pose']['position_m'][0]=.2;snapshot.write_text(json.dumps(spec))
    with pytest.raises(ValueError,match='hash'):checked_spec(root,snapshot.relative_to(root))


@pytest.mark.parametrize('field',['mass_kg','pose','collision'])
def test_unknown_inputs_gate_before_isaac_or_process_launch(fixture,monkeypatch,field):
    root,spec,snapshot,manifest,audit=fixture
    if field=='mass_kg':spec['objects'][0]['mass_kg']=None
    elif field=='pose':spec['objects'][0]['pose']['position_m']=None
    else:spec['objects'][0]['collision']={'type':'unknown'}
    persist(snapshot,spec)
    monkeypatch.setattr('modules.scene_building.service.resolve',lambda *a,**k:pytest.fail('missing task facts must gate before Isaac environment access'))
    result=build_scene({'root':root,'run_id':'test','output_dir':root/'output','input':{'spec_path':str(snapshot.relative_to(root))},'dependencies':{},'check_cancel':lambda:None,'emit':lambda *a:None})
    assert result['status']=='needs_configuration' and result['missing_fields']


@pytest.mark.parametrize('reference',['evidence/frame.txt','tasks/fixture/profile.json'])
def test_validator_rechecks_evidence_and_profile_after_build(fixture,reference):
    root,spec,snapshot,manifest,audit=fixture
    (root/reference).write_text('changed between building and validating')
    rejected_or_failed(lambda:validate_manifest(root,manifest,audit))


def test_empty_usd_cannot_pass_scene_structure_from_manifest_alone(fixture):
    root,spec,snapshot,manifest,audit=fixture
    audit.update(rigid_bodies=[],physics_scenes=[],cameras=[])
    rejected_or_failed(lambda:validate_manifest(root,manifest,audit))


def test_truncated_png_cannot_pass_as_a_rendered_image(fixture):
    root,spec,snapshot,manifest,audit=fixture
    image=root/'build/scene.png';image.write_bytes(image.read_bytes()[:24])
    rejected_or_failed(lambda:validate_manifest(root,manifest,audit))


@pytest.mark.parametrize('failure',['below_table','moving','outside_table','pair_joint','controller'])
def test_actual_local_failure_conditions_remain_failures(fixture,failure):
    root,spec,snapshot,manifest,audit=fixture
    if failure=='below_table':manifest['world_visual_bounds']['block']['min'][2]=.75
    elif failure=='moving':manifest['dynamics']['block']['linear_velocity_m_s']=[.1,0,0]
    elif failure=='outside_table':manifest['world_visual_bounds']['block']['max'][0]=.7
    elif failure=='pair_joint':manifest['connector_pair_joints']=['/World/ForbiddenPairWeld']
    else:manifest['robot_motion_controller_loaded']=True
    report=validate_manifest(root,manifest,audit)
    assert report['status']=='failed'
    assert report['full_task_evaluated'] is False
