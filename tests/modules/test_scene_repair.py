import json

import pytest

from application import collision_agent, scene_cycle, scene_repair
from contracts.tasks import canonical_hash
from modules.scene_building.repair import apply


@pytest.mark.parametrize('physically_valid',[True,False])
def test_independent_collision_agent_preserves_mesh_and_build_checks_candidate(tmp_path,monkeypatch,physically_valid):
    (tmp_path/'reference.obj').write_text('v 0 0 0\nv .2 0 0\nv 0 .1 0\nv 0 0 .08\nf 1 2 3\nf 1 2 4\nf 2 3 4\nf 1 3 4\n')
    (tmp_path/'reference.jpg').write_bytes(b'contact frame')
    part={'id':'workpiece','geometry_type':'mesh','asset_path':'reference.obj','scale':[1,1,1],
          'mass_kg':.2,'collision':{'type':'convex_hull'},
          'pose':{'position_m':[.1,.2,.8],'quaternion_wxyz':[1,0,0,0]}}
    spec={'schema_version':'task-spec/v1','task_id':'fixture','revision':1,'state':'draft',
          'source':{},'robot':{'arms':[{'id':'arm'}]},'scene':{'table':{}},'objects':[part],
          'frames':[],'phases':[],'assumptions':[],'unresolved':[],
          'field_provenance':{},'scene_approximation':{'source_task_spec_hash':'fixture'},
          'dependency_hashes':{'evidence':{},'assets':{}},'evaluation':{'action':{},'retention':{}},
          'hardware_verified':False,'contact_execution_authorized':False}
    spec['content_hash']=canonical_hash(spec)
    (tmp_path/'original.json').write_text(json.dumps(spec))
    boxes=[{'center_m':[.05,.02,.02],'size_m':[.04,.03,.03]}]

    def propose(ctx,prompt,schema,**kwargs):
        assert ctx['stage']=='collision_geometry' and 'reference_geometry' in prompt
        return {'status':'succeeded'},{'diagnosis':'根据参考网格保留空隙','edits':[
            {'object_id':'workpiece','collision_boxes':boxes,'frame_ref':'reference.jpg','reason':'证据'}]},['reference.jpg']

    built=[]
    events=[]

    def build(ctx):
        ref=ctx['dependencies']['scene_layout']['spec_path']
        snapshot=json.loads((tmp_path/ref).read_text())
        built.append(snapshot)
        return {'status':'succeeded','manifest_path':f'manifest{len(built)}.json','task_spec_hash':snapshot['content_hash']}

    def validate(ctx):
        assert ctx['dependencies']['scene_build']['manifest_path']=='manifest1.json'
        return {'status':'succeeded' if physically_valid else 'failed','report_path':'physical.json'}

    monkeypatch.setattr(collision_agent,'request_with_evidence',propose)
    monkeypatch.setattr(scene_cycle,'build_scene',build)
    monkeypatch.setattr(scene_cycle,'validate_scene',validate)
    ctx={'root':tmp_path,'output_dir':tmp_path/'scene_build','input':{'source_path':'fixture'},
         'dependencies':{'scene_layout':{'spec_path':'original.json'},'data_audit':{'sample_frames':[{'path':'reference.jpg'}]}},
         'emit':lambda kind,data:events.append((kind,data)),'check_cancel':lambda:None}
    result=scene_cycle.build_preview(ctx)
    assert result['collision_validation_path']=='physical.json'
    assert result['manifest_path']==('manifest1.json' if physically_valid else 'manifest2.json')
    assert built[0]['objects'][0]==part | {'collision':{'type':'preview_compound','boxes':boxes}}
    assert built[-1]['task_id']==spec['task_id'] and (physically_valid or built[-1]==spec)
    assert any(event['recipient']=='碰撞几何 Agent' for kind,event in events if kind=='agent.handoff_started')


def test_approach_repair_preserves_objects_and_relative_robot_registration():
    transform=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
    pose={'position_m':[0,0,.85],'quaternion_wxyz':[1,0,0,0]}
    objects=[{'id':'part','geometry_type':'box','collision':{'type':'box'},'pose':pose}]
    base={'schema_version':'task-spec/v1','task_id':'fixture','revision':1,'state':'draft',
          'source':{},'scene':{},'objects':objects,'frames':[],'phases':[],'assumptions':[],'unresolved':[],
          'robot':{'arms':[{'id':'left','base_pose':pose},
                           {'id':'right','base_pose':pose | {'position_m':[1,0,.85]}}],
                   'preview_registration':{'status':'estimated','T_scene_calibration_board':transform,
                       'center_camera':{'T_world_camera_optical':transform}}},
          'scene_approximation':{},'field_provenance':{},'dependency_hashes':{'evidence':{}},
          'evaluation':{'action':{},'retention':{}},'hardware_verified':False,'contact_execution_authorized':False}
    base['content_hash']=canonical_hash(base)
    proposal={'edits':[],'rig_translation_m':[.04,0,0]}
    revised=apply(base,proposal,{}, {'frame.jpg':'digest'},motion_object_id='part')
    assert revised['objects']==objects and base['robot']['arms'][0]['base_pose']==pose
    poses=[arm['base_pose']['position_m'] for arm in revised['robot']['arms']]
    assert poses==[[.04,0,.85],[1.04,0,.85]]
    registration=revised['robot']['preview_registration']
    assert registration['T_scene_calibration_board'][0][3]==.04
    assert registration['center_camera']['T_world_camera_optical'][0][3]==.04
    assert revised['dependency_hashes']['evidence']=={'frame.jpg':'digest'}
    base['robot']['preview_registration']['status']='measured'
    with pytest.raises(ValueError,match='估算标定'):
        apply(base,proposal,{}, {},motion_object_id='part')

    objects[0]['preview_support_id']='fixture_support'
    objects.append({'id':'fixture_support','geometry_type':'mesh','collision':{'type':'convex_hull'},
                    'pose':pose | {'position_m':[0,0,.73]}})
    boxes=[{'center_m':[0,0,-.02],'size_m':[.04,.08,.01]},
           {'center_m':[.03,0,0],'size_m':[.02,.08,.04]}]
    support={'object_id':'fixture_support','position_m':None,'rotation_xyz_deg':None,'collision_boxes':boxes}
    measured={'fixture_support':{'local_bounds_m':[[-.05]*3,[.05]*3]}}
    corrected=apply(base,{'edits':[support]},measured,{},motion_object_id='part')
    assert corrected['objects'][1]['collision']=={'type':'preview_compound','boxes':boxes}
    assert corrected['objects'][1]['pose']==objects[1]['pose']
    with pytest.raises(ValueError,match='碰撞代理'):
        apply(base,{'edits':[support | {'position_m':[0,0,.74]}]},measured,{},motion_object_id='part')


@pytest.mark.parametrize('check_clearance,check_visual',[(False,False),(True,False),(False,True)])
def test_failed_check_returns_to_agent_and_uses_independently_checked_scene(tmp_path,monkeypatch,check_clearance,check_visual):
    base = {'schema_version':'task-spec/v1','task_id':'fixture','revision':1,'state':'draft','profile_id':None,
            'source':{},'robot':{},'frames':[],'phases':[],'assumptions':[],'unresolved':[],
            'scene':{'table':{'size_m':[1,1,.1],'position_m':[0,0,.7]}},'scene_approximation':{'source_task_spec_hash':'source'},
            'objects':[{'id':'panel','geometry_type':'box','scale':[1,1,1],'size_m':[.2,.03,.2],'mass_kg':.2,
                        'collision':{'type':'box'},'pose':{'position_m':[0,0,.85],'quaternion_wxyz':[1,0,0,0]}}],
            'evaluation':{'action':{},'retention':{}},'field_provenance':{},'dependency_hashes':{'assets':{},'evidence':{}},
            'contact_execution_authorized':False,'hardware_verified':False}
    base['content_hash']=canonical_hash(base)
    manifest=tmp_path/'scene_manifest.json'
    manifest.write_text('{"settled_poses":{}}')
    (tmp_path/'view.jpg').write_bytes(b'fixture image')
    original={'status':'succeeded','spec_path':'base.json','task_spec_hash':base['content_hash'],'manifest_path':manifest.name}
    failed={'status':'failed','checks':[{'id':'settled:panel','passed':False,'details':{}}]}
    inputs=[]
    validated=[]
    monkeypatch.setattr(scene_repair,'previous_scene',lambda *args,**kwargs:{'views':[]})
    monkeypatch.setattr(scene_repair,'load_snapshot',lambda root,path,hash:base if path=='base.json' else json.loads((root/path).read_text()))

    def mocked_proposal(ctx,prompt,schema,comparison_images):
        inputs.append(prompt)
        return {'status':'succeeded'}, {'diagnosis':'fixture repair','evidence_ref':'view.jpg','edits':[
            {'object_id':'panel','position_m':[.01*len(inputs),0,.85],'rotation_xyz_deg':None,'collision_boxes':[]}]},['view.jpg']

    def build(ctx):
        layout=ctx['dependencies']['scene_layout']
        return original | {'spec_path':layout['spec_path'],'task_spec_hash':layout['spec_hash']}

    def validate(ctx):
        validated.append(ctx['dependencies']['scene_build'])
        return failed if len(validated)==1 and not check_visual else {'status':'succeeded','checks':[{'id':'settled:panel','passed':True}]}

    def compare(ctx, candidate):
        render_path = tmp_path/'orientation.json'
        render_path.write_text('{}')
        if len(validated)==1:
            return {'status':'orientation_mismatch','report_path':render_path.name,
                    'checks':[{'id':'visual_orientation:panel','passed':False,'details':{'cue':'接线盒方向反了'}}]}
        return {'status':'orientation_verified','report_path':render_path.name}

    def clearance(spec):
        if spec['objects'][0]['pose']['position_m'][0]==.01:
            raise ValueError('fixture clearance failed')
        return {'id':'upright_insertion_clearance','passed':True}

    monkeypatch.setattr(scene_repair,'request_with_evidence',mocked_proposal)
    monkeypatch.setattr(scene_repair,'build_preview',build)
    monkeypatch.setattr(scene_repair,'validate_scene',validate)
    monkeypatch.setattr(scene_repair,'review_visual',compare)
    inputs_ctx = {'source_path':'/fixture'} if check_visual else {}
    result=scene_repair.repair({'root':tmp_path,'output_dir':tmp_path/'review','run_id':'fixture','input':inputs_ctx,'dependencies':{},
                                'check_cancel':lambda:None,'emit':lambda *args:None},original,failed,
                               clearance_check=clearance if check_clearance else None)
    assert result['status']=='succeeded' and result['resolved_scene_build']==validated[1]
    assert base['objects'][0]['pose']['position_m']==[0,0,.85] and 'fixture repair' in inputs[1]
    log=json.loads((tmp_path/result['repair_report_path']).read_text())
    assert [row['status'] for row in log['attempts']]==(['rejected'] if check_clearance else [])+[
        'blocked' if check_visual else 'failed','succeeded']
    if check_clearance:
        assert 'fixture clearance failed' in inputs[1]
        assert log['attempts'][-1]['clearance']['passed']
    if check_visual:
        assert '接线盒方向反了' in inputs[1]
        assert result['visual_comparison_status']=='orientation_verified'

