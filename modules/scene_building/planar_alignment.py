"""Reconcile tabletop landmarks in multiple recorded views using planar rectification."""
import itertools

import numpy as np
from scipy.spatial.transform import Rotation


# Semantic corner order in the task's tabletop frame, independent of image orientation.
TABLE_CORNERS = np.array([[0.,0.],[1.,0.],[1.,1.],[0.,1.]])


def _uv(value):
    point=np.asarray(value,dtype=float)
    if point.shape!=(2,) or not np.isfinite(point).all() or np.any((point<0)|(point>1)):
        raise ValueError('标记点必须在图像内')
    return point


def _rectifier(corners):
    uv=np.array([_uv(point) for point in corners])
    if uv.shape!=(4,2):
        raise ValueError('需要同一工作面的四个可见角点')
    edges=np.roll(uv,-1,axis=0)-uv
    following=np.roll(edges,-1,axis=0)
    turns=edges[:,0]*following[:,1]-edges[:,1]*following[:,0]
    if not (np.all(turns>.004) or np.all(turns<-.004)):
        raise ValueError('工作面角点顺序错误、轮廓太窄或面积不足')
    # Solve for the homography using normalized pixel and tabletop coordinates.
    matrix=[]
    right=[]
    for (u,v),(x,y) in zip(uv,TABLE_CORNERS):
        matrix.extend(([u,v,1,0,0,0,-u*x,-v*x],[0,0,0,u,v,1,-u*y,-v*y]))
        right.extend((x,y))
    if np.linalg.cond(matrix)>5000:
        raise ValueError('该视角的工作面投影不稳定')
    h=np.r_[np.linalg.solve(matrix,right),1.].reshape(3,3)

    def rectify(point):
        projected=h@np.r_[_uv(point),1.]
        if abs(projected[2])<1e-7:
            raise ValueError('标记点落在工作面投影的无穷远处')
        return projected[:2]/projected[2]

    return h,rectify


def fit_reference_camera(table, corners, image_size):
    """Estimate a perspective preview from a rectangular plane and square pixels."""
    h,_=_rectifier(corners)
    width,height=image_size
    if min(width,height)<32:
        return None
    sx,sy=table['size_m'][:2]
    ratio=height/width
    centered=np.array([[1,0,-.5],[0,ratio,-.5*ratio],[0,0,1.]])
    h=centered@np.linalg.inv(h)@np.diag([1/sx,1/sy,1.])
    a,b,t=h.T
    coefficients=np.array([a[:2]@b[:2],a[:2]@a[:2]-b[:2]@b[:2]])
    target=-np.array([a[2]*b[2],a[2]**2-b[2]**2])
    power=coefficients@coefficients
    if power<1e-8:
        return None
    inverse_focal_sq=float(coefficients@target/power)
    if not .15<inverse_focal_sq<16.:
        return None
    focal=1/np.sqrt(inverse_focal_sq)
    transform=np.diag([1/focal,1/focal,1.])@np.column_stack((a,b,t))
    scale=2/(np.linalg.norm(transform[:,0])+np.linalg.norm(transform[:,1]))
    if transform[2,2]<0:
        scale=-scale
    x,y,translation=(transform*scale).T
    raw=np.column_stack((x,y,np.cross(x,y)))
    u,_,v=np.linalg.svd(raw)
    rotation=u@v
    xy=TABLE_CORNERS*np.array([sx,sy])
    camera=np.column_stack((xy,np.zeros(4)))@rotation.T+translation
    if np.min(camera[:,2])<.04:
        return None
    reprojected=camera[:,:2]/camera[:,2,None]*np.array([focal,focal/ratio])+.5
    error=float(np.max(np.linalg.norm(reprojected-np.asarray(corners),axis=1)))
    top=table['position_m'][2]+table['size_m'][2]/2
    origin=np.r_[table_xy(table,[0,0]),top]
    eye=origin-rotation.T@translation
    if error>.035 or eye[2]<top or np.linalg.norm(eye-origin)>8.:
        return None
    return {'eye_m':eye.tolist(),'target_m':(eye+rotation[2]).tolist(),'up':(-rotation[1]).tolist(),
            'focal_fraction':float(focal),'width':int(width),'height':int(height),'corner_fit_error':error}


def observations(views, initial_frames, object_ids):
    """Fuse compatible normalized footprints from different cameras, retaining their references."""
    observed={}
    rejected=[]
    for view in views or []:
        if not isinstance(view,dict):
            continue
        ref=view.get('frame_ref')
        camera=initial_frames.get(ref)
        if not camera:
            continue
        try:
            _,rectify=_rectifier(view['corners_uv'])
        except (KeyError,TypeError,ValueError,np.linalg.LinAlgError) as exc:
            rejected.append({'frame_ref':ref,'reason':str(exc)})
            continue
        seen=set()
        for item in view.get('footprints') or []:
            if not isinstance(item,dict):
                continue
            key=item.get('object_id')
            if key not in object_ids or key in seen:
                continue
            seen.add(key)
            try:
                xy=rectify(item['center_uv'])
                # Propagate a small pixel uncertainty, including calibration-conditioning effects.
                uv=_uv(item['center_uv'])
                error=max(np.linalg.norm(rectify(np.clip(uv+delta,0,1))-xy)
                          for delta in ((.008,0),(-.008,0),(0,.008),(0,-.008)))
                if np.any((xy<0)|(xy>1)) or error>.10:
                    raise ValueError('落点超出工作面或透视误差过大')
            except (KeyError,TypeError,ValueError) as exc:
                rejected.append({'frame_ref':ref,'object_id':key,'reason':str(exc)})
                continue
            observed.setdefault(key,{})[camera]=(xy,error,ref)
    matched={}
    for key,cameras in observed.items():
        samples=list(cameras.values())
        if len(samples)<2:
            rejected.append({'object_id':key,'reason':'只有一个可用视角'})
            continue
        spread=max(float(np.linalg.norm(a[0]-b[0])) for a,b in itertools.combinations(samples,2))
        if spread>.11:
            rejected.append({'object_id':key,'reason':'多视角工作面落点不一致','spread':spread})
            continue
        matched[key]={'table_uv':np.median([item[0] for item in samples],axis=0).tolist(),
                      'tolerance':max(.045,spread+max(item[1] for item in samples)),
                      'frame_refs':[item[2] for item in samples]}
    return {'method':'recorded_table_plane/v1','objects':matched,'rejected':rejected}


def table_xy(table, point):
    return np.asarray(table['position_m'][:2])+(np.asarray(point)-.5)*np.asarray(table['size_m'][:2])


def line_spacing(views, initial_frames, objects, supports):
    """Estimate an in-line separation relative to a visible model edge, even in cropped views."""
    votes={}
    for view in views or []:
        try:
            ref=view['frame_ref']
            camera=initial_frames[ref]
            key=view['reference_object']
            axis=view['reference_axis']
            ids=tuple(sorted(view['objects']))
            if len(ids)!=2 or ids[0]==ids[1] or key not in ids or any(item not in objects or item in supports for item in ids):
                continue
            obj=objects[key]
            if obj.get('geometry_type')!='mesh' or not obj.get('preview_bounds_size_m') or axis not in (0,1,2):
                continue
            a,b=(_uv(item) for item in view['reference_uv'])
            c,d=(_uv(item) for item in view['parallel_uv'])
            length=np.linalg.norm(b-a)
            if min(length,np.linalg.norm(d-c))<.06:
                continue
            unit=(b-a)/length
            p,q=(_uv(item) for item in view['centers_uv'])
            u0,u1=(float((point-a)@unit) for point in (p,q))
            if any(np.linalg.norm(point-a-u*unit)>.024 for point,u in ((p,u0),(q,u1))):
                continue
            vanish=np.cross(np.cross(np.r_[a,1.],np.r_[b,1.]),np.cross(np.r_[c,1.],np.r_[d,1.]))
            if np.linalg.norm(vanish)<1e-8:
                continue
            if abs(vanish[2])<1e-6:
                ratio=abs(u1-u0)/length
            else:
                origin=float((vanish[:2]/vanish[2]-a)@unit)
                points=np.asarray([0.,length,u0,u1])-origin
                if np.min(np.abs(points))<.05 or np.any(points>0) and np.any(points<0):
                    continue
                a0,b0,p0,q0=1/points
                ratio=abs((q0-p0)/(b0-a0))
            if not .18<ratio<12:
                continue
            votes.setdefault((ids,key,axis),{})[camera]=(ratio,ref)
        except (IndexError,KeyError,TypeError,ValueError):
            continue
    results=[]
    seen=set()
    for (ids,key,axis),group in votes.items():
        ratios,refs=zip(*group.values())
        median=float(np.median(ratios))
        if len(refs)<2 or max(ratios)-min(ratios)>.20*median or ids in seen:
            continue
        seen.add(ids)
        unit=objects[key]['preview_bounds_size_m'][axis]
        results.append({'objects':list(ids),'reference_object':key,'reference_axis':axis,'spacing_m':median*unit,
                        'tolerance_m':max(.025,unit*(max(ratios)-min(ratios)+.12*median)),
                        'frame_refs':list(refs)})
    return results


def apply_line_spacing(table, alignment, objects, supports, locked_positions):
    """Move the free end and its supported objects along the inferred common line."""
    used=set()
    for row in alignment.get('line_spacings',[]):
        anchor=row['reference_object']
        other=next(key for key in row['objects'] if key!=anchor)
        if other in locked_positions or other in alignment['objects'] or set(row['objects'])&used:
            continue
        fixed=np.asarray(objects[anchor]['pose']['position_m'][:2])
        current=np.asarray(objects[other]['pose']['position_m'][:2])
        direction=current-fixed
        length=np.linalg.norm(direction)
        if length<.01:
            continue
        orientation=np.asarray(objects[anchor]['pose']['quaternion_wxyz'])
        axis=Rotation.from_quat(orientation[[1,2,3,0]]).as_matrix()[:,row['reference_axis']]
        if abs(axis[2])>.2 or abs(axis[:2]@direction/length)<.85:
            continue
        target=fixed+direction*(row['spacing_m']/length)
        if np.any(np.abs(target-np.asarray(table['position_m'][:2]))>=np.asarray(table['size_m'][:2])/2):
            continue
        move=target-current
        for key,obj in objects.items():
            ancestor=key
            while ancestor in supports and ancestor!=other:
                ancestor=supports[ancestor]
            if ancestor==other and key not in locked_positions:
                current_pose=obj['pose']['position_m']
                current_pose[:2]=(np.asarray(current_pose[:2])+move).tolist()
                yield key,row['frame_refs']
        used.update(row['objects'])


def compare(table, alignment, settled):
    """Compare settled footprint centers and all evidenced spacings in tabletop units."""
    observed=alignment.get('objects',{})
    size=np.asarray(table['size_m'][:2])
    checks=[]
    for key,reference in observed.items():
        if key not in settled:
            continue
        actual=(np.asarray(settled[key]['position_m'][:2])-table_xy(table,[0,0]))/size
        expected=np.asarray(reference['table_uv'])
        error=float(np.linalg.norm(actual-expected))
        checks.append({'object_id':key,'expected_uv':expected.tolist(),'settled_uv':actual.tolist(),
                       'error':error,'tolerance':reference['tolerance'],'passed':error<=reference['tolerance'],
                       'frame_refs':reference['frame_refs']})
    pairs=[]
    for first,second in itertools.combinations(checks,2):
        error=float(np.linalg.norm((np.asarray(first['settled_uv'])-second['settled_uv'])-
                                   (np.asarray(first['expected_uv'])-second['expected_uv'])))
        tolerance=first['tolerance']+second['tolerance']
        pairs.append({'objects':[first['object_id'],second['object_id']], 'error':error,
                      'tolerance':tolerance,'passed':error<=tolerance,
                      'estimated_spacing_m':float(np.linalg.norm((np.asarray(first['expected_uv'])-second['expected_uv'])*size)),
                      'simulated_spacing_m':float(np.linalg.norm((np.asarray(first['settled_uv'])-second['settled_uv'])*size))})
    for row in alignment.get('line_spacings',[]):
        first,second=row['objects']
        if first not in settled or second not in settled:
            continue
        actual=float(np.linalg.norm(np.asarray(settled[first]['position_m'][:2])-settled[second]['position_m'][:2]))
        error=abs(actual-row['spacing_m'])
        pairs.append({'objects':row['objects'],'error':error,'tolerance':row['tolerance_m'],
                      'passed':error<=row['tolerance_m'],'estimated_spacing_m':row['spacing_m'],
                      'simulated_spacing_m':actual,'source':'object_mesh_span','frame_refs':row['frame_refs']})
    return {'status':'mismatch' if any(not row['passed'] for row in [*checks,*pairs]) else 'checked' if checks or pairs else 'unmeasured',
            'scale_basis':'scene_table_dimensions','positions':checks,'spacings':pairs}
