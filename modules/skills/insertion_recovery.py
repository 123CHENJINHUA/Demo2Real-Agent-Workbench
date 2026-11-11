"""Phase-aware recovery via observed poses/proprioception and simulator ports.

Actual actuation and base proposals are logged separately. No force, event label,
object pose write or rigid attachment is used to choose movements.
"""
from pathlib import Path
from collections import Counter
import json,numpy as np
from scipy.spatial.transform import Rotation
from contracts.phase_recovery import PhaseState
from modules.policies.scripted.phase_guard import PhaseGuardPolicy
from modules.planning.regrasp import hand_geometry

BASE_NAMES=('continue','wait','regrasp_left','regrasp_right','needs_human')
class PhaseRecovery:
 def __init__(self,session):
  self.session=session;self.sim=session.sim;self.root=Path(__file__).resolve().parents[2];self.config=json.loads((self.root/'config/phase_recovery_v3.json').read_text());self.policy=PhaseGuardPolicy(self.config);self.anchors=None;self.gap_baseline=None;self.previous=None;self.trace=[];self.events=[];self.counts=Counter();self.overrides=0;self.abandoned=False;self.reason=None;self.vertices=hand_geometry(self.root);self.last_state=None;self.was_active=False
 def reset(self):self.anchors=None;self.gap_baseline=None;self.previous=None;self.policy.reset_transient();self.was_active=False
 def gaps(self):return np.array([float(-np.diff(a['robot'].get_joint_positions(joint_indices=a['fingers']))[0]) for a in self.sim.arms])
 def geometry(self,sample,controller):
  from sim_bridge.connector_keying import keyed_error,keyed_ready,keyed_success
  e=keyed_error(sample['poses'],controller.key_target_roll,controller.key_config);complete=keyed_success(e,controller.key_config) and keyed_ready(e,controller.key_config) and controller.depth_command>=controller.key_config['lock_target_depth_m']-.00005
  return e,bool(complete)
 def observed(self,sample):return bool(np.all(sample['visible']) and np.min(sample['confidence'])>=self.config['confidence_min'] and np.max(sample['age'])<=self.config['pose_age_max_s'])
 def band(self,tool,obj):
  return (np.linalg.inv(obj)@tool@np.array([0.,0.,.130,1.]))[:3]
 def band_drift(self,tool,obj,anchor):
  current=self.band(tool,obj);baseline=(np.linalg.inv(anchor)@np.array([0.,0.,.130,1.]))[:3]
  return float(abs(current[0]-baseline[0])),float(abs(np.linalg.norm(current[1:])-np.linalg.norm(baseline[1:])))
 def capture_target(self,obj,arm,rotation):
  # Cylindrical rear-barrel grasp: axial band matters, free shaft roll does not.
  band=(np.linalg.inv(self.anchors[arm])@np.array([0.,0.,.130,1.]))[0];center=obj@np.array([band,0.,0.,1.]);target=np.eye(4);target[:3,:3]=rotation;target[:3,3]=center[:3]-rotation@np.array([0.,0.,.130]);return target
 def inspect(self,sample,controller,base_action,progress):
  c=self.config;tools=np.array([self.sim.tool(a) for a in range(2)]);poses=sample['poses'];grips=np.linalg.inv(tools)@poses;gaps=self.gaps();e,complete=self.geometry(sample,controller);active=bool(controller.inserting or (progress>=.7 and e['depth_m']>.002))
  if self.anchors is None or not active:
   self.anchors=np.array(self.session.grips).copy();self.gap_baseline=gaps.copy()
   if active and np.all((gaps>c['min_gap_m'])&(gaps<c['max_gap_m'])):self.anchors=grips.copy()
  if active and not self.was_active and self.observed(sample) and np.all((gaps>c['support_min_gap_m'])&(gaps<c['max_gap_m'])):self.anchors=grips.copy();self.gap_baseline=gaps.copy()
  self.was_active=active
  drift=np.linalg.norm(grips[:,:3,3]-self.anchors[:,:3,3],axis=1);dt=max(1/240,sample['time']-self.previous[0]) if self.previous is not None else 1/30;rates=np.zeros(2) if self.previous is None else (gaps-self.previous[1])/dt;relative_speed=np.zeros(2) if self.previous is None else np.linalg.norm(grips[:,:3,3]-self.previous[2][:,:3,3],axis=1)/dt
  axial=np.array([self.band_drift(tools[a],poses[a],self.anchors[a])[0] for a in (0,1)]);radial=np.array([self.band_drift(tools[a],poses[a],self.anchors[a])[1] for a in (0,1)]);closing_axis=np.array([abs(tools[a,:3,1]@poses[a,:3,0]) for a in (0,1)]);held=(gaps>c['support_min_gap_m'])&(gaps<c['max_gap_m'])&(axial<c['support_grip_max_drift_m'])&(radial<c['barrel_radial_change_max_m'])&(closing_axis<c['closing_axis_max_dot']);suspicious=((gaps<self.gap_baseline-c['gap_warning_change_m'])&(rates<-c['closing_speed_m_s']))|((axial>c['warning_drift_m'])&(relative_speed>c['warning_relative_speed_m_s']))
  heights=poses[:,2,3]-self.sim.table_height;tabletop=(heights>=-.005)&(heights<.030)&(np.abs(poses[:,2,0])<.35)&(sample['stable']>=.20-1e-6)
  supported=bool(c['pair_min_depth_m']<e['depth_m']<c['pair_max_depth_m'] and e['angle_rad']<c['pair_max_axis_rad'] and abs(e['roll_error_rad'])<c['pair_max_roll_rad'] and np.min(heights)>c['pair_min_height_m'])
  state=PhaseState(float(sample['time']),active,self.observed(sample),complete,tuple(bool(x) for x in held),tuple(bool(x) for x in suspicious),tuple(bool(x) for x in tabletop),supported,float(e['depth_m']));decision=self.policy.decide(state,base_action);self.previous=(sample['time'],gaps.copy(),grips.copy());self.last_state=state
  actual=BASE_NAMES[base_action] if decision.mode=='base' else decision.mode;self.counts[actual]+=1;self.overrides+=int(actual!=BASE_NAMES[base_action]);self.trace.append({'time_s':float(sample['time']),'stage':'insertion' if active else 'coordination','base_mode':BASE_NAMES[base_action],'raw_base_mode':BASE_NAMES[self.session.raw_actions[-1]],'base_confidence':float(self.session.confidences[-1]),'actual':actual,'reason':decision.reason,'arm':decision.arm,'gaps_m':gaps.tolist(),'grip_drift_m':drift.tolist(),'axial_band_drift_m':axial.tolist(),'radial_band_change_m':radial.tolist(),'closing_axis_dot':closing_axis.tolist(),'closing_rates_m_s':rates.tolist(),'held':list(state.held),'observed':state.observed,'geometry':e,'depth_command_m':float(controller.depth_command),'object_poses_estimated':poses.tolist(),'tool_poses_fk':tools.tolist(),'source':'simulated_pose_estimate','force_observations':False})
  return decision,e
 def abandon(self,reason):
  self.abandoned=True;self.reason=str(reason);self.session.needs_human=False;self.sim.set_phase('phase_recovery_abandoned');self.events.append({'time_s':self.sim.time(),'outcome':'abandoned','reason':str(reason),'terminal_control':'release_both_and_return_initial_joints'});self.finish_failure();raise RuntimeError('Trial abandoned: '+str(reason))
 def finish_failure(self):
  if hasattr(self,'failure_exit_result'):return self.failure_exit_result
  from modules.skills.failure_exit import execute
  self.failure_exit_result=execute(self.session,self.config,self.vertices);return self.failure_exit_result
 def _support(self,sample,controller,arm):
  if not self.observed(sample):raise RuntimeError('Recovery observations became unavailable')
  good=1-arm;gap=self.gaps()[good];rel=np.linalg.inv(self.sim.tool(good))@sample['poses'][good]
  axial,radial=self.band_drift(self.sim.tool(good),sample['poses'][good],self.anchors[good])
  if not self.config['support_min_gap_m']<gap<self.config['max_gap_m'] or axial>self.config['support_grip_max_drift_m'] or radial>self.config['barrel_radial_change_max_m']:raise RuntimeError('Supporting arm lost its grip')
  e,_=self.geometry(sample,controller)
  if e['depth_m']<self.config['pair_min_depth_m'] or np.min(sample['poses'][:,2,3]-self.sim.table_height)<self.config['pair_min_height_m']:raise RuntimeError('Partial mating support became invalid')
  return e
 def _step(self):
  for _ in range(8):self.sim.step()
 def _command(self,arm,target):
  state=self.sim.arms[arm];world=self.vertices@target[:3,:3].T+target[:3,3]
  if world[:,2].min()<self.sim.table_height+.0005:raise RuntimeError('In-place hand path lacks tabletop clearance')
  try:q=self.sim.ik.solve(target,arm,state['command'],False)
  except RuntimeError:q=self.sim.ik.solve(target,arm,state['command'],True)
  state['command']+=np.clip(q-state['command'],-.015,.015)
 def _anchor(self,sample,controller,arm=None):
  indices=range(2) if arm is None else (arm,)
  for a in indices:
   grip=np.linalg.inv(self.sim.tool(a))@sample['poses'][a];self.session.grips[a]=grip.copy();controller.grips[a]=grip.copy();self.anchors[a]=grip;self.gap_baseline[a]=self.gaps()[a];controller.translation_integral[a]=0.;controller.rotation_integral[a]=0.
  e,_=self.geometry(sample,controller);controller.depth_command=float(np.clip(e['depth_m'],-.012,controller.key_config['lock_target_depth_m']));self.policy.reset_transient()
 def recover(self,decision,sample,controller):
  c=self.config;arm=decision.arm;event={'start_s':self.sim.time(),'kind':decision.mode,'arm':arm,'outcome':'in_progress','uses_force_to_decide':False};self.events.append(event)
  try:
   if decision.mode=='reanchor':
    if not all(self.last_state.held):raise RuntimeError('Cannot reanchor invalid grips')
    self._anchor(sample,controller);event.update(outcome='observational_reanchor',end_s=self.sim.time());return
   if decision.mode=='unload':
    # Both grips still held; early insertion only, below the latch region.
    start_depth=self.geometry(sample,controller)[0]['depth_m'];target=self.sim.tool(0).copy();target[:3,3]+=sample['poses'][1,:3,0]*c['unload_distance_m'];start=self.sim.tool(0).copy()
    from scipy.spatial.transform import Slerp
    rotations=Slerp([0,1],Rotation.from_matrix([start[:3,:3],target[:3,:3]]))
    for u in np.linspace(0,1,31):
     s=self.session.observe();self._support(s,controller,0);T=target.copy();T[:3,3]=(1-u)*start[:3,3]+u*target[:3,3];T[:3,:3]=rotations(u).as_matrix();self._command(0,T);self._step()
    s=self.session.observe();end_depth=self.geometry(s,controller)[0]['depth_m']
    if start_depth-end_depth<.0015:raise RuntimeError('Bounded unloading was not observed')
    self._anchor(s,controller);self.policy.recovered();event.update(outcome='unloaded',end_s=self.sim.time(),measured_backoff_m=start_depth-end_depth);return
   self.sim.set_phase(f'phase_air_recapture_{arm}');state=self.sim.arms[arm];initial=self.sim.tool(arm);target=self.capture_target(sample['poses'][arm],arm,initial[:3,:3]);translation=np.linalg.norm(target[:3,3]-initial[:3,3]);angle=Rotation.from_matrix(target[:3,:3]@initial[:3,:3].T).magnitude()
   if translation>c['recapture_translation_max_m'] or angle>c['recapture_rotation_max_rad']:raise RuntimeError('In-place correction exceeds bounded envelope')
   state['opening']=c['open_width_m'];started=self.sim.time();command=initial.copy();aligned=False
   while self.sim.time()-started<c['recapture_timeout_s']:
    s=self.session.observe();self._support(s,controller,arm);wanted=self.capture_target(s['poses'][arm],arm,initial[:3,:3]);displacement=wanted[:3,3]-initial[:3,3]
    if np.linalg.norm(displacement)>c['recapture_translation_max_m']:raise RuntimeError('Observed target left recapture envelope')
    delta=wanted[:3,3]-command[:3,3];command[:3,3]+=delta*min(1,c['servo_speed_m_s']/30/max(np.linalg.norm(delta),1e-12));rot=Rotation.from_matrix(wanted[:3,:3]@command[:3,:3].T).as_rotvec();rot*=min(1,c['servo_angular_speed_rad_s']/30/max(np.linalg.norm(rot),1e-12));command[:3,:3]=Rotation.from_rotvec(rot).as_matrix()@command[:3,:3];self._command(arm,command);self._step();tool=self.sim.tool(arm)
    p_error=np.linalg.norm(tool[:3,3]-wanted[:3,3]);r_error=Rotation.from_matrix(wanted[:3,:3]@tool[:3,:3].T).magnitude()
    if self.sim.time()-started>c['open_settle_s'] and p_error<.0015 and r_error<.03:aligned=True;break
   if not aligned:raise RuntimeError('In-place visual servo timed out')
   capture_reference=np.linalg.inv(wanted)@s['poses'][arm]
   state['opening']=0.;started=self.sim.time();stable=0.;previous=None
   while self.sim.time()-started<c['close_timeout_s']:
    self._step();s=self.session.observe();self._support(s,controller,arm);gap=self.gaps()[arm];grip=np.linalg.inv(self.sim.tool(arm))@s['poses'][arm];p_error=np.linalg.norm(grip[:3,3]-capture_reference[:3,3]);r_error=float(np.arccos(np.clip(grip[:3,0]@capture_reference[:3,0],-1,1)));quiet=previous is not None and np.linalg.norm(grip[:3,3]-previous[:3,3])<.00075
    stable=stable+1/30 if c['min_gap_m']<gap<c['max_gap_m'] and p_error<c['close_translation_tolerance_m'] and r_error<c['close_rotation_tolerance_rad'] and quiet else 0.;previous=grip.copy()
    if stable>=c['close_stable_s']:break
   else:raise RuntimeError('In-place grip confirmation failed')
   self._anchor(s,controller,arm);self.policy.recovered();forces=np.linalg.norm(self.sim.force_matrix()[arm*2:arm*2+2,arm],axis=-1);event.update(outcome='recaptured',end_s=self.sim.time(),gap_m=float(gap),translation_error_m=float(p_error),rotation_error_rad=float(r_error),evaluation_only_finger_forces_N=forces.tolist(),independent_two_finger_contact=bool(np.all(forces>.2)))
  except RuntimeError as exc:event.update(outcome='abandoned',end_s=self.sim.time(),error=str(exc));self.abandon(str(exc))
 def save(self):
  out=Path(self.session.output);(out/'phase_trace.json').write_text(json.dumps(self.trace,indent=2));summary={'version':'insertion_guard_v3','base_policy':'frozen v2 MLP' if self.session.student else 'scripted expert','actual_action_counts':dict(self.counts),'phase_overrides':self.overrides,'events':self.events,'abandoned':self.abandoned,'abandon_reason':self.reason,'requires_human':False,'motion_scope':'bounded joint targets only; no object writes/attachments','not_a_new_learned_model':True,'failure_exit':getattr(self,'failure_exit_result',None),'force_observations':False};(out/'phase_recovery.json').write_text(json.dumps(summary,indent=2));return summary
