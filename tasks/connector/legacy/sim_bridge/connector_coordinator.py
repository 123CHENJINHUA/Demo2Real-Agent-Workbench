"""Continuous object-relative feedback around the demonstrated cooperative path.

Only arm targets are returned. Object poses are measurements, never commands.
The female reference follows a frozen demonstration path, so moving a grasped
object cannot redefine the task goal and make the controller chase itself.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from sim_bridge.connector_planner import align_axis, blend, insertion_error, TABLE


class RestartCooperation(Exception):
    """A recovered grasp invalidates the previous cooperative phase cursor."""


def smooth(value):
    u = np.clip(value,0.,1.)
    return float(u*u*(3.-2.*u))


def limit_pose(previous, wanted, dt, speed=.08, angular_speed=.6):
    target = wanted.copy()
    delta = wanted[:3,3]-previous[:3,3]
    target[:3,3] = previous[:3,3]+delta*min(1.,speed*dt/max(np.linalg.norm(delta),1e-12))
    rotation = Rotation.from_matrix(wanted[:3,:3]@previous[:3,:3].T).as_rotvec()
    rotation *= min(1.,angular_speed*dt/max(np.linalg.norm(rotation),1e-12))
    target[:3,:3] = Rotation.from_rotvec(rotation).as_matrix()@previous[:3,:3]
    return target


class CooperativeFeedback:
    """Measured grasp compensation, relative alignment, guarded insertion/recovery."""
    def __init__(self, tools, objects, axis_only=False, keyed=False):
        self.keyed=keyed
        self.axis_only=axis_only and not keyed
        self.start_tools=[p.copy() for p in tools]
        self.initial_grips = [np.linalg.inv(tools[a])@objects[a] for a in range(2)]
        self.grips = [p.copy() for p in self.initial_grips]
        self.commands = [p.copy() for p in tools]
        self.translation_integral = np.zeros((2,3))
        self.rotation_integral = np.zeros((2,3))
        self.inserting = False
        self.depth_command = -.012
        self.recovering = False
        self.recoveries = 0
        self.recovery_elapsed = 0.
        self.jam_elapsed = 0.
        self.recovery_relative = None
        self.recovery_depth = None
        if keyed:
            from sim_bridge.connector_keying import profile
            self.key_config=profile()
            self.reset_key_reference(objects)

    def reset_key_reference(self,objects,target_roll=None):
        from sim_bridge.connector_keying import choose_roll,wrap_pi
        relative=np.linalg.inv(objects[1])@objects[0]
        roll=float(np.arctan2(relative[2,1],relative[1,1]))
        self.key_target_roll=choose_roll(relative,self.key_config) if target_roll is None else float(target_roll)
        self.key_roll_correction=float(wrap_pi(self.key_target_roll-roll))

    def update(self, reference, tools, objects, progress, pair_force, dt=1/30):
        if self.keyed:
            from sim_bridge.connector_keying import keyed_error,keyed_reference
            error=keyed_error(objects,self.key_target_roll,self.key_config)
            source_reference=reference.copy()
            reference=keyed_reference(reference,self.initial_grips,self.key_roll_correction,smooth(progress/.55))
        else:error=insertion_error(objects)
        if pair_force>12.:
            raise RuntimeError(f'Cooperative contact hard stop: {pair_force:.3f} N')
        for a in range(2):
            self.grips[a] = blend(self.grips[a],np.linalg.inv(tools[a])@objects[a],.2)
        nominal = [reference[a]@self.initial_grips[a] for a in range(2)]
        relative = np.linalg.inv(nominal[1])@nominal[0]
        weight = smooth(progress/.65)
        aligned = relative.copy()
        aligned[:3,:3] = (Rotation.from_euler('x',self.key_target_roll).as_matrix() if self.keyed else
                          align_axis(relative[:3,:3],np.array([1.,0.,0.])))
        aligned[1:3,3] = 0.
        desired_relative = blend(relative,aligned,weight)
        # Alignment happens while transporting, before entering the socket.
        nominal_depth = .057-relative[0,3]
        desired_depth = min(nominal_depth,-.012)
        if self.keyed:
            from sim_bridge.connector_keying import keyed_ready
            ready=keyed_ready(error,self.key_config)
        else:ready=error['lateral_error_m']<.0008 and error['angle_rad']<.035
        self.jam_elapsed = self.jam_elapsed+dt if self.inserting and pair_force>3. else 0.
        if not self.recovering and (pair_force>8. or self.jam_elapsed>1.):
            if self.recoveries>=1:
                raise RuntimeError('Cooperative insertion repeated abnormal contact after recovery')
            self.recovering = True
            self.recoveries += 1
            self.recovery_elapsed = 0.
            self.recovery_relative = np.linalg.inv(objects[1])@objects[0]
            self.recovery_depth = min(-.008,error['depth_m']-.008)
        if self.recovering:
            self.recovery_elapsed += dt
            desired_relative = self.recovery_relative.copy()
            desired_depth = self.recovery_depth
            if error['depth_m']<self.recovery_depth+.001 and pair_force<1.:
                self.recovering = False
                self.inserting = False
                self.depth_command = min(-.012,error['depth_m'])
                self.jam_elapsed = 0.
            elif self.recovery_elapsed>3.:
                raise RuntimeError('Contact recovery failed to clear the socket')
        else:
            if not self.inserting and progress>=.70 and nominal_depth>-.025 and ready:
                self.inserting = True
                self.depth_command = min(-.012,error['depth_m'])
            if self.inserting:
                if ready and pair_force<3.:
                    maximum=self.key_config['lock_target_depth_m'] if self.keyed else .0113
                    rate=.003 if self.keyed else .004
                    self.depth_command = min(maximum,self.depth_command+rate*dt)
                desired_depth = self.depth_command
                desired_relative = aligned.copy()
        desired_relative[0,3] = .057-desired_depth
        # One immutable global reference for the female, one prescribed relative
        # goal for the male. Both grips are continuously measured and compensated.
        female_goal=nominal[1].copy()
        if self.keyed:
            # The recorded endpoint already overlaps the unkeyed meshes. Share
            # the required safe spacing/alignment translation between the arms
            # about the recorded object midpoint. This is a prescribed reference
            # deformation, independent of observed object drift, so the female
            # still has an immutable world goal. Otherwise the entire 50+ mm
            # pre-insertion clearance falls on one wrist and exhausts its bound.
            aligned_male=nominal[1]@desired_relative
            female_goal[:3,3]-=.5*(aligned_male[:3,3]-nominal[0][:3,3])
        goals = [objects[1]@desired_relative,female_goal]
        if self.axis_only:
            # The connector proxies are axially symmetric. A dropped cylinder
            # can roll 180 degrees without changing its insertion geometry.
            # Keep approach coordinates in the task frame, not in that free roll.
            goals[0][:3,3]=objects[1][:3,3]+nominal[1][:3,:3]@desired_relative[:3,3]
            male_axis=nominal[1][:3,:3]@desired_relative[:3,0]
            if self.inserting:male_axis=objects[1][:3,0]
            # Choose the free roll that keeps each wrist close to its recorded
            # orientation, rather than keeping an arbitrary post-drop body roll.
            preferred_male=reference[0,:3,:3]@self.grips[0][:3,:3]
            preferred_female=reference[1,:3,:3]@self.grips[1][:3,:3]
            goals[0][:3,:3]=align_axis(preferred_male,male_axis)
            goals[1]=nominal[1].copy()
            goals[1][:3,:3]=align_axis(preferred_female,nominal[1][:3,0])
        targets = []
        saturated = []
        for a,goal in enumerate(goals):
            if pair_force<3. and not self.recovering:
                self.translation_integral[a] = np.clip(self.translation_integral[a]+.12*(goal[:3,3]-objects[a][:3,3]),-.008,.008)
                angular = Rotation.from_matrix(goal[:3,:3]@objects[a][:3,:3].T).as_rotvec()
                self.rotation_integral[a] = np.clip(self.rotation_integral[a]+.08*angular,-.06,.06)
            hand = goal@np.linalg.inv(self.grips[a])
            hand[:3,3] += self.translation_integral[a]
            hand[:3,:3] = Rotation.from_rotvec(self.rotation_integral[a]).as_matrix()@hand[:3,:3]
            correction = hand[:3,3]-reference[a,:3,3]
            length = np.linalg.norm(correction)
            # Bound departure from the demonstrated path instead of abandoning it.
            if length>.060:hand[:3,3] = reference[a,:3,3]+correction*.060/length
            angular = Rotation.from_matrix(hand[:3,:3]@reference[a,:3,:3].T).as_rotvec()
            angle = np.linalg.norm(angular)
            if angle>.6:hand[:3,:3] = Rotation.from_rotvec(angular*.6/angle).as_matrix()@reference[a,:3,:3]
            self.commands[a] = limit_pose(self.commands[a],hand,dt)
            targets.append(self.commands[a].copy())
            saturated.append(bool(length>.060 or angle>.6))
        status = dict(**error,alignment_weight=weight,depth_command_m=float(desired_depth),
                      progress=float(progress),pair_force_N=float(pair_force),recoveries=self.recoveries,
                      recovering=self.recovering,inserting=self.inserting,correction_saturated=saturated)
        if self.keyed:
            status['keyed_alignment_ready']=bool(ready)
            status['key_reference_rotation_rad']=float(.5*self.key_roll_correction*smooth(progress/.55))
            status['original_demo_translation_deviation_m']=np.linalg.norm(np.asarray(targets)[:,:3,3]-source_reference[:,:3,3],axis=-1).tolist()
        return np.array(targets),status


def select_key_candidate(sim,controller,references,joint_references,initial_objects):
    """A failed IK candidate must not prevent checking the other key orientation."""
    from sim_bridge.connector_keying import keyed_reference,wrap_pi
    from sim_bridge.connector_planner import BASES
    candidates=[]
    for roll in np.radians(controller.key_config['roll_candidates_deg']):
        relative=np.linalg.inv(initial_objects[1])@initial_objects[0]
        correction=wrap_pi(roll-np.arctan2(relative[2,1],relative[1,1]))
        probe=keyed_reference(references[-1],controller.initial_grips,correction,1.)
        score=0.;details=[];feasible=True
        for a in range(2):
            qref=joint_references[-1,a]
            try:q=sim.ik.solve_near_demo(probe[a],a,sim.arms[a]['command'],qref)
            except RuntimeError as exc:
                details.append(dict(arm=a,reachable=False,error=str(exc)));feasible=False;break
            actual=sim.ik.fk(q);actual[:3,3]+=BASES[a]
            p_error=float(np.linalg.norm(actual[:3,3]-probe[a,:3,3]));r_error=float(Rotation.from_matrix(actual[:3,:3].T@probe[a,:3,:3]).magnitude())
            score+=float(np.linalg.norm(q-qref)**2)+10000*p_error+50*r_error
            details.append(dict(arm=a,reachable=True,position_error_m=p_error,rotation_error_rad=r_error))
            feasible=feasible and p_error<=.005 and r_error<=.10
        candidates.append(dict(roll_rad=float(roll),score=score if feasible else None,feasible=bool(feasible),ik=details))
    valid=[c for c in candidates if c['feasible']]
    return candidates,min(valid,key=lambda c:c['score']) if valid else None


def run_cooperative_feedback(sim, references, joint_references, output):
    """30 Hz object feedback over the cooperative segment and into insertion."""
    from sim_bridge.connector_planner import insertion_error
    robust=getattr(sim,'robust',None)
    initial_objects=sim.object_poses() if robust is None else robust.observe()['poses']
    keyed=getattr(sim.args,'connector_model','legacy')=='keyed'
    controller = CooperativeFeedback([sim.tool(a) for a in range(2)],initial_objects,axis_only=robust is not None,keyed=keyed)
    if keyed:
        # Choose the two-fold candidate by bounded IK near the source, using
        # estimated grasp geometry only. Never choose by simulator success.
        candidates,chosen=select_key_candidate(sim,controller,references,joint_references,initial_objects)
        import json
        if chosen:controller.reset_key_reference(initial_objects,chosen['roll_rad'])
        (output/'keyed_reference_plan.json').write_text(json.dumps(dict(candidates=candidates,selected=chosen,
            relative_roll_correction_rad=controller.key_roll_correction if chosen else None,
            method='Evaluate both key candidates independently; reject failed IK without relaxing limits'),indent=2))
        if not chosen:raise RuntimeError('Neither keyed roll candidate is reachable near the demonstrated posture; see keyed_reference_plan.json')
    records,targets_log,tools_log,qref_log,reference_log = [],[],[],[],[]
    sim.set_phase('dex_coordination_closed_loop')
    previous = np.array([sim.tool(a) for a in range(2)])

    def control(reference,progress,qref):
        if robust is not None:
            for attempt in range(450):
                sample=robust.observe();action=robust.decide(sample,progress,reference)
                phase_guard=getattr(robust,'phase_guard',None)
                if phase_guard:
                    decision,phase_error=phase_guard.inspect(sample,controller,action,progress)
                    if decision.mode=='abandon':phase_guard.abandon(decision.reason)
                    if decision.mode in ('recapture','unload','reanchor'):
                        phase_guard.recover(decision,sample,controller);continue
                    if decision.mode=='wait':
                        sim.set_phase('phase_recovery_wait')
                        for _ in range(8):sim.step()
                        continue
                    if decision.mode=='hold_complete':
                        from sim_bridge.connector_keying import keyed_ready
                        status=getattr(controller,'phase_last_status',{}).copy();status.update(phase_error,alignment_weight=1.,depth_command_m=float(controller.depth_command),progress=float(progress),pair_force_N=0.,recoveries=controller.recoveries,recovering=False,inserting=controller.inserting,correction_saturated=[False,False],keyed_alignment_ready=keyed_ready(phase_error,controller.key_config),key_reference_rotation_rad=float(.5*controller.key_roll_correction*smooth(progress/.55)),original_demo_translation_deviation_m=np.linalg.norm(np.array([sim.tool(a) for a in range(2)])[:,:3,3]-reference[:,:3,3],axis=-1).tolist())
                        sim.set_phase('phase_hold_complete')
                        for _ in range(8):sim.step()
                        status['simulation_time_s']=sim.time()
                        records.append(status);targets_log.append(np.array([sim.tool(a) for a in range(2)]));tools_log.append([sim.tool(a) for a in range(2)]);qref_log.append(qref);reference_log.append(reference.copy());return status
                if action==0:break
                if action==4:
                    robust.needs_human=True
                    raise RuntimeError('Needs human: object unavailable or outside supported workspace')
                if action in (2,3):
                    if len(robust.recoveries)>=2:raise RuntimeError('Maximum regrasp attempts reached')
                    robust.regrasp(action-2,sample,reference,controller)
                    raise RestartCooperation()
                if sim.phase()!='recovery_wait':sim.set_phase('recovery_wait')
                for _ in range(8):sim.step()
            else:
                if getattr(robust,'phase_guard',None):robust.phase_guard.abandon('Continuous observation/decision wait timeout')
                robust.needs_human=True
                raise RuntimeError('Needs human: recovery observation timeout')
            desired_phase='dex_coordination_closed_loop' if progress<1. else 'cooperative_insert'
            if sim.phase()!=desired_phase:sim.set_phase(desired_phase)
        tools = [sim.tool(a) for a in range(2)]
        objects = sim.object_poses() if robust is None else sample['poses']
        if robust is None and min(p[2,3] for p in objects)<TABLE+.03:
            raise RuntimeError('A connector dropped during cooperative feedback')
        forces = np.asarray(sim.object_contacts.get_contact_force_matrix(dt=1/240))
        targets,status = controller.update(reference,tools,objects,progress,0. if robust is not None else float(np.linalg.norm(forces[0,1])))
        if getattr(robust,'phase_guard',None):controller.phase_last_status=status.copy()
        for a,target in enumerate(targets):
            state = sim.arms[a]
            q = sim.ik.solve_near_demo(target,a,state['command'],qref[a])
            state['command'] += np.clip(q-state['command'],-1./30,1./30)
            state['opening'] = 0.
        for _ in range(8):sim.step()
        status['simulation_time_s'] = sim.time()
        records.append(status)
        targets_log.append(targets)
        tools_log.append([sim.tool(a) for a in range(2)])
        qref_log.append(qref)
        reference_log.append(reference.copy())
        return status

    try:
        restarts=0
        while True:
            try:
                for k,wanted in enumerate(references):
                    progress = k/max(1,len(references)-1)
                    for fraction in (.5,1.):
                        reference = np.array([blend(previous[a],wanted[a],fraction) for a in range(2)])
                        status = control(reference,progress,joint_references[k])
                        while controller.recovering:
                            status = control(reference,progress,joint_references[k])
                    previous = wanted.copy()
                    if k%30==0:
                        print(f'COOPERATIVE step={k}/{len(references)} weight={status["alignment_weight"]:.3f} depth={status["depth_m"]:.4f} lateral={status["lateral_error_m"]:.4f} angle={status["angle_rad"]:.4f}',flush=True)
                end_of_coordination=status.copy()
                sim.set_phase('cooperative_insert')
                stable = 0
                for k in range(30*(40 if keyed else 20)):
                    status = control(references[-1],1.,joint_references[-1])
                    if keyed and k%150==0:
                        print('KEYED_INSERT', {key:status[key] for key in ('depth_m','lateral_error_m','angle_rad',
                            'roll_error_rad','depth_command_m','keyed_alignment_ready','correction_saturated')},flush=True)
                    if keyed:
                        from sim_bridge.connector_keying import keyed_error,keyed_success
                        error=keyed_error(sim.object_poses(),controller.key_target_roll) if robust is None else {key:status[key] for key in ('depth_m','lateral_error_m','angle_rad','roll_rad','roll_error_rad','target_roll_rad','latch_engagement_m','nominal_hooks_caught')}
                        complete=(keyed_success(error,controller.key_config) and status['keyed_alignment_ready']
                            and controller.depth_command>=controller.key_config['lock_target_depth_m']-.00005)
                    else:
                        error=insertion_error(sim.object_poses()) if robust is None else {key:status[key] for key in ('depth_m','lateral_error_m','angle_rad')}
                        complete=error['depth_m']>.010 and error['lateral_error_m']<.001 and error['angle_rad']<.05
                    stable=stable+1 if complete else 0
                    if stable>=(12 if keyed else 6):break
                else:raise RuntimeError(f'Continuous cooperative insertion did not converge: {error}')
                break
            except RestartCooperation:
                restarts+=1
                previous=np.array([sim.tool(a) for a in range(2)])
                print(f'COOPERATIVE_RESTART {restarts}',flush=True)
        sim.capture('inserted')
        report = dict(method='Continuous measured-object feedback during demonstrated coordination and insertion',
                      feedback_hz=30,reference_frame='Frozen female demonstration path with prescribed male-relative goal',
                      normal_fixed_backoff=False,recoveries=controller.recoveries,insertion=error,passed=True,
                      correction_limits=dict(translation_m=.060,rotation_rad=.6,tool_speed_m_s=.08,tool_angular_speed_rad_s=.6),
                      terminal_alignment_error=end_of_coordination,cooperative_restarts=restarts,
                      insertion_started_during_coordination=bool(any(r['inserting'] and r['progress']<1. for r in records)))
        nominal=np.array(reference_log);executed=np.array(targets_log)
        deviation=np.linalg.norm(executed[:,:,:3,3]-nominal[:,:,:3,3],axis=-1)
        rotation=Rotation.from_matrix((executed[:,:,:3,:3]@nominal[:,:,:3,:3].transpose(0,1,3,2)).reshape(-1,3,3)).magnitude()
        report['demonstration_correction'] = dict(max_translation_m=float(deviation.max()),
                                                 max_rotation_rad=float(rotation.max()))
        if keyed:
            report.update(mechanical_task='90-degree keyed latch engagement',key_target_roll_rad=controller.key_target_roll,
                          reference_roll_correction_rad=controller.key_roll_correction,
                          reference_deformation='Necessary key rotation split between arms; residual .06m/.6rad limits around deformed reference')
        return report,np.array(targets_log),np.array(tools_log),np.array(qref_log)
    finally:
        if records:
            import json
            (output/'cooperative_feedback.json').write_text(json.dumps(records,indent=2))
            np.savez_compressed(output/'cooperative_feedback.npz',reference_poses=references,
                                targets=np.array(targets_log),actual_tools=np.array(tools_log),nominal_tools=np.array(reference_log),
                                **{key:np.array([r[key] for r in records]) for key in records[0]})
