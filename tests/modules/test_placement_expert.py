import hashlib
import json

import numpy as np
import pytest

from application import placement_expert
from application.stage_diagnosis import question_for_failure
from modules.evaluation.placement_expert import assess
from modules.experiments.placement_expert import freeze


def test_unmatched_task_exits_before_panel_training(tmp_path,monkeypatch):
    spec={'content_hash':'fixture','objects':[{'id':'parcel','preview_shape':'box'},
                                            {'id':'platform','preview_shape':'box'}]}
    audit={'replay_limit_check_passed':True,'scene_robot_ids':['left','right']}
    monkeypatch.setattr(placement_expert,'preflight',lambda ctx:audit)
    monkeypatch.setattr(placement_expert,'_bound_scene',lambda root,audited:('scene.json',{},spec))
    monkeypatch.setattr(placement_expert,'resolve',lambda *_:pytest.fail('unexpected simulator startup'))
    result=placement_expert.run({'root':tmp_path,'input':{},'dependencies':{}})
    assert result['status']=='needs_configuration'
    assert result['unsupported_capability']=='task_scoped_action_expert'
    assert '没有匹配的动作专家' in result['reason']


def test_placement_trials_freeze_held_out_positions_and_require_released_retention():
    def body(identifier, shape, position, size, **extra):
        return {'id':identifier,'preview_shape':shape,'size_m':size,'scale':[1,1,1],
                'pose':{'position_m':position,'quaternion_wxyz':[1,0,0,0]},**extra}

    spec = {'content_hash':'fixture', 'objects':[
        body('panel','framed_panel',[.30,0,.24],[.25,.02,.38],preview_support_id='shelf'),
        body('shelf','channel',[.30,0,.03],[.32,.07,.06]),
        body('slot','channel',[-.30,0,.03],[.34,.08,.06])],
        'scene':{'table':{'position_m':[0,0,0],'size_m':[1.20,.50,.10]}}}
    experiment = freeze(spec)
    assert experiment==freeze(spec) and experiment['objects']['goal']=='slot'
    train = np.array([[row['source_xy_m'],row['goal_xy_m']] for row in experiment['train']])
    test = np.array([[row['source_xy_m'],row['goal_xy_m']] for row in experiment['test']])
    assert np.abs(train).max()<np.abs(test).min()
    assert np.all(np.any(test[:,0]!=test[:,1],axis=1))

    destination = [-.30,0,.03+.38/2-.24*.06]
    trace = {'times_s':np.array([0.,.25,1.,1.25,1.5,1.75]), 'goal_m':np.array([-.30,0,.03]),
             'source_m':np.array([.30,0,.03]), 'goal_wxyz':np.array([1.,0,0,0]),
             'panel_m':np.asarray([[.30,0,.24],[.15,0,.33],*([destination]*4)]),
             'panel_wxyz':np.tile([1.,0,0,0],(6,1)), 'finger_m':np.ones((6,4,3)),
             'panel_speed_m_s':np.zeros(6)}
    assert assess(trace,experiment)['passed']
    trace['finger_m'][-1,0] = destination
    result = assess(trace,experiment)
    assert result['seated'] and result['transferred'] and not result['retained'] and not result['passed']


def test_bound_mesh_slots_use_their_opening_and_native_axis():
    def holder(identifier,x,gap):
        return {'id':identifier,'label':identifier,'geometry_type':'mesh','scale':[.001]*3,
                'preview_bounds_size_m':[.04,.26,.05],
                'pose':{'position_m':[x,0,.78],'quaternion_wxyz':[2**-.5,0,0,2**-.5]},
                'collision':{'type':'preview_compound','boxes':[
                    {'center_m':[0,0,-.02],'size_m':[.04,.26,.01]},
                    *[{'center_m':[s*(.02+gap/2)/2,0,.005],'size_m':[.02-gap/2,.26,.04]} for s in (-1,1)]]}}

    panel={'id':'panel','geometry_type':'box','size_m':[.18,.021,.27],'scale':[1,1,1],
           'pose':{'position_m':[.29,0,.92],'quaternion_wxyz':[1,0,0,0]}}
    spec={'content_hash':'fixture','scene':{'table':{'position_m':[0,0,.72],'size_m':[1.1,.5,.06]}},
          'objects':[panel,holder('holder',.29,.025),holder('goal',-.29,.020)]}
    roles={'panel':{'shape':'framed_panel','support_id':'holder'},'holder':{'shape':'channel'},'goal':{'shape':'channel'}}
    with pytest.raises(ValueError,match=r'goal.*20\.0 mm.*21\.0 mm'):
        freeze(spec,roles=roles)
    panel['size_m'][1]=.018
    result=freeze(spec,roles=roles)
    assert result['sizes_m']['goal']==[.26,.04,.05]
    assert result['object_frame_z_deg']['goal']==-90.
    assert result['slot_openings']['goal']['gap_m']==pytest.approx(.02)


@pytest.mark.parametrize('source_gap', [.020, .025])
def test_insertion_handoff_uses_original_mesh_and_rebinds_repaired_scene(tmp_path,monkeypatch,source_gap):
    def boxes(gap):
        return [{'center_m':[0,0,-.02],'size_m':[.26,.05,.01]},
                *[{'center_m':[0,side*(.025+gap/2)/2,.005],'size_m':[.26,.025-gap/2,.04]} for side in (-1,1)]]

    def write_obj(path, shapes):
        signs = np.asarray([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],
                            [-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]])
        faces = [(1,4,3,2),(5,6,7,8),(1,2,6,5),(2,3,7,6),(3,4,8,7),(4,1,5,8)]
        rows = []
        for index,box in enumerate(shapes):
            for vertex in signs*np.asarray(box['size_m'])/2+box['center_m']:
                rows.append('v '+' '.join(map(str,vertex)))
            rows.extend('f '+' '.join(str(item+8*index) for item in face) for face in faces)
        path.write_text('\n'.join(rows)+'\n')

    def mesh(identifier,x,size,shapes,collision):
        path = tmp_path/(identifier+'.obj')
        write_obj(path,shapes)
        return {'id':identifier,'geometry_type':'mesh','asset_path':path.name,'scale':[1,1,1],
                'preview_bounds_size_m':size,'pose':{'position_m':[x,0,.85],'quaternion_wxyz':[1,0,0,0]},
                'collision':collision}

    panel = mesh('panel',.30,[.18,.022,.27],[{'center_m':[0,0,0],'size_m':[.18,.022,.27]}],{'type':'convex_hull'})
    holder = mesh('holder',.30,[.26,.05,.05],boxes(.03),{'type':'preview_compound','boxes':boxes(.03)})
    goal = mesh('goal',-.30,[.26,.05,.05],boxes(source_gap),{'type':'preview_compound','boxes':boxes(.020)})
    roles = {'panel':{'shape':'framed_panel','support_id':'holder'},'holder':{'shape':'channel'},'goal':{'shape':'channel'}}
    original = {'content_hash':'original','scene_approximation':{'object_roles':roles},
                'scene':{'table':{'position_m':[0,0,.72],'size_m':[1.2,.5,.06]}},'objects':[panel,holder,goal]}
    revised = original | {'content_hash':'revised','objects':[
        panel,holder,goal | {'collision':{'type':'preview_compound','boxes':boxes(source_gap)}}]}
    scenes = {'original':original,'revised':revised}
    for name in scenes:
        (tmp_path/(name+'.json')).write_text(json.dumps({'spec_snapshot_path':name+'.spec','task_spec_hash':name}))
    monkeypatch.setattr(placement_expert,'load_snapshot',lambda root,path,digest:scenes[digest])
    monkeypatch.setattr(placement_expert,'resolve',lambda *args:None)
    calls = []

    def preflight(ctx):
        manifest = ctx['dependencies']['scene_build']['manifest_path']
        calls.append(manifest)
        return {'replay_limit_check_passed':True,'scene_robot_ids':['left','right'],'scene_manifest':manifest,
                'scene_manifest_sha256':hashlib.sha256((tmp_path/manifest).read_bytes()).hexdigest(),
                'report_path':manifest+'.replay'}

    def repair(ctx,build,validation,*,clearance_check,clearance_evidence):
        assert source_gap==.025 and build['manifest_path']=='original.json'
        assert clearance_evidence['source_geometry']['status']=='no_interference_sampled'
        fabricated = revised | {'objects':[panel,holder,goal | {'collision':{'type':'preview_compound','boxes':boxes(.034)}}]}
        with pytest.raises(ValueError,match='超过原始模型'):
            clearance_check(fabricated)
        assert clearance_check(revised)['passed']
        return {'status':'succeeded','report_path':'physics.json','repair_report_path':'repair.json',
                'resolved_scene_build':{'manifest_path':'revised.json','task_spec_hash':'revised'}}

    monkeypatch.setattr(placement_expert,'preflight',preflight)
    monkeypatch.setattr(placement_expert,'repair_scene',repair)
    result = placement_expert.run({'root':tmp_path,'output_dir':tmp_path/'simulation','input':{},
                                   'dependencies':{'scene_build':{'manifest_path':'original.json'}},
                                   'check_cancel':lambda:None,'emit':lambda *_:None})
    report = json.loads((tmp_path/result['insertion_report_path']).read_text())
    source = report['checks'][0]['details']['source_geometry']
    if source_gap==.020:
        assert source['status']=='source_interference'
        assert source['conflicting_slice']['source_panel_span_m']==pytest.approx(.022)
        assert calls==['original.json'] and '原始模型显示' in result['reason']
        assert result['status']=='blocked' and result['unsupported_capability']=='alternative_insertion_pose'
        assert question_for_failure('simulation',result) is None
    else:
        assert source['status']=='no_interference_sampled'
        assert calls==['original.json','revised.json']
        assert result['repaired_scene_manifest']=='revised.json'
        assert result['repaired_scene_validation_path']=='physics.json'
