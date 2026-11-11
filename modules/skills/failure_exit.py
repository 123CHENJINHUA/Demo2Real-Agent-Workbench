"""Release both physical grippers, then drive joints back to loaded initial state."""
from pathlib import Path
import json,numpy as np
from modules.planning.return_home import plan_return

def execute(session,config,vertices):
 sim=session.sim;out=Path(session.output)/'failure_exit.json';report={'schema_version':'failure-exit/v1','requested':'release_both_and_return_initial_joints','passed':False,'object_pose_writes':False,'robot_joint_teleport':False,'requires_human':False,'hardware_commands_sent':0}
 def save():out.write_text(json.dumps(report,indent=2))
 def step():
  for _ in range(8):sim.step()
 def gaps():return np.array([float(-np.diff(a['robot'].get_joint_positions(joint_indices=a['fingers']))[0]) for a in sim.arms])
 try:
  sim.set_phase('abort_open_grippers')
  for arm in sim.arms:arm['opening']=config['failure_open_width_m']
  started=sim.time();stable=0
  while sim.time()-started<config['failure_open_timeout_s']:
   step();stable=stable+1 if np.all(gaps()>=config['failure_min_open_gap_m']) else 0
   if stable>=3:break
  else:raise RuntimeError('Failure exit: both jaws did not open')
  sim.wait(config['failure_release_settle_s']);report['opened_gaps_m']=gaps().tolist();report['evaluation_only_finger_force_max_N']=float(np.linalg.norm(sim.force_matrix(),axis=-1).max());save()
  current=np.array([a['command'].copy() for a in sim.arms]);home=np.asarray(sim.initial_joint_targets);path,preflight=plan_return(sim.ik,sim.bases,current,home,vertices,sim.table_height,config);report['return_preflight']=preflight;report['initial_joint_targets']=home.tolist();save();sim.set_phase('abort_return_home')
  for targets in path:
   for a,q in zip(sim.arms,targets):a['command']=q.copy();a['opening']=config['failure_open_width_m']
   step()
  started=sim.time()
  while sim.time()-started<config['home_settle_timeout_s']:
   measured=np.array([a['robot'].get_joint_positions(joint_indices=a['indices']) for a in sim.arms]);error=np.max(np.abs(measured-home),axis=1)
   if sim.time()-started>=config.get('home_min_settle_s',0) and np.max(error)<=config['home_tolerance_rad']:break
   step()
  else:raise RuntimeError('Failure exit: initial joint posture did not settle')
  report.update(passed=True,final_joint_error_max_rad=error.tolist(),final_jaw_gaps_m=gaps().tolist(),final_simulation_time_s=sim.time());sim.set_phase('abandoned_at_initial_state');save();return report
 except Exception as exc:report.update(error=str(exc),exception=type(exc).__name__,return_attempts=getattr(exc,'attempts',[]));save();return report
