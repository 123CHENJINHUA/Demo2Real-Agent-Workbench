"""Bounded motor-space return paths, hand/table preflight only."""
import numpy as np

def plan_return(ik,bases,current,home,vertices,table_height,config):
 current=np.asarray(current,float);home=np.asarray(home,float);c=config
 if current.shape!=(2,7) or home.shape!=(2,7):raise ValueError('Two seven-joint states required')
 if not np.isfinite(current).all() or not np.isfinite(home).all():raise ValueError('Nonfinite return target')
 if np.any(home<ik.limits[:,0]) or np.any(home>ik.limits[:,1]):raise ValueError('Initial joint target exceeds URDF limits')
 attempts=[];step=c['home_speed_rad_s']/30
 for order in ('together','left_then_right','right_then_left'):
  phases=[(0,1)] if order=='together' else [(0,),(1,)] if order=='left_then_right' else [(1,),(0,)];q=current.copy();path=[];minimum=np.inf
  try:
   for arms in phases:
    start=q.copy();target=q.copy()
    for arm in arms:target[arm]=home[arm]
    count=max(1,int(np.ceil(np.max(np.abs(target-start))/step)))
    for u in np.linspace(0,1,count+1)[1:]:
     knot=start+u*(target-start)
     if np.any(knot<ik.limits[:,0]-1e-6) or np.any(knot>ik.limits[:,1]+1e-6):raise RuntimeError('Return path violates joint limits')
     for arm in (0,1):
      T=ik.fk(knot[arm]);T[:3,3]+=bases[arm];clearance=float((vertices@T[:3,:3].T+T[:3,3])[:,2].min()-table_height);minimum=min(minimum,clearance)
      if clearance<c['home_min_hand_table_clearance_m']:raise RuntimeError('Return hand/table clearance insufficient')
     path.append(knot)
    q=target
   return np.asarray(path),{'passed':True,'order':order,'waypoints':len(path),'max_joint_step_rad':step,'minimum_hand_table_clearance_m':float(minimum),'collision_scope':'actual hand vertex/table checks; not full robot/environment collision proof','rejected_paths':attempts}
  except RuntimeError as exc:attempts.append({'order':order,'reason':str(exc)})
 error=RuntimeError('No preflighted return-home path');error.attempts=attempts;raise error
