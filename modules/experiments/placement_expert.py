"""Bind an evidenced panel-and-channel preview and freeze placement variants."""
import numpy as np
from scipy.spatial import ConvexHull, QhullError
from scipy.spatial.transform import Rotation

from modules.geometry.functional import load_mesh, section


CLEARANCE_TOLERANCE_M = .0002


class UnsupportedPlacementTask(ValueError):
    """No compatible rigid-panel experiment can be bound to this scene."""


class SlotClearanceError(ValueError):
    def __init__(self, panel, slot, role, panel_yaw, slot_yaw, opening, span):
        label = slot.get('label',slot['id'])
        self.details = {'panel_id':panel['id'],'slot_id':slot['id'],'slot_label':label,'slot_role':role,
                        'panel_yaw_deg':panel_yaw,'slot_yaw_deg':slot_yaw,'opening':opening.copy(),
                        'required_m':float(span)}
        super().__init__(f'「{label}」的碰撞开口宽 {opening["gap_m"]*1000:.1f} mm，'
                         f'当前面板碰撞体直立坐到底需要 {span*1000:.1f} mm。场景局部稳定已通过，目标入槽还需核对。')


def _dimensions(obj):
    if obj.get('preview_bounds_size_m') is not None:
        size = np.asarray(obj['preview_bounds_size_m'],float)
    else:
        size = np.asarray(obj['size_m'],float)*obj['scale']
    if size.shape!=(3,) or not np.isfinite(size).all() or np.any(size<=0):
        raise ValueError('场景缺少可信的物体尺寸')
    return size


def _collision_bounds(obj, yaw):
    aligned = Rotation.from_euler('z',-yaw,degrees=True).as_matrix()
    for box in obj['collision']['boxes']:
        center = aligned @ box['center_m']
        half = np.abs(aligned) @ box['size_m']/2
        yield center-half,center+half


def _slot_opening(obj, size, yaw):
    if obj.get('geometry_type','box')=='box':
        return {'floor_z_m':-.24*size[2],'gap_m':.64*size[1],'across_center_m':0.,'insertion_depth_m':.74*size[2]}
    if obj['collision']['type']!='preview_compound':
        raise ValueError(f'「{obj.get("label",obj["id"])}」的碰撞体未保留可测的槽口')
    bounds = list(_collision_bounds(obj,yaw))
    floors=[hi[2] for lo,hi in bounds if lo[0]<=0<=hi[0] and lo[1]<0<hi[1]]
    if not floors:
        raise ValueError('场景槽口缺少可测的碰撞槽底')
    floor=max(floors)
    left=[(hi[1],hi[2]) for lo,hi in bounds if lo[0]<=0<=hi[0] and hi[1]<0 and hi[2]>floor]
    right=[(lo[1],hi[2]) for lo,hi in bounds if lo[0]<=0<=hi[0] and lo[1]>0 and hi[2]>floor]
    if not left or not right:
        raise ValueError('场景槽口缺少可测的两侧碰撞面')
    edge_left,top_left=max(left,key=lambda edge:edge[0])
    edge_right,top_right=min(right,key=lambda edge:edge[0])
    return {'floor_z_m':float(floor),'gap_m':float(edge_right-edge_left),
            'across_center_m':float((edge_right+edge_left)/2),'insertion_depth_m':float(min(top_left,top_right)-floor)}


def _read_geometry(root, obj, yaw):
    path=root/obj['asset_path']
    if path.resolve()!=path or not path.is_relative_to(root):
        raise ValueError('入槽网格路径无效')
    points,faces=load_mesh(path)
    points=(points-np.asarray(obj.get('preview_mesh_center_asset',[0,0,0])))*obj['scale']
    return Rotation.from_euler('z',-yaw,degrees=True).apply(points),faces


def _lower_panel_span(root, panel, yaw, height):
    kind = panel['collision']['type']
    if kind not in ('convex_hull','preview_compound') or root is None:
        raise ValueError('当前面板碰撞体需要独立的入槽几何检查')
    points,_=_read_geometry(root,panel,yaw)
    bottom = points[:,2].min()
    if kind=='preview_compound':
        bounds = list(_collision_bounds(panel,yaw))
        if abs(min(lo[2] for lo,_ in bounds)-bottom)>CLEARANCE_TOLERANCE_M:
            raise ValueError('面板复合碰撞体的底缘与原始模型不一致')
        lower = [(lo,hi) for lo,hi in bounds if lo[2]<=bottom+height]
        return np.min([lo[:2] for lo,_ in lower],axis=0),np.max([hi[:2] for _,hi in lower],axis=0)
    points[:,2]-=bottom
    try:
        hull=ConvexHull(points)
    except QhullError as exc:
        raise ValueError('面板碰撞凸包无法核对插入截面') from exc
    below=points[hull.vertices]
    below=below[below[:,2]<=height]
    i,j=np.concatenate([hull.simplices[:,[a,b]] for a,b in ((0,1),(0,2),(1,2))]).T
    left,right=points[i],points[j]
    cross=(left[:,2]<=height)!=(right[:,2]<=height)
    left,right=left[cross],right[cross]
    at_height=left+(right-left)*((height-left[:,2])/(right[:,2]-left[:,2]))[:,None]
    footprint=np.concatenate((below,at_height))[:,:2]
    return footprint.min(axis=0),footprint.max(axis=0)


def audit_source_clearance(root, spec, failure):
    """Check the original meshes at corresponding insertion heights before editing collisions."""
    objects={obj['id']:obj for obj in spec['objects']}
    panel,slot=(objects[failure[key]] for key in ('panel_id','slot_id'))
    if not panel.get('asset_path') or not slot.get('asset_path'):
        return {'status':'no_source_mesh_pair','samples':[]}
    points,faces=_read_geometry(root,panel,failure['panel_yaw_deg'])
    walls,wall_faces=_read_geometry(root,slot,failure['slot_yaw_deg'])
    bottom=float(points[:,2].min())
    opening=failure['opening']
    depth=opening['insertion_depth_m']
    axis=np.eye(3)

    def cut(vertices, triangles, origin, normal, u, v):
        return np.asarray(section(vertices,triangles,origin,normal,u,v)['segments']).reshape(-1,2,2)

    def crossings(edges, value, coordinate):
        a,b=edges[:,0],edges[:,1]
        crossing=(a[:,coordinate]<=value)!=(b[:,coordinate]<=value)
        a,b=a[crossing],b[crossing]
        ratio=(value-a[:,coordinate])/(b[:,coordinate]-a[:,coordinate])
        index=1-coordinate
        return a[:,index]+(b[:,index]-a[:,index])*ratio

    samples=[]
    width_center=opening['across_center_m']
    for fraction in (.2,.5,.8,.95,.998):
        height=depth*fraction
        plate=cut(points,faces,[0.,0.,bottom+height],axis[2],axis[0],axis[1]).reshape(-1,2)
        if not len(plate):
            continue
        walls_at_height=[]
        for x in np.linspace(plate[:,0].min(),plate[:,0].max(),5)[1:-1]:
            profile=cut(walls,wall_faces,[x,0.,0.],axis[0],axis[1],axis[2])
            floors=crossings(profile,width_center,0)
            if not len(floors):
                continue
            sides=crossings(profile,max(floors)+height,1)
            left,right=sides[sides<width_center],sides[sides>width_center]
            if len(left) and len(right):
                walls_at_height.append((left.max(),right.min()))
        if len(walls_at_height)==3:
            left,right=np.asarray(walls_at_height).T
            samples.append({'height_m':height,'source_gap_m':float(right.min()-left.max()),
                            'source_outer_aperture_m':float(right.max()-left.min()),
                            'source_panel_span_m':float(np.ptp(plate[:,1])),
                            'source_panel_y_bounds_m':[float(plate[:,1].min()),float(plate[:,1].max())]})
    conflict=next((row for row in samples
                   if row['source_panel_span_m']>row['source_outer_aperture_m']+CLEARANCE_TOLERANCE_M),None)
    support = len(samples)==5 and all(row['source_gap_m']-row['source_panel_span_m']>CLEARANCE_TOLERANCE_M
                                    for row in samples)
    return {'status':'source_interference' if conflict else 'no_interference_sampled' if support else 'insufficient_clearance_evidence',
            'samples':samples,'conflicting_slice':conflict}


def verify_clearance_repair(experiment, failure, audit):
    """Keep the same seating geometry and require source evidence for the revised proxies."""
    opening = experiment['slot_openings'][failure['slot_role']]
    before = failure['opening']
    tolerance = CLEARANCE_TOLERANCE_M
    low,high = (row[1] for row in opening['panel_footprint_xy_bounds_m'])
    if any(abs(opening[key]-before[key])>tolerance for key in ('floor_z_m','insertion_depth_m','across_center_m')):
        raise ValueError('入槽纠错改变了原定坐底深度或槽口中心')
    for sample in audit['samples']:
        if opening['gap_m']>sample['source_gap_m']+tolerance:
            raise ValueError('修正槽口宽度超过原始模型的测量开口')
        left,right = sample['source_panel_y_bounds_m']
        if low>left+tolerance or high<right-tolerance:
            raise ValueError('修正面板碰撞体遗漏了原始模型的入槽截面')


def freeze(spec, *, roles=None, root=None):
    objects = {obj['id']:obj for obj in spec['objects']}
    roles = roles or {identifier:{'shape':obj.get('preview_shape'),'support_id':obj.get('preview_support_id')}
                      for identifier,obj in objects.items()}
    def role(identifier):
        return roles.get(identifier,{})
    panels = [obj for obj in objects.values() if role(obj['id']).get('shape')=='framed_panel'
              and role(obj['id']).get('support_relation') in (None,'inside')
              and role(role(obj['id']).get('support_id')).get('shape')=='channel'
              and role(obj['id']).get('support_id') in objects]
    if not panels:
        raise UnsupportedPlacementTask('当前任务没有匹配的动作专家。现有学习模板适用于刚性面板换槽。')
    if len(panels)>1:
        raise ValueError('需要从示教识别唯一的带支架面板，当前模板仅支持刚性面板换槽')
    panel = panels[0]
    source = objects[role(panel['id'])['support_id']]
    alternatives = [obj for obj in objects.values() if role(obj['id']).get('shape')=='channel' and obj is not source]
    if len(alternatives)!=1:
        raise ValueError('需要从场景识别唯一目标槽，才能生成位置变化和验证用例')
    target = alternatives[0]
    geometry = (panel,source,target)
    native = [_dimensions(item) for item in geometry]
    frame_yaw = [-90. if item.get('geometry_type')=='mesh' and size[1]>size[0] else 0. for item,size in zip(geometry,native)]
    sizes = [size[[1,0,2]] if yaw else size for size,yaw in zip(native,frame_yaw)]
    openings = {}
    for index,name in ((1,'source'),(2,'goal')):
        slot=geometry[index]
        opening=_slot_opening(slot,sizes[index],frame_yaw[index])
        low,high=(_lower_panel_span(root,panel,frame_yaw[0],opening['insertion_depth_m'])
                  if panel.get('geometry_type')=='mesh' else (-sizes[0][:2]/2,sizes[0][:2]/2))
        span=high-low
        if span[0]>=sizes[index][0]:
            raise ValueError(f'「{slot.get("label",slot["id"])}」的槽长度不足以容纳面板底边')
        if span[1]>=opening['gap_m']:
            raise SlotClearanceError(panel,slot,name,frame_yaw[0],frame_yaw[index],opening,span[1])
        opening['panel_footprint_xy_bounds_m']=[low.tolist(),high.tolist()]
        openings[name]=opening
    table = spec['scene']['table']
    center = np.asarray(table['position_m'][:2])
    half_table = np.asarray(table['size_m'][:2])/2
    margins = []
    for obj,size in zip(geometry,native):
        q = np.asarray(obj['pose']['quaternion_wxyz'])
        half_extent = np.abs(Rotation.from_quat(q[[1,2,3,0]]).as_matrix())@size/2
        margins.append(half_table-np.abs(np.asarray(obj['pose']['position_m'][:2])-center)-half_extent[:2]-.005)
    amplitude = float(min(.05,np.min(margins)))
    separation = np.linalg.norm(np.subtract(source['pose']['position_m'][:2],target['pose']['position_m'][:2]))
    if amplitude<.005 or separation<max(sizes[1][0],sizes[2][0])+.05:
        raise ValueError('场景可用桌面或槽间距不足，无法生成独立的组件位置变化')
    rng = np.random.default_rng(2509)
    train = [np.zeros((2,2)),*rng.uniform(-.55*amplitude,.55*amplitude,(3,2,2))]
    test_rng = np.random.default_rng(8281)
    test = (np.sign(delta)*test_rng.uniform(.68*amplitude,.95*amplitude,delta.shape)
            for delta in test_rng.uniform(-1,1,(3,2,2)))
    def cases(label, variants):
        return [{'id':f'{label}_{i}','source_xy_m':v[0].tolist(),'goal_xy_m':v[1].tolist()} for i,v in enumerate(variants)]
    return {'schema_version':'rigid_panel_placement_experiment/v1','task_spec_hash':spec['content_hash'],'held_out_seed':8281,
            'objects':{'panel':panel['id'],'source':source['id'],'goal':target['id']},
            'sizes_m':{name:size.tolist() for name,size in zip(('panel','source','goal'),sizes)},
            'object_frame_z_deg':dict(zip(('panel','source','goal'),frame_yaw)), 'slot_openings':openings,
            'train':cases('train',train),'test':cases('holdout',test),'position_amplitude_m':amplitude,
            'acceptance':{'lift_m':.035,'height_error_m':.025,'tilt_deg':12.,'release_clearance_m':.045,
                          'hold_s':.5,'hold_translation_m':.015,'hold_speed_m_s':.04}}
