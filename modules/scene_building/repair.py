"""Validate and materialize a task-specific scene correction proposal."""
from copy import deepcopy
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from contracts.tasks import canonical_hash, validate_task_spec, vector
from modules.geometry.functional import load_mesh, section


def mesh_evidence(root, obj):
    """Report measured slices in the same centered local coordinates as the USD mesh."""
    path = root/obj['asset_path']
    points,faces = load_mesh(path)
    center = obj.get('preview_mesh_center_asset',[0,0,0])
    points = (points-np.asarray(center))*np.asarray(obj['scale'])
    lower,upper = points.min(0),points.max(0)
    along = int(np.argmax((upper-lower)[:2]))
    across = 1-along
    basis = np.eye(3)
    slices = []
    for fraction in (.05,.25,.5,.75,.95):
        origin = np.zeros(3)
        origin[along] = lower[along]+fraction*(upper[along]-lower[along])
        cut = section(points,faces,origin,basis[along],basis[across],basis[2])
        edges = np.asarray(cut['segments']).reshape(-1,2,2)
        if not len(edges):
            continue
        samples = []
        boundaries = []
        for width in np.linspace(lower[across],upper[across],17)[1:-1]:
            a,b = edges[:,0],edges[:,1]
            hit = (a[:,0]>width)!=(b[:,0]>width)
            a,b = a[hit],b[hit]
            heights = a[:,1]+(b[:,1]-a[:,1])*(width-a[:,0])/(b[:,0]-a[:,0])
            samples.append([round(float(width),6),round(float(max(heights)),6) if len(heights) else None])
            ordered=np.unique(np.round(heights,6))
            if len(ordered)>8:
                ordered=np.r_[ordered[:4],ordered[-4:]]
            boundaries.append([round(float(width),6),ordered.tolist()])
        slices.append({'position_m':round(float(origin[along]),6),'across_and_surface_z_m':samples,
                       'across_and_boundary_z_m':boundaries})
    bottom = []
    for fraction in (.01,.05,.10,.15):
        depth = float((upper[2]-lower[2])*fraction)
        cut = section(points,faces,np.array([0.,0.,lower[2]+depth]),basis[2],basis[0],basis[1])
        xy = np.asarray(cut['segments']).reshape(-1,2)
        if len(xy):
            bottom.append({'above_bottom_m':depth,'xy_bounds_m':[xy.min(0).tolist(),xy.max(0).tolist()]})
    return {'local_bounds_m':[lower.tolist(),upper.tolist()], 'slice_axis':'xy'[along],
            'across_axis':'xy'[across], 'cross_sections':slices,'bottom_sections':bottom}


def motion_geometry_ids(spec, object_id):
    """Limit proxy review to the contacted object and its declared support."""
    objects={obj['id']:obj for obj in spec['objects']}
    obj=objects.get(object_id,{})
    roles=(spec.get('scene_approximation') or {}).get('object_roles',{})
    support=obj.get('preview_support_id') or roles.get(object_id,{}).get('support_id')
    return [key for key in (object_id,support) if key in objects and objects[key]['geometry_type'] in ('mesh','box')]


def apply(base, proposal, measured, hashes, *, motion_object_id=None, allow_estimated_alignment=False):
    scene = deepcopy(base)
    scene['state'] = 'draft'
    scene.pop('frozen_at',None)
    objects = {obj['id']:obj for obj in scene['objects']}
    changed = set()
    edits = proposal['edits']
    translation=proposal.get('rig_translation_m') if motion_object_id or allow_estimated_alignment else None
    if motion_object_id:
        allowed=motion_geometry_ids(base,motion_object_id)
        if translation is not None and edits:
            raise ValueError('同一次接近回查只验证一类场景修正')
        if any(edit['object_id'] not in allowed or edit['position_m'] is not None or
               edit['rotation_xyz_deg'] is not None or not edit['collision_boxes'] for edit in edits):
            raise ValueError('接近回查仅可依据截面修正接触件及其支撑的碰撞代理')
    if len(edits)>len(objects) or (not edits and translation is None):
        raise ValueError('纠错方案需要给出有限且明确的对象修改')
    if translation is not None:
        vector(translation,3,'repair.rig_translation_m',False)
        shift=np.asarray(translation,dtype=float)
        robot=scene['robot']
        registration=robot.get('preview_registration') or {}
        if registration.get('status')!='estimated' or not robot.get('arms') or not .002<=np.linalg.norm(shift)<=.12:
            raise ValueError('基座修正需要已登记的估算标定及 2–120 毫米的局部调整')
        for arm in robot['arms']:
            pose=arm['base_pose']
            arm['base_pose']=pose | {'position_m':(np.asarray(pose['position_m'])+shift).tolist()}
        for owner,key in ((registration,'T_scene_calibration_board'),(registration['center_camera'],'T_world_camera_optical')):
            transform=np.asarray(owner[key],dtype=float)
            transform[:3,3]+=shift
            owner[key]=transform.tolist()
        registration['motion_repair_translation_m']=shift.tolist()
        registration['message']='按示教画面和接近碰撞报告修正估算基座，双臂相对标定关系保持一致。'
        scene['field_provenance']['/robot/arms']={'source':'task_scoped_approach_review',
            'validation_scope':'estimated_offline_scene_alignment','evidence_refs':list(hashes)}
    for edit in edits:
        identifier = edit['object_id']
        if identifier not in objects or identifier in changed:
            raise ValueError('纠错方案含未知或重复对象')
        changed.add(identifier)
        obj = objects[identifier]
        initial = deepcopy(obj)
        position = edit['position_m']
        orientation = edit['rotation_xyz_deg']
        if position is not None:
            vector(position,3,'repair.position_m',False)
            if any(abs(a-b)>.5 for a,b in zip(position,obj['pose']['position_m'])):
                raise ValueError('局部修正移动超出场景预算')
            obj['pose']['position_m'] = position
        if orientation is not None:
            vector(orientation,3,'repair.rotation_xyz_deg',False)
            if any(abs(value)>180 for value in orientation):
                raise ValueError('纠错姿态角超出范围')
            obj['pose']['quaternion_wxyz'] = Rotation.from_euler('xyz',orientation,degrees=True).as_quat()[[3,0,1,2]].tolist()
        boxes = edit['collision_boxes']
        if boxes:
            if identifier not in measured or not 1<=len(boxes)<=8:
                raise ValueError('复合碰撞形状需要当前网格的截面证据')
            lower,upper = np.asarray(measured[identifier]['local_bounds_m'])
            for box in boxes:
                vector(box['center_m'],3,'repair.collision_center_m',False)
                vector(box['size_m'],3,'repair.collision_size_m',False)
                center,half = np.asarray(box['center_m']),np.asarray(box['size_m'])/2
                if (half<.00025).any() or (center-half<lower-.001).any() or (center+half>upper+.001).any():
                    raise ValueError('碰撞体超出当前网格外形')
            obj['collision'] = {'type':'preview_compound','boxes':boxes}
        if obj==initial:
            raise ValueError('纠错修改未改变对象状态')
    scene['dependency_hashes']['evidence'].update(hashes)
    scene['scene_approximation']['repair_source_spec_hash'] = base['content_hash']
    scene['scene_approximation']['repair_scope'] = 'task_specific_preview_repair'
    scene['content_hash'] = canonical_hash(scene)
    return validate_task_spec(scene)
