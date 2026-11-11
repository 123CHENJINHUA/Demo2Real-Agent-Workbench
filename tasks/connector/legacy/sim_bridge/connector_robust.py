"""Perturbation curriculum and pose/proprioception-only recovery supervision.

External interventions belong to the test environment, never to the policy.
The pose sensor is a noisy/delayed simulation surrogate, not an RGB detector.
Contact forces, perturbation labels and simulator truth are excluded from policy
features. The learned policy selects skills; it is not an end-to-end motor policy.
"""
from collections import deque
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

from sim_bridge.connector_planner import TABLE, align_axis, blend

MODES = ['continue','wait','regrasp_left','regrasp_right','needs_human']
ROOT=Path(__file__).resolve().parents[4]
FEATURE_NAMES = [f'{arm}.{field}' for arm in ('left','right') for field in
                 ('visible','pose_age_s','confidence','grasp_drift_m','jaw_gap_m',
                  'object_height_m','object_speed_m_s','object_x_m','object_y_m','stable_s')]
FEATURE_NAMES += ['progress','wait_s','left_joint_margin_rad','right_joint_margin_rad']


def canonical(pose):
    result=pose.copy()
    x=pose[:3,0].copy();x[2]=0.;x/=max(np.linalg.norm(x),1e-9)
    result[:3,:3]=np.column_stack((x,np.cross([0.,0.,1.],x),[0.,0.,1.]))
    return result


class PoseSensor:
    """Replace sample() with a real timestamped pose tracker at deployment."""
    def __init__(self,seed,delay=.066,noise=.0003,rotation_noise=.002):
        self.rng=np.random.default_rng(seed);self.delay=delay;self.noise=noise;self.rotation_noise=rotation_noise
        self.buffer=deque(maxlen=60);self.last=None;self.last_t=None
        self.last_seen=np.zeros(2);self.stable=np.zeros(2)

    def record(self,truth,time):
        if not self.buffer or time>self.buffer[-1][0]+1e-6:self.buffer.append((time,np.array(truth).copy()))

    def sample(self,truth,time,hidden=()):
        self.record(truth,time)
        available=[(t,p) for t,p in self.buffer if t<=time-self.delay]
        measured_t,measured_pose=available[-1] if available else self.buffer[0]
        poses=measured_pose.copy()
        visible=np.array([a not in hidden and -.65<poses[a,0,3]<-.17 and abs(poses[a,1,3])<.30
                          and TABLE-.01<poses[a,2,3]<1.35 for a in range(2)])
        dt=max(.001,time-self.last_t) if self.last_t is not None else 1/30
        for a in range(2):
            if visible[a]:
                poses[a,:3,3]+=self.rng.normal(0,self.noise,3)
                perturb=Rotation.from_rotvec(self.rng.normal(0,self.rotation_noise,3)).as_matrix()
                poses[a,:3,:3]=perturb@poses[a,:3,:3]
                self.last_seen[a]=time
            elif self.last is not None:poses[a]=self.last[a]
        speed=np.zeros(2) if self.last is None else np.linalg.norm(poses[:,:3,3]-self.last[:,:3,3],axis=-1)/dt
        self.stable=np.where((speed<.035)&visible,self.stable+dt,0.)
        age=time-self.last_seen
        self.last=poses.copy();self.last_t=time
        return dict(poses=poses,visible=visible,age=age,confidence=visible.astype(float)*.95,
                    speed=speed,stable=self.stable.copy(),time=time,measurement_time=measured_t)


class Disturbance:
    def __init__(self,sim,kind,arm,delay,shift):
        self.sim=sim;self.kind=kind;self.arm=arm;self.delay=delay;self.shift=np.array(shift)
        self.phase_start=None;self.started=None;self.origin=None;self.events=[];self.pending=[]
        self.scheduled=kind!='none'
        self.cards_active=False

    def queue(self,kind,arm):self.pending.append((kind,arm))

    def hidden(self,time):
        if self.started is None:return ()
        elapsed=time-self.started
        if self.kind=='occlude' and elapsed<2.:return (self.arm,)
        if self.kind=='relocate' and elapsed<1.9:return (self.arm,)
        if self.kind=='remove':return (self.arm,)
        return ()

    def image_occlusion(self, visible):
        """Environment-only visual cards. Their depth is rendered normally.

        These cards test perception occlusion, not hand contacts or collision
        avoidance. No hidden flag is supplied to the RGBD sensor or actor.
        """
        rig=getattr(self.sim,'rgbd',None)
        if rig is None:return
        import omni.usd
        from pxr import Gf,UsdGeom
        stage=omni.usd.get_context().get_stage()
        self.cards_active=visible
        for a,frame in enumerate(rig.frames):
            card=UsdGeom.Cube.Define(stage,f'/World/RGBDOccluder{a}')
            xform=UsdGeom.Xformable(card.GetPrim())
            if visible:
                pose=frame['T_world_camera'].copy()
                pose[:3,3]=(pose[:3,3]+self.origin[:3,3])/2
                xform.ClearXformOpOrder()
                xform.AddTransformOp().Set(Gf.Matrix4d(pose.T.tolist()))
                xform.AddScaleOp().Set(Gf.Vec3f(.15,.15,.002))
                card.CreateSizeAttr(1.)
                card.CreateDisplayColorAttr([Gf.Vec3f(.45,.45,.45)])
            card.CreateVisibilityAttr('inherited' if visible else 'invisible')

    def before_step(self,time,phase):
        insertion_stage=getattr(self.sim.args,'disturbance_stage','coordination')=='insertion'
        stage_ready=(phase=='cooperative_insert') if insertion_stage else phase.startswith('dex_coordination')
        if stage_ready and self.phase_start is None:self.phase_start=time
        depth_gate=getattr(self.sim.args,'disturbance_depth',None)
        depth_ready=True
        if depth_gate is not None:
            from sim_bridge.connector_keying import keyed_error
            depth_ready=keyed_error(self.sim.object_poses())['depth_m']>=depth_gate
        trigger=self.scheduled and self.phase_start is not None and time-self.phase_start>=self.delay and depth_ready
        if self.pending or trigger:
            if self.cards_active:self.image_occlusion(False)
            if self.pending:self.kind,self.arm=self.pending.pop(0)
            self.scheduled=False;self.started=time
            self.origin=self.sim.object_poses()[self.arm].copy()
            if self.kind=='occlude' and getattr(self.sim.args,'perception','surrogate')=='rgbd':
                self.image_occlusion(True)
            self.events.append(dict(time_s=time,kind=self.kind,arm=self.arm,shift_m=self.shift.tolist(),
                                    intervention='external force carrier + jaw release' if self.kind in ('relocate','remove') else self.kind))
            print('DISTURBANCE',self.events[-1],flush=True)
        overrides={}
        if self.started is None:return overrides
        elapsed=time-self.started;a=self.arm
        if self.cards_active and elapsed>=2.:
            self.image_occlusion(False)
        if self.kind in ('drop','relocate','remove') and elapsed<1.15:overrides[a]=.065
        if self.kind=='grip_slip' and elapsed<.18:overrides[a]=.027
        if self.kind=='grip_release' and elapsed<.8:overrides[a]=.065
        if self.kind=='dual_release' and elapsed<.8:overrides={0:.065,1:.065}
        if self.kind=='push' and elapsed<.15:
            force=np.array([.35,.25,0.])
            self.sim.object_contacts.apply_forces_and_torques_at_pos(forces=force[None],indices=np.array([a]))
        if self.kind in ('relocate','remove') and elapsed<2.2:
            destination=self.origin[:3,3].copy();destination[:2]+=self.shift
            destination[2]=TABLE+.012
            if self.kind=='remove':destination=np.array([-.80,.35,1.0])
            high=self.origin[:3,3]+[0.,0.,.10]
            if elapsed<.55:target=self.origin[:3,3]+min(1.,elapsed/.55)*(high-self.origin[:3,3])
            else:target=high+np.clip((elapsed-.55)/1.4,0.,1.)*(destination-high)
            pose=self.sim.object_poses()[a];obj=self.sim.objects[a]
            force=40.*(target-pose[:3,3])-1.8*np.array(obj.get_linear_velocity())+[0.,0.,[.020,.025][a]*9.81]
            force*=min(1.,3./max(np.linalg.norm(force),1e-9))
            rot=Rotation.from_matrix(canonical(self.origin)[:3,:3]@pose[:3,:3].T).as_rotvec()
            torque=.003*rot-.0003*np.array(obj.get_angular_velocity())
            torque*=min(1.,.01/max(np.linalg.norm(torque),1e-9))
            self.sim.object_contacts.apply_forces_and_torques_at_pos(forces=force[None],torques=torque[None],indices=np.array([a]))
        return overrides


def features(sample,tools,gaps,grips,progress,wait_s,joint_margin):
    values=[]
    for a in range(2):
        relative=np.linalg.inv(tools[a])@sample['poses'][a]
        drift=np.linalg.norm(relative[:3,3]-grips[a][:3,3])
        values.extend([sample['visible'][a],min(10.,sample['age'][a]),sample['confidence'][a],
                       min(.5,drift),gaps[a],sample['poses'][a,2,3]-TABLE,
                       min(2.,sample['speed'][a]),sample['poses'][a,0,3],sample['poses'][a,1,3],min(2.,sample['stable'][a])])
    return np.array([*values,progress,min(10.,wait_s),*joint_margin],dtype=np.float32)


def expert_mode(x):
    # Labels use only the same pose/proprioceptive features available to a student.
    if min(x[0],x[10])<.5:
        return 4 if x[21]>8. else 1
    lost=[a for a in range(2) if x[a*10+3]>.025 or x[a*10+4]<.006 or x[a*10+4]>.035]
    if not lost:return 0
    for a in lost:
        z=x[a*10+5]
        if z<-.005 or not (-.60<x[a*10+7]<-.20 and abs(x[a*10+8])<.25):return 4
        if x[a*10+6]>.035 or x[a*10+9]<.20:return 1
    return 2+lost[0]


class LearnedSupervisor:
    def __init__(self,path):
        from modules.policies.neural.recovery_supervisor import RecoverySupervisor
        path=(ROOT/path).resolve();path.relative_to(ROOT);self.model=RecoverySupervisor(path);self.metadata=self.model.metadata
        if self.metadata['feature_names']!=FEATURE_NAMES:raise ValueError('Policy observation schema mismatch')
    def act(self,x):return self.model.act(x)


class RobustSession:
    def __init__(self,sim,output,objects,tools):
        self.sim=sim;self.output=output
        if getattr(sim.args,'perception','surrogate')=='rgbd':
            from sim_bridge.connector_rgbd import RGBDPoseSensor
            def gaps():
                return [float(-np.diff(s['robot'].get_joint_positions(joint_indices=s['fingers']))[0]) for s in sim.arms]
            show=not sim.args.rgbd_no_visualize and (sim.args.rgbd_visualize or not sim.args.headless)
            self.sensor=RGBDPoseSensor(sim.rgbd,sim.tool,gaps,output/'vision',hz=sim.args.rgbd_hz,
                                      visualize=show,live=show and not sim.args.headless,pump=sim.pump,
                                      task_goal=sim.args.stop_after)
            observed=self.sensor.sample(sim.time())
        else:
            self.sensor=PoseSensor(sim.args.seed+173,sim.args.pose_delay,sim.args.pose_noise,getattr(sim.args,'pose_rotation_noise',.002))
            observed=self.sensor.sample(objects,sim.time())
        self.disturbance=Disturbance(sim,sim.args.disturbance,sim.args.disturb_arm,
                                     sim.args.disturb_after,sim.args.disturb_shift)
        self.grips=[np.linalg.inv(tools[a])@observed['poses'][a] for a in range(2)]
        self.wait_s=0.;self.rows=[];self.labels=[];self.actions=[];self.confidences=[];self.recoveries=[];self.times=[]
        self.raw_actions=[];self.needs_human=False;self.active=True;self.guard_overrides=0
        self.tool_history=deque(maxlen=90)
        self.pose_records=[];self.tool_records=[];self.joint_records=[];self.gap_records=[];self.reference_records=[]
        self.student=LearnedSupervisor(sim.args.supervisor_policy) if sim.args.supervisor_policy else None
        self.phase_guard=None
        if getattr(sim.args,'phase_supervisor',None)=='insertion_guard_v3':
            from modules.skills.insertion_recovery import PhaseRecovery
            self.phase_guard=PhaseRecovery(self)
        sim.set_disturbance(self)
        if not sim.args.headless:
            import omni.ui as ui
            self.window=ui.Window('Connector disturbances',width=350,height=210)
            self.buttons=[]
            with self.window.frame:
                with ui.VStack():
                    ui.Label('Learned recovery supervisor' if self.student else 'Expert data collection')
                    self.status=ui.Label('Interventions during active cooperative task')
                    for kind in ('push','drop','relocate','occlude'):
                        with ui.HStack():
                            for a,name in enumerate(('left','right')):
                                self.buttons.append(ui.Button(f'{kind} {name}',clicked_fn=lambda kind=kind,a=a:self.disturbance.queue(kind,a)))

    def observe(self):
        if isinstance(self.sensor,PoseSensor):
            sample=self.sensor.sample(self.sim.object_poses(),self.sim.time(),self.disturbance.hidden(self.sim.time()))
        else:
            sample=self.sensor.sample(self.sim.time())
        current=[self.sim.tool(a) for a in range(2)]
        if not self.tool_history or self.sim.time()>self.tool_history[-1][0]+1e-6:
            self.tool_history.append((self.sim.time(),np.array(current)))
        history=[tools for t,tools in self.tool_history if t<=sample['measurement_time']+1e-6]
        matched=history[-1] if history else current
        # Predict only confidently held objects using timestamp-matched robot
        # FK. This is proprioceptive delay compensation, not simulator truth.
        for a in range(2):
            state=self.sim.arms[a]
            gap=float(-np.diff(state['robot'].get_joint_positions(joint_indices=state['fingers']))[0])
            measured_grip=np.linalg.inv(matched[a])@sample['poses'][a]
            if sample['visible'][a] and .008<gap<.035 and np.linalg.norm(measured_grip[:3,3]-self.grips[a][:3,3])<.020:
                sample['poses'][a]=current[a]@measured_grip
        return sample

    def begin_held_phase(self):
        sample=self.observe()
        if not np.all(sample['visible']):
            self.needs_human=True
            raise RuntimeError('Cannot establish cooperative grasp frames without valid visual observations')
        self.grips=[np.linalg.inv(self.sim.tool(a))@sample['poses'][a] for a in range(2)]
        if hasattr(self,'buttons'):
            for button in self.buttons:button.enabled=True
            self.status.text='Interventions during active cooperative task'

    def before_step(self,time,phase):
        if not self.active:return {}
        if isinstance(self.sensor,PoseSensor) and (not self.sensor.buffer or time-self.sensor.buffer[-1][0]>=1/60-1e-6):
            self.sensor.record(self.sim.object_poses(),time)
        if not self.tool_history or time-self.tool_history[-1][0]>=1/60-1e-6:
            self.tool_history.append((time,np.array([self.sim.tool(a) for a in range(2)])))
        return self.disturbance.before_step(time,phase)

    def decide(self,sample,progress,reference=None):
        tools=[self.sim.tool(a) for a in range(2)];gaps=[];margins=[];joints=[]
        for state in self.sim.arms:
            q=state['robot'].get_joint_positions(joint_indices=state['indices'])
            joints.append(q.copy())
            gaps.append(float(-np.diff(state['robot'].get_joint_positions(joint_indices=state['fingers']))[0]))
            margins.append(float(np.min(np.minimum(q-self.sim.ik.limits[:,0],self.sim.ik.limits[:,1]-q))))
        x=features(sample,tools,gaps,self.grips,progress,self.wait_s,margins);label=expert_mode(x)
        action,confidence=self.student.act(x) if self.student else (label,1.)
        # Visibility is a mandatory observation-validity guard, not an expert
        # action override. Low confidence never commands an unseen regrasp.
        raw_action=action
        if not np.all(sample['visible']) and action not in (1,4):action=1
        if confidence<.60:action=1
        self.raw_actions.append(raw_action)
        self.guard_overrides+=int(raw_action!=action)
        self.rows.append(x);self.labels.append(label);self.actions.append(action);self.confidences.append(confidence)
        self.times.append(sample['time'])
        self.pose_records.append(sample['poses'].copy());self.tool_records.append(np.array(tools))
        self.joint_records.append(np.array(joints));self.gap_records.append(gaps)
        self.reference_records.append(np.array(tools) if reference is None else reference.copy())
        self.wait_s=self.wait_s+1/30 if action==1 else 0.
        return action

    def save(self):
        self.active=False
        if hasattr(self.sensor,'close'):self.sensor.close()
        if hasattr(self,'buttons'):
            for button in self.buttons:button.enabled=False
            self.status.text='Task ended; start another episode from main panel'
        np.savez_compressed(self.output/('base_supervision.npz' if self.phase_guard else 'supervision.npz'),observations=np.array(self.rows),expert_modes=np.array(self.labels),
                            **{('base_modes' if self.phase_guard else 'executed_modes'):np.array(self.actions)},raw_modes=np.array(self.raw_actions),confidence=np.array(self.confidences),feature_names=np.array(FEATURE_NAMES),time=np.array(self.times))
        np.savez_compressed(self.output/'policy_observations.npz',time=np.array(self.times),
                            estimated_object_poses=np.array(self.pose_records),tool_poses_fk=np.array(self.tool_records),
                            measured_joints=np.array(self.joint_records),measured_jaw_gaps=np.array(self.gap_records),
                            nominal_tool_reference=np.array(self.reference_records),expert_modes=np.array(self.labels),
                            **{('base_modes' if self.phase_guard else 'executed_modes'):np.array(self.actions)},force_observations=np.array(False))
        summary=dict(policy='learned skill supervisor' if self.student else 'scripted data-collection expert',
                     policy_checkpoint=None if self.student is None else str(self.sim.args.supervisor_policy),
                     force_observations=False,pose_source=getattr(self.sensor,'source','Noisy delayed simulator pose surrogate; not RGB perception'),
                     pose_noise_m=self.sensor.noise if isinstance(self.sensor,PoseSensor) else None,
                     pose_delay_s=self.sensor.delay if isinstance(self.sensor,PoseSensor) else None,
                     held_pose_prediction='Timestamp-matched FK compensation; disabled for empty/open/lost grasps',
                     events=self.disturbance.events,recoveries=self.recoveries,
                     requires_human=self.needs_human,observation_guard_overrides=self.guard_overrides,
                     counts={name:int(np.count_nonzero(np.array(self.actions)==i)) for i,name in enumerate(MODES)},
                     learning_scope='High-level recovery skill decisions; low-level demonstration and visual servo skills remain deterministic')
        if self.phase_guard:
            summary['base_decision_counts']=summary.pop('counts');summary['phase_recovery']=self.phase_guard.save();summary['requires_human']=False;summary['base_modes_are_proposals_only']=True;summary['learning_scope']='Frozen v2 base proposals plus explicit scripted phase guard; no learned v3 policy'
        (self.output/'robustness.json').write_text(json.dumps(summary,indent=2))
        return summary

    def regrasp(self,arm,sample,reference,controller):
        sim=self.sim
        sim.set_phase(f'recovery_regrasp_{arm}')
        state=sim.arms[arm];state['opening']=.06
        retreat=sim.tool(arm);retreat[2,3]=max(retreat[2,3]+.10,TABLE+.20)
        sim.move([retreat if a==arm else None for a in range(2)],2.)
        # The dropped object must settle before a new geometric grasp is planned.
        for _ in range(120):
            sample=self.observe()
            if sample['visible'][arm] and sample['stable'][arm]>.25:break
            sim.wait(1/30)
        else:raise RuntimeError('Recovery waiting for stable visible object timed out')
        obj=sample['poses'][arm]
        if abs(obj[2,0])>.35 or obj[2,3]<TABLE-.005:raise RuntimeError('Dropped object has no supported tabletop grasp; needs human')
        source=np.load(Path(__file__).resolve().parents[4]/'data/reference/connector/converted/episode_000.npz')
        frame=int(source['grasp_frames'][arm])
        target=canonical(obj)@np.linalg.inv(source['initial_objects'][arm])@source['eef_poses'][frame,arm]
        if getattr(sim.args,'recovery_skill','legacy_v1')=='contact_center_v2':
            from modules.skills.connector_regrasp import execute
            execute(self,arm,target,controller)
        else:
            above=target.copy();above[2,3]+=.09
            sim.move([above if a==arm else None for a in range(2)],2.)
            sim.move([target if a==arm else None for a in range(2)],2.)
            state['opening']=0.;sim.wait(1.3)
            lifted=sim.tool(arm);lifted[2,3]+=.08
            sim.move([lifted if a==arm else None for a in range(2)],2.5)
            sim.wait(.3);sample=self.observe()
            gap=float(-np.diff(state['robot'].get_joint_positions(joint_indices=state['fingers']))[0])
            if not sample['visible'][arm] or not .008<gap<.035 or sample['poses'][arm,2,3]<TABLE+.04:
                raise RuntimeError('Recovery regrasp lacks a valid visual lift and gripper confirmation')
        # Restart a well-defined cooperative stage after loss, rather than
        # resuming an insertion cursor whose two-hand constraints are obsolete.
        sim.move([p.copy() for p in controller.start_tools],3.5)
        sim.wait(.2)
        sample=self.observe()
        if not np.all(sample['visible']):
            self.needs_human=True
            raise RuntimeError('Cannot restart cooperation without visible connector poses')
        for a in range(2):
            self.grips[a]=np.linalg.inv(sim.tool(a))@sample['poses'][a]
            controller.grips[a]=self.grips[a].copy()
            controller.initial_grips[a]=self.grips[a].copy()
            controller.commands[a]=sim.tool(a).copy()
        if getattr(controller,'keyed',False):controller.reset_key_reference(sample['poses'])
        controller.translation_integral[:]=0.;controller.rotation_integral[:]=0.
        controller.inserting=False;controller.depth_command=-.012
        controller.recovering=False;controller.jam_elapsed=0.
        self.recoveries.append(dict(arm=arm,time_s=sim.time(),passed=True))
        if self.phase_guard:self.phase_guard.reset()
        sim.set_phase('dex_coordination_closed_loop')
