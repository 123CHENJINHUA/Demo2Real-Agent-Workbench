"""Continuous replay fitting and scene-relative adjustment for arbitrary planned phases."""
import numpy as np
from scipy.spatial.transform import Rotation

from modules.motion_replay.inspect import JOINTS
from modules.motion_replay.kinematics import tool_jacobians
from modules.motion_replay.timing import recorded_timing
from modules.rig_binding import joint_limits


def fit(root, arms, tape):
    times=tape['timestamps_s']-tape['timestamps_s'][0]
    if len(times)<2 or not np.isfinite(times).all() or np.any(np.diff(times)<=0) or times[-1]>180:
        raise ValueError("录制轨迹时长或采样时间超出离线求解范围")
    joints=tape['joint_positions_rad']
    gaps=tape['gripper_feedback']
    if joints.shape!=(len(times),len(arms),len(JOINTS)) or gaps.shape!=(len(times),len(arms)):
        raise ValueError("录制关节或夹爪通道与场景不匹配")
    ticks=np.linspace(0,times[-1],int(np.ceil(times[-1]*15))+1)
    channels=np.column_stack((joints.reshape(len(times),-1),gaps))
    fitted=np.stack([np.interp(ticks,times,channel) for channel in channels.T],axis=1)
    reference=fitted[:,:-len(arms)].reshape(len(ticks),len(arms),len(JOINTS))
    limits=np.asarray([[bounds[joint] for joint in JOINTS]
        for bounds in (joint_limits(root/arm['urdf_path'],JOINTS) for arm in arms)])
    reference=np.clip(reference,limits[:,:,0],limits[:,:,1])
    kinematics=[tool_jacobians(root/arm['urdf_path'],arm['base_pose'],reference[:,index],full=True)
                for index,arm in enumerate(arms)]
    # Convert nearly closed demonstrated gripper feedback to a contact-seeking close request.
    low,high=np.percentile(gaps,[15,90],axis=0)
    feedback=fitted[:,-len(arms):]
    openings=np.where(feedback<low+.34*(high-low),0.,np.clip(feedback,0.,.09))
    return {'times_s':ticks,'reference_rad':reference,'gripper_opening_m':openings,
            'recorded_gripper_m':np.clip(feedback,0.,.09),
            'limits_rad':limits,'inverse':np.stack([inverse for _,inverse in kinematics],axis=1),
            'tool_m':np.stack([tool for tool,_ in kinematics],axis=1),'arms':[arm['id'] for arm in arms]}


def actions(model, phases, scene_offsets, adjustments, *, retarget=True, start_offset_m=None, offset_window_s=None):
    reference=model['reference_rad']
    if not retarget:
        return reference.copy()
    offsets=np.zeros((len(phases)+1,len(model['arms']),6))
    for index,phase in enumerate(phases):
        translation=np.asarray(scene_offsets.get(phase['anchor'],[0.,0.,0.]))+phase['adjustment_m']+adjustments[index,:3]
        rotation=0.14*np.deg2rad(np.asarray(phase['rotation_adjustment_deg'])+adjustments[index,3:])
        delta=np.r_[translation,rotation]
        for arm in phase['arms']:
            offsets[index+1,model['arms'].index(arm)]=delta
    knots=[0.,*(phase['end_fraction'] for phase in phases)]
    elapsed=model['times_s']/model['times_s'][-1]
    shift=np.stack([np.interp(elapsed,knots,axis) for axis in offsets.reshape(len(knots),-1).T],axis=1)
    shift=shift.reshape(len(elapsed),len(model['arms']),6)
    if start_offset_m is not None:
        if offset_window_s is None:
            progress=np.clip(elapsed/phases[0]['end_fraction'],0.,1.)
            weight=1.-progress**2*(3.-2.*progress)
        else:
            progress=np.interp(model['times_s'],offset_window_s,[0.,1.,1.,0.],left=0.,right=0.)
            weight=progress**2*(3.-2.*progress)
        shift[:,:,:3]+=weight[:,None,None]*start_offset_m
    commands=reference+np.einsum('taij,taj->tai',model['inverse'],shift)
    bounds=model['limits_rad']
    return np.clip(commands,bounds[:,:,0],bounds[:,:,1])


def clearance_window(model, phases, fault):
    if fault.get('initial_window'):
        return None
    duration=model['times_s'][-1]
    phase=fault['phase']
    start=phases[phase-1]['end_fraction']*duration if phase else 0.
    end=phases[phase]['end_fraction']*duration
    contact=fault['time_s']
    lead=min(.65,(contact-start)*.8)
    release=min(.6,(end-contact)*.5)
    if lead<=0 or release<=0:
        return None
    return [float(contact-lead),float(contact-lead/3),float(end-release),float(end)]


def approach_offsets(model, fault, robots, pose, size):
    """Propose bounded translations for the arm involved in a measured contact."""
    arm_path=fault['actors'][0]
    arm=next((robot['id'] for robot in robots if arm_path.startswith(robot['root_path']+'/')),None)
    if arm not in model['arms'] or not pose:
        return []
    index=model['arms'].index(arm)
    tool=model['tool_m'][:,index]
    sample=0 if fault.get('initial_window') else int(np.searchsorted(model['times_s'],fault['time_s']))
    sample=min(sample,len(tool)-1)
    center=np.asarray(pose['position_m'])
    away=tool[sample]-center
    if size is not None:
        rotation=Rotation.from_quat(np.asarray(pose['quaternion_wxyz'])[[1,2,3,0]])
        local=rotation.inv().apply(away)
        half=np.asarray(size)/2
        direction=local-np.clip(local,-half,half)
        if not np.any(direction):
            axis=np.argmin(half-np.abs(local))
            direction[axis]=1. if local[axis]>=0 else -1.
        away=rotation.apply(direction)
    length=np.linalg.norm(away)
    if length<1e-6:
        return []
    unit=away/length
    lateral=tool[max(0,sample-3)]-tool[min(len(tool)-1,sample+3)]
    lateral-=np.dot(lateral,unit)*unit
    lateral_length=np.linalg.norm(lateral)
    deltas=[distance*unit for distance in (.025,.05,.08)]
    if lateral_length>1e-6:
        deltas.extend(.04*unit+sign*.06*lateral/lateral_length for sign in (1.,-1.))
    variants=[]
    for delta in deltas:
        offset=np.zeros((len(model['arms']),3))
        offset[index]=delta
        variants.append(offset)
    return variants


def gripper_openings(model, phases, *, retarget=True):
    openings=model['gripper_opening_m'].copy()
    if not retarget:
        return openings
    elapsed=model['times_s']/model['times_s'][-1]
    start=0.
    for phase in phases:
        end=phase['end_fraction']
        window=(elapsed>start)&(elapsed<=end)
        if start==0.:
            window[0]=True
        for target in phase['grippers']:
            openings[window,model['arms'].index(target['arm'])]=target['opening_m']
        start=end
    return openings
