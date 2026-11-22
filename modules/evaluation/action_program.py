"""Measure frozen task-specific checks against an offline physics trace."""
import numpy as np
from scipy.spatial.transform import Rotation


def phase_window(times, program, duration, phase):
    phases=program['phases']
    start=duration*phases[phase-1]['end_fraction'] if phase else 0.
    end=times[-1] if phase==len(phases)-1 else duration*phases[phase]['end_fraction']
    return (times>=start)&(times<=end),end


def assess(trace, program, acceptance, sizes, duration, *, through_phase=None):
    indices={name:index for index,name in enumerate(trace['object_ids'])}
    times=trace['times_s']
    checks=[]
    for rule in acceptance['checks']:
        kind=rule['kind']
        phase=rule['phase']
        evaluated=through_phase is None or phase<=through_phase
        interval,window_end=phase_window(times,program,duration,phase)
        tail=interval & (times>=window_end-.5)
        measured=None
        passed=False
        correction=None
        angular_correction=None
        region_sample=None
        if evaluated and kind!='unsupported' and interval.any():
            if kind!='gripper_gap':
                index=indices[rule['object_id']]
                points=trace['object_m'][:,index]
                if rule['relative_to'] is not None:
                    target=indices[rule['relative_to']]
                    axes=Rotation.from_quat(trace['object_wxyz'][:,target][:,[1,2,3,0]])
                    points=axes.inv().apply(points-trace['object_m'][:,target])
            if kind in ('travel','rise'):
                change=points[interval]-points[interval][0]
                measured=float((change[:,2] if kind=='rise' else np.linalg.norm(change,axis=1)).max())
                passed=measured>=rule['value']
            elif kind=='region' and tail.any():
                low,high=np.asarray(rule['lower']),np.asarray(rule['upper'])
                positions=points[tail]
                deltas=np.clip(positions,low,high)-positions
                errors=np.max(np.abs(deltas),axis=1)
                worst=int(np.argmax(errors))
                measured=float(errors[worst])
                passed=measured==0.
                if not passed:
                    delta=deltas[worst]
                    correction=(axes[tail][worst].apply(delta) if rule['relative_to'] is not None else delta).tolist()
                    region_sample={'time_s':float(times[tail][worst]),'position_m':positions[worst].tolist(),
                                   'lower_m':rule['lower'],'upper_m':rule['upper'],'correction_local_m':delta.tolist()}
            elif kind=='stable' and tail.sum()>=2:
                measured=float(np.linalg.norm(points[tail]-points[tail][-1],axis=1).max())
                passed=measured<=rule['value']
            elif kind=='clearance' and tail.any() and rule['object_id'] in sizes:
                orientation=Rotation.from_quat(trace['object_wxyz'][tail,index][:,[1,2,3,0]])
                deltas=trace['finger_m'][tail]-trace['object_m'][tail,index,None]
                local=np.einsum('tij,tfj->tfi',orientation.inv().as_matrix(),deltas)
                measured=float(np.linalg.norm(np.maximum(np.abs(local)-np.asarray(sizes[rule['object_id']])/2,0.),axis=-1).min())
                passed=measured>=rule['value']
            elif kind=='gripper_gap' and tail.any():
                gaps=np.abs(trace['actual_joints'][tail,:,7]-trace['actual_joints'][tail,:,8])
                measured=float(gaps.max(axis=1).min())
                passed=measured>=rule['value']
            elif kind=='orientation' and tail.any():
                actual_world=Rotation.from_quat(trace['object_wxyz'][tail,index][:,[1,2,3,0]])
                actual=actual_world
                if rule['relative_to'] is not None:
                    actual=axes[tail].inv()*actual
                expected=Rotation.from_euler('xyz',rule['lower'],degrees=True)
                measured=float(np.rad2deg((expected.inv()*actual).magnitude()).max())
                passed=measured<=rule['value']
                if not passed:
                    desired=axes[tail][-1]*expected if rule['relative_to'] is not None else expected
                    angular_correction=np.rad2deg((desired*actual_world[-1].inv()).as_rotvec()).tolist()
        checks.append({'criterion':rule['criterion'],'phase':phase,'kind':kind,'description':rule['description'],
                       'object_id':rule['object_id'],'relative_to':rule['relative_to'],
                       'passed':bool(passed),'measured':measured,'threshold':rule['value'],'evaluated':evaluated})
        if correction is not None:
            checks[-1]['correction_world_m']=correction
            checks[-1]['region_sample']=region_sample
        if angular_correction is not None:
            checks[-1]['correction_world_deg']=angular_correction
    return {'passed':all(row['passed'] for row in checks),'checks':checks,'score':sum(row['passed'] for row in checks)}
