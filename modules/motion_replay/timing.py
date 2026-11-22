"""Find timed motion evidence in recorded joint and gripper feedback."""

import numpy as np
from scipy.spatial.transform import Rotation

from modules.motion_replay.kinematics import ToolKinematics


def recorded_timing(tape):
    """Summarize measured opening transitions to anchor the inferred phase boundaries."""
    times=tape['timestamps_s']-tape['timestamps_s'][0]
    if len(times)<2 or times[-1]<=0.:
        return {}
    earlier=np.searchsorted(times,times-.3,side='left')
    changes={}
    for arm,width in zip(tape['arms'].tolist(),tape['gripper_feedback'].T):
        change=width-width[earlier]
        events=[]
        for direction,sign in (('opening',1),('closing',-1)):
            significant=sign*change>=max(.004,.2*np.ptp(width))
            bounds=np.flatnonzero(np.diff(np.r_[False,significant,False]))
            for first,last in bounds.reshape(-1,2):
                start=earlier[first]
                end=last-1
                events.append({'direction':direction,'start_s':round(float(times[start]),3),
                               'end_s':round(float(times[end]),3),
                               'start_fraction':round(float(times[start]/times[-1]),4),
                               'end_fraction':round(float(times[end]/times[-1]),4),
                               'change_mm':round(float((width[end]-width[start])*1000),1)})
        changes[arm]=sorted(events,key=lambda event:event['start_s'])
    return {'duration_s':round(float(times[-1]),3),'measured_gripper_changes':changes}


def scene_motion(root, model_paths, times, joints, openings):
    """Collect contact candidates in robot-base coordinates, independent of the scene."""
    elapsed=times-times[0]
    arms=list(model_paths)
    timing=recorded_timing({'timestamps_s':times,'arms':np.asarray(arms),'gripper_feedback':openings})
    references={}
    events=[]
    base={'position_m':[0.,0.,0.],'quaternion_wxyz':[1.,0.,0.,0.]}
    for axis,(arm,reference) in enumerate(model_paths.items()):
        path=(root/reference).resolve()
        path.relative_to(root)
        tool=ToolKinematics(path,base)
        points,rotations,_=tool.tool(joints[:,axis])
        previous=np.searchsorted(elapsed,elapsed-.10,side='left')
        delta=elapsed-elapsed[previous]
        speed=np.linalg.norm(points-points[previous],axis=1)/np.maximum(delta,1e-6)
        rotation=Rotation.from_matrix(rotations)
        angular=(rotation[previous].inv()*rotation).magnitude()/np.maximum(delta,1e-6)
        quiet=(speed<.018)&(angular<.24)&(delta>.075)
        bounds=np.flatnonzero(np.diff(np.r_[False,quiet,False])).reshape(-1,2)
        moving=np.maximum.accumulate((speed>=.018)|(angular>=.24))
        pauses=[(a,b-1) for a,b in bounds if elapsed[b-1]-elapsed[a]>=.25 and a>0 and moving[a-1]]
        readings=[]
        for move in timing['measured_gripper_changes'][arm]:
            start,end=np.searchsorted(elapsed,[move['start_s'],move['end_s']])
            end=min(len(elapsed)-1,int(end))
            if start>end:
                continue
            widths=openings[start:end+1,axis]
            peak=widths.min() if move['direction']=='closing' else widths.max()
            index=int(start+np.flatnonzero(np.abs(widths-peak)<=.00075)[0])
            readings.append((move['direction'],index,int(start),end))
        readings.extend(('pause',(a+b)//2,a,b) for a,b in pauses)
        references[arm]={'urdf_path':reference,'base_frame':'urdf_root'}
        counts={}
        for kind,index,start,end in sorted(readings,key=lambda item:item[1]):
            counts[kind]=counts.get(kind,0)+1
            q=rotation[index].as_quat()
            events.append({'id':f'{arm}_{kind}_{counts[kind]}','arm':arm,'kind':kind,'frame_index':int(index),
                           'time_s':round(float(elapsed[index]),4),'window_s':elapsed[[start,end]].round(4).tolist(),
                           'position_in_base_m':points[index].round(6).tolist(),'tool_quaternion_wxyz':q[[3,0,1,2]].round(7).tolist(),
                           'gripper_width_m':round(float(openings[index,axis]),5)})
    events.sort(key=lambda event:event['time_s'])
    return {'duration_s':float(elapsed[-1]),'arms':references,'events':events,
            'reference_point':'URDF gripper_base origin plus 0.13 m along local +Z',
            'scope':'recorded_motion_candidates_no_contact_or_world_calibration_claimed'}


def scene_keyframes(events, budget):
    """Select distinct times, covering different arms and motion types when possible."""
    closes=[event for event in events if event['kind']=='closing']
    first_per_arm=list({event['arm']:event for event in reversed(closes)}.values())
    openings=[event for event in reversed(events) if event['kind']=='opening']
    pauses=sorted((event for event in events if event['kind']=='pause'),
                  key=lambda event:event['window_s'][1]-event['window_s'][0],reverse=True)
    selected=[]
    for event in [*first_per_arm,*openings[:1],*pauses,*closes,*openings[1:]]:
        if all(abs(event['time_s']-prior['time_s'])>.16 for prior in selected):
            selected.append(event)
        if len(selected)>=budget:
            break
    return sorted(selected,key=lambda event:event['time_s'])


