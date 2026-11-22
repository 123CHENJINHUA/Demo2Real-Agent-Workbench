"""Use image-associated tool poses as approximate anchors for preview geometry."""

from copy import deepcopy

import numpy as np
from scipy.spatial.transform import Rotation

from contracts.tasks import canonical_hash, validate_task_spec, vector


def observed_anchors(spec, proposal, motion, frames):
    events={event['id']:event for event in motion.get('events',[])}
    linked=[]
    used=set()
    objects={obj['id'] for obj in spec['objects']}
    for anchor in proposal.get('motion_anchors',[]):
        reference=anchor['frame_ref']
        key=(anchor['object_id'],anchor['role'])
        event=events.get(anchor['event_id'])
        if (event is None or anchor['object_id'] not in objects or key in used
                or anchor['role'] not in ('initial_contact','target_contact')
                or event['id'] not in frames.get(reference,{}).get('event_ids',[])):
            continue
        point=anchor['object_point_m']
        if point is not None:
            vector(point,3,'scene motion anchor object point',False)
        used.add(key)
        linked.append(anchor | {'event':event,'urdf_path':motion['arms'][event['arm']]['urdf_path']})
    return linked


def apply_motion_anchors(spec):
    approx=spec.get('scene_approximation') or {}
    anchors=approx.get('motion_anchors',[])
    if not anchors or approx.get('motion_anchor_review') or not spec['robot'].get('arms'):
        return spec
    scene=deepcopy(spec)
    arms={arm['id']:arm for arm in scene['robot']['arms']}
    objects={obj['id']:(i,obj) for i,obj in enumerate(scene['objects'])}
    table=scene['scene']['table']
    reviewed=[]
    for anchor in anchors:
        event=anchor['event']
        arm=arms.get(event['arm'])
        if not arm or arm['urdf_path']!=anchor['urdf_path']:
            continue
        base=arm['base_pose']
        q=np.asarray(base['quaternion_wxyz'])
        tool=np.asarray(base['position_m'])+Rotation.from_quat(q[[1,2,3,0]]).apply(event['position_in_base_m'])
        row=anchor | {'tool_point_world_m':tool.tolist(),'used_for_initial_position':False}
        reviewed.append(row)
        if anchor['role']!='initial_contact' or anchor['object_point_m'] is None:
            continue
        index,obj=objects[anchor['object_id']]
        pose_key=f'/objects/{index}/pose/position_m'
        if scene['field_provenance'].get(pose_key,{}).get('source')!='multi_view_proxy_assumption':
            continue
        if obj['id'] in (approx.get('placement_alignment') or {}).get('objects',{}):
            continue
        point=np.asarray(anchor['object_point_m'])
        size=_size(obj)
        if size is None or (np.abs(point)>size/2+.002).any():
            continue
        pose=obj['pose']
        q=np.asarray(pose['quaternion_wxyz'])
        candidate=tool-Rotation.from_quat(q[[1,2,3,0]]).apply(point)
        support=objects.get(obj.get('preview_support_id'))
        support_position,support_size=(table['position_m'],np.asarray(table['size_m']))
        local_xy=candidate[:2]-np.asarray(support_position[:2])
        if support:
            fixture=support[1]
            support_pose=fixture['pose']
            support_size=_size(fixture)
            reference_q=np.asarray(support_pose['quaternion_wxyz'])
            local_xy=Rotation.from_quat(reference_q[[1,2,3,0]]).inv().apply(
                candidate-np.asarray(support_pose['position_m']))[:2]
        if (support_size is None or (np.abs(local_xy)>support_size[:2]/2).any()
                or abs(candidate[2]-pose['position_m'][2])>max(.02,size.min())):
            continue
        pose['position_m']=[*candidate[:2].tolist(),pose['position_m'][2]]
        row['used_for_initial_position']=True
        scene['field_provenance'][pose_key]={
            'source':'recorded_tool_contact_estimate','validation_scope':'estimated_registration_and_support_height',
            'evidence_refs':[anchor['frame_ref']]}
    scene['scene_approximation']['motion_anchor_review']=reviewed
    scene['content_hash']=canonical_hash(scene)
    return validate_task_spec(scene)


def _size(obj):
    if size:=obj.get('preview_bounds_size_m'):
        return np.asarray(size)
    if size:=obj.get('size_m'):
        return np.asarray(size)*obj['scale']
    return None
