"""Bounded contact-centre regrasp planning; no simulator or device imports."""
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation,Slerp
from modules.geometry.functional import load_mesh

OFFSET=np.array([0.,0.,.130])
VERSION='contact_center_regrasp/v2'

def hand_geometry(root):
    root=Path(root);parts=[]
    v,_=load_mesh(root/'assets/nero/meshes/gripper_base.stl');parts.append(v)
    for gap in (0.,.06):
        for finger in (1,2):
            v,_=load_mesh(root/f'assets/nero/meshes/gripper_link{finger}.stl')
            R=Rotation.from_euler('xyz',[np.pi/2,0,np.pi if finger==1 else 0]).as_matrix()
            parts.append((v+[0,0,gap/2])@R.T+[0,0,.1358])
    return np.concatenate(parts)

def _pose(center,R):
    T=np.eye(4);T[:3,:3]=R;T[:3,3]=center-R@OFFSET;return T

def _curve(first,last,steps=30):
    a=first[:3,3]+first[:3,:3]@OFFSET;b=last[:3,3]+last[:3,:3]@OFFSET
    rotations=Slerp([0.,1.],Rotation.from_matrix([first[:3,:3],last[:3,:3]]))
    return [_pose((1-u)*a+u*b,rotations(u).as_matrix()) for u in np.linspace(0.,1.,steps+1)]

def plan_regrasp(ik,base,arm,seed,start,nominal,return_pose,vertices,table_z,other_object=None):
    """Keep jaw closing axis and contact centre; preflight approach, lift, return.

    Uses the existing solver tolerances. Collision scope is the actual hand
    vertices versus table and a conservative cylinder for the other connector;
    it is not a full-robot or unmodelled-camera collision proof.
    """
    base=np.asarray(base);seed=np.asarray(seed);center=nominal[:3,3]+nominal[:3,:3]@OFFSET;attempts=[]
    def check(T):
        points=vertices@T[:3,:3].T+T[:3,3];minimum=float(points[:,2].min())
        if minimum<table_z+.0005:raise RuntimeError('Finger/base mesh lacks tabletop clearance')
        if other_object is not None:
            local=(points-other_object[:3,3])@other_object[:3,:3]
            if np.any((np.abs(local[:,0])<.065)&(np.linalg.norm(local[:,1:],axis=1)<.017)):raise RuntimeError('Hand intersects other connector envelope')
        return minimum
    for tilt in (0,-5,5,-10,10,-15,15,-20,20,-25,25,-30,30):
        rotation=nominal[:3,:3]@Rotation.from_euler('y',tilt,degrees=True).as_matrix();grasp=_pose(center,rotation)
        try:ik.solve(grasp,arm,seed,True);check(grasp)
        except RuntimeError as exc:attempts.append({'tilt_deg':tilt,'stage':'grasp','error':str(exc)});continue
        for clearance in (.05,.03,.07):
            pre=_pose(center+[0,0,clearance],start[:3,:3]);lift=_pose(center+[0,0,.08],start[:3,:3]);targets={'approach':(start,pre,2.),'descend':(pre,grasp,2.),'lift':(grasp,lift,2.5),'return':(lift,return_pose,3.5)}
            q=seed.copy();segments={};minimum=np.inf;max_error=0.;max_angle=0.
            try:
                for name,(first,last,duration) in targets.items():
                    path=[]
                    for index,T in enumerate(_curve(first,last,int(duration*30))):
                        minimum=min(minimum,check(T))
                        try:solved=ik.solve(T,arm,q,False)
                        except RuntimeError:solved=ik.solve(T,arm,q,True)
                        count=max(1,int(np.ceil(np.max(np.abs(solved-q))/.015)))
                        for fraction in np.linspace(0.,1.,count+1)[1:]:
                            knot=q+fraction*(solved-q);actual=ik.fk(knot);actual[:3,3]+=base;minimum=min(minimum,check(actual));path.append(knot)
                        q=solved;actual=ik.fk(q);actual[:3,3]+=base;error=float(np.linalg.norm(actual[:3,3]-T[:3,3]));angle=float(Rotation.from_matrix(T[:3,:3].T@actual[:3,:3]).magnitude());max_error=max(max_error,error);max_angle=max(max_angle,angle)
                    segments[name]=np.asarray(path)
                report={'planner':VERSION,'passed':True,'selected_tilt_deg':tilt,'contact_center_world_m':center.tolist(),'approach_clearance_m':clearance,'jaw_closing_axis_preserved':bool(np.allclose(grasp[:3,1],nominal[:3,1])),'contact_center_preserved':True,'position_error_max_m':max_error,'angle_error_max_rad':max_angle,'hand_table_clearance_min_m':float(minimum-table_z),'collision_scope':'actual hand mesh vertices/table + other connector cylinder; not complete robot/environment collision checking','waypoints':{k:len(v) for k,v in segments.items()},'rejected_candidates':attempts}
                return segments,report
            except RuntimeError as exc:attempts.append({'tilt_deg':tilt,'clearance_m':clearance,'stage':name,'error':str(exc)})
    error=RuntimeError('No fully preflighted regrasp candidate; needs human');error.attempts=attempts;raise error
