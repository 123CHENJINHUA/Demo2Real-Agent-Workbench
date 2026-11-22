"""Review recorded simulator trajectories without starting another rollout."""
from bisect import bisect_left

import numpy as np

from modules.evaluation.action_program import assess, phase_window


def _lift_samples(trace, samples, check, tabletop):
    index=trace['object_ids'].tolist().index(check['object_id'])
    height=trace['object_m'][:,index,2]
    peak=int(samples[np.argmax(height[samples])])
    rise=float(height[peak]-height[samples[0]])
    below=samples[(samples>peak)&(height[samples]<tabletop)]
    lost=(int(below[0]) if below.size and height[samples[0]]>tabletop and rise>=check['threshold'] else None)
    return rise,peak,lost


def failed_lift(trace, program, table, duration, result, phase):
    """Find a measured loss of height explaining a failed same-stage check."""
    checks=[check for check in result['checks'] if check['phase']==phase and check.get('measured') is not None]
    failed_objects={check['object_id'] for check in checks if not check['passed']}
    tabletop=table['position_m'][2]+table['size_m'][2]/2
    window,_=phase_window(trace['times_s'],program,duration,phase)
    samples=np.flatnonzero(window)
    for check in checks:
        if (check['kind']=='rise' and check['relative_to'] is None and check['passed']
                and check['object_id'] in failed_objects and samples.size):
            rise,peak,lost=_lift_samples(trace,samples,check,tabletop)
            if lost is not None:
                times=trace['times_s']
                return {'kind':'lift_loss','phase':phase,'object_id':check['object_id'],
                        'time_s':float(times[lost]),'peak_s':float(times[peak]),
                        'peak_rise_m':rise,'tabletop_m':tabletop}
    return None


def review_phase(trace, program, acceptance, sizes, table, duration, phase, recorded_checks):
    """Recompute the observed checks and sample the poses that explain them."""
    phases=program['phases']
    times=trace['times_s']
    window,_=phase_window(times,program,duration,phase)
    samples=np.flatnonzero(window)
    review={'phase':phase,'name':phases[phase]['name']}
    if not samples.size:
        return review | {'recorded_samples':0}

    rules=[rule for rule,check in zip(acceptance['checks'],recorded_checks)
           if rule['phase']==phase and check.get('evaluated',True) and rule['kind']!='unsupported']
    checks=assess(trace,program,{'checks':rules},sizes,duration)['checks']
    review['checks']=[{k:v for k,v in check.items() if k not in ('description','evaluated')} for check in checks]
    subjects=list(dict.fromkeys(rule['object_id'] for rule in rules if rule['object_id'] is not None))
    references=list(dict.fromkeys(rule['relative_to'] for rule in rules if rule['relative_to'] is not None))
    anchor=phases[phase].get('anchor')
    keys=trace['object_ids'].tolist()
    objects=list(dict.fromkeys(subjects+references+([anchor] if anchor in keys else [])))[:4]
    first,last=int(samples[0]),int(samples[-1])
    frames={'start':first,'last':last}
    for check in checks:
        if region:=check.get('region_sample'):
            frames.setdefault('region_worst',int(np.searchsorted(times,region['time_s'])))

    tabletop=table['position_m'][2]+table['size_m'][2]/2
    rises=[]
    for check in checks:
        if check['kind']!='rise' or check['relative_to'] is not None:
            continue
        key=check['object_id']
        height_gain,peak,lost=_lift_samples(trace,samples,check,tabletop)
        rise={'object_id':key,'peak_rise_m':height_gain,'peak_s':float(times[peak])}
        frames['peak_'+key]=peak
        if lost is not None:
            frames['below_table_'+key]=lost
            rise['below_table_after_peak_s']=float(times[lost])
        rises.append(rise)

    poses=trace['object_m']
    rotations=trace['object_wxyz']
    gaps=None
    if 'actual_joints' in trace:
        joints=trace['actual_joints']
        gaps=np.abs(joints[:,:,7]-joints[:,:,8])

    def snapshot(frame):
        world={}
        for key in objects:
            index=keys.index(key)
            alignment=abs(float(np.dot(rotations[first,index],rotations[frame,index])))
            world[key]={'xyz_m':poses[frame,index].round(5).tolist(),
                        'rotation_from_start_deg':round(float(np.degrees(2*np.arccos(np.clip(alignment,0.,1.)))),2)}
        data={'time_s':round(float(times[frame]),4),'world_objects':world}
        if gaps is not None and 'commanded_gripper_m' in trace:
            commanded=trace['commanded_gripper_m']
            data.update(gripper_gap_m=gaps[frame].round(5).tolist(),
                        gripper_command_m=commanded[min(frame,len(commanded)-1)].round(5).tolist())
        return data

    return review | {'tabletop_m':tabletop,'world_rises':rises,
                     'samples':{name:snapshot(frame) for name,frame in frames.items()}}


class EarlyFall:
    """Track a drop below the tabletop before the required climb has occurred."""
    def __init__(self, object_ids, program, acceptance, table, duration):
        self.ends=[step['end_fraction']*duration for step in program['phases']]
        self.top=table['position_m'][2]+table['size_m'][2]/2
        keys={key:i for i,key in enumerate(object_ids)}
        checks=acceptance['checks']
        self.rules=[]
        for rule in checks:
            if rule['kind']!='rise' or rule['relative_to'] is not None:
                continue
            key,phase=rule['object_id'],rule['phase']
            # An earlier transport may intentionally take this object below the table.
            if any(item['object_id']==key and item['phase']<phase and item['kind'] in ('region','travel','rise')
                   for item in checks):
                continue
            self.rules.append((key,keys[key],self.ends[phase],rule['value']))
        self.active=None

    def observe(self, time_s, points):
        if self.active is None:
            self.active=[(key,index,end,points[index][2]+rise) for key,index,end,rise in self.rules
                         if points[index][2]>self.top]
        for i in range(len(self.active)-1,-1,-1):
            key,index,end,target=self.active[i]
            height=points[index][2]
            if time_s>end or height>=target:
                self.active.pop(i)
            elif height<self.top:
                return {'object_id':key,'time_s':float(time_s),'phase':bisect_left(self.ends,time_s),
                        'height_m':float(height),'tabletop_m':self.top}
        return None


def first_fall(trace, program, acceptance, table, duration):
    detector=EarlyFall(trace['object_ids'],program,acceptance,table,duration)
    for time_s,points in zip(trace['times_s'],trace['object_m']):
        hit=detector.observe(time_s,points)
        if hit:
            return hit
        if not detector.active:
            break
    return None


class InitialSupport:
    """Detect loss of a declared support before the first intended manipulation."""
    def __init__(self, object_ids, program, supports, sizes, duration):
        approach=program['phases'][0]
        targets={approach.get('anchor')} | {point['relative_to'] for point in approach.get('waypoints',[])}
        ids={key:index for index,key in enumerate(object_ids)}
        self.relations=[]
        if approach.get('contact_free'):
            for key,support in supports.items():
                if key in targets and key in ids and support in ids:
                    scale=max(sizes.get(key) or [.05])
                    tolerance=max(.004,.20*scale)
                    self.relations.append((key,support,ids[key],ids[support],tolerance))
        self.until=approach['end_fraction']*duration
        self.start=None

    def observe(self, time_s, points, read_orientations):
        if not self.relations or time_s>self.until:
            return None
        if self.start is None:
            orientations=read_orientations()
            self.start=[(points[index]-points[support_index],orientations[index].copy())
                        for _,_,index,support_index,_ in self.relations]
            return None
        orientations=None
        for (key,support,index,support_index,tolerance),(origin,attitude) in zip(self.relations,self.start):
            drift=points[index]-points[support_index]-origin
            distance=float(np.linalg.norm(drift))
            if distance<=tolerance/2:
                continue
            if orientations is None:
                orientations=read_orientations()
            agreement=np.clip(abs(np.dot(attitude,orientations[index])),0.,1.)
            rotation=float(np.rad2deg(2.*np.arccos(agreement)))
            if distance>tolerance or rotation>30.:
                return {'kind':'initial_support_loss','phase':0,'object_id':key,'support_id':support,
                        'time_s':float(time_s),'relative_drift_m':distance,'height_change_m':float(drift[2]),
                        'rotation_deg':rotation}
        return None


def first_support_loss(trace, program, supports, sizes, duration):
    detector=InitialSupport(trace['object_ids'],program,supports,sizes,duration)
    if not detector.relations:
        return None
    for time_s,points,orientations in zip(trace['times_s'],trace['object_m'],trace['object_wxyz']):
        if time_s>detector.until:
            break
        if loss:=detector.observe(time_s,points,lambda:orientations):
            return loss
    return None


def first_motion_fault(result):
    events=[result.get(key) for key in ('unexpected_contact','initial_support_loss','early_fall')]
    return min((event for event in events if event),key=lambda event:event['time_s'],default=None)


class TrialScreen:
    """Stop a training rollout on a confirmed fall or a completed failed phase."""
    def __init__(self, object_ids, program, acceptance, table, sizes, duration, *, contact_rows=None, robot_roots=(), supports=None):
        self.detector=EarlyFall(object_ids,program,acceptance,table,duration)
        self.support=InitialSupport(object_ids,program,supports or {},sizes,duration)
        self.program,self.acceptance,self.sizes,self.duration=program,acceptance,sizes,duration
        self.next_phase=0
        self.through_phase=None
        self.termination=None
        self.early_fall=None
        self.contact_rows=contact_rows
        self.robots=tuple(path.rstrip('/')+'/' for path in robot_roots)
        self.objects={'/World/Objects/'+key:key for key in object_ids}
        self.contact_cursor=0
        self.unexpected_contact=None
        self.initial_support_loss=None

    def inspect_contacts(self):
        rows=self.contact_rows()
        for at,first,second,impulse in rows[self.contact_cursor:]:
            phase=min(bisect_left(self.detector.ends,at),len(self.detector.ends)-1)
            if not self.program['phases'][phase].get('contact_free') or impulse<.0005:
                continue
            for robot,obj in ((first,second),(second,first)):
                if obj in self.objects and robot.startswith(self.robots):
                    return {'kind':'unexpected_robot_contact','phase':phase,'time_s':at,'actors':[robot,obj],
                            'object_id':self.objects[obj],'impulse_ns':impulse,
                            'initial_window':at<=.1}
        self.contact_cursor=len(rows)
        return None

    def observe(self, time_s, points, read_trace):
        if self.contact_rows:
            self.unexpected_contact=self.inspect_contacts()
            if self.unexpected_contact:
                self.through_phase=self.unexpected_contact['phase']-1
                self.termination=self.unexpected_contact
                return True
        self.initial_support_loss=self.support.observe(time_s,points,lambda:read_trace()['object_wxyz'][-1])
        if self.initial_support_loss:
            self.through_phase=-1
            self.termination=self.initial_support_loss
            return True
        self.early_fall=self.detector.observe(time_s,points)
        if self.early_fall:
            phase=self.early_fall['phase']
            self.through_phase=phase-1
            self.termination={'kind':'early_fall','phase':phase,'time_s':time_s}
            return True
        # The final phase keeps the complete rollout's settling window.
        ends=self.detector.ends
        while self.next_phase<len(ends)-1 and time_s>=ends[self.next_phase]:
            phase=self.next_phase
            self.next_phase+=1
            rules=[rule for rule in self.acceptance['checks'] if rule['phase']==phase and rule['kind']!='unsupported']
            if not rules:
                continue
            result=assess(read_trace(),self.program,{'checks':rules},self.sizes,self.duration)
            if not result['passed']:
                self.through_phase=phase
                self.termination={'kind':'phase_check_failed','phase':phase,'time_s':time_s}
                return True
        return False


def eligible_for_validation(result):
    observed=[check for check in result['checks'] if check['kind']!='unsupported']
    return bool(observed) and first_motion_fault(result) is None and all(check['passed'] for check in observed)


def measurable_prefix(result, phase_count):
    """Count the measurable phases completed before a failed check or an early fall."""
    score=0
    fault=first_motion_fault(result)
    for phase in range(phase_count):
        if fault and phase>=fault['phase']:
            break
        checks=[rule for rule in result['checks'] if rule['phase']==phase and rule['kind']!='unsupported'
                and rule.get('evaluated',True)]
        if any(not check['passed'] for check in checks):
            break
        score+=bool(checks)
    return score


def search_focus(results, phase_count):
    failures=[]
    for result in results:
        fault=first_motion_fault(result)
        if fault:
            failures.append(fault['phase'])
        failures.extend(check['phase'] for check in result['checks'] if check['kind']!='unsupported'
                        and check.get('evaluated',True) and not check['passed'])
    return min(failures,default=phase_count-1)


def partial_progress(result, phase):
    """Measure progress on the first failed stage before its checks pass outright."""
    progress=[]
    for check in result['checks']:
        measured=check.get('measured')
        if check['phase']!=phase or measured is None or not check.get('evaluated',True):
            continue
        kind,threshold=check['kind'],check['threshold']
        if check['passed']:
            progress.append(1.)
        elif kind in ('travel','rise','clearance','gripper_gap'):
            progress.append(max(0.,measured)/threshold)
        elif kind=='region':
            progress.append(1. / (1.+measured/.02))
        else:
            progress.append(threshold/measured)
    return sum(progress)/len(progress) if progress else 0.
