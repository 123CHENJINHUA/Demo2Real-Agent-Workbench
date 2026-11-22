"""Nero tool Jacobians shared by offline demonstration learners."""
import xml.etree.ElementTree as ET

import numpy as np
from scipy.spatial.transform import Rotation

from modules.motion_replay.inspect import JOINTS


def _chain(path):
    xml = ET.parse(path).getroot()
    by_child = {joint.find('child').get('link'):joint for joint in xml.findall('joint')}
    chain = []
    link = 'gripper_base'
    while link in by_child:
        joint = by_child[link]
        chain.append(joint)
        link = joint.find('parent').get('link')
    if [j.get('name') for j in reversed(chain) if j.get('type')!='fixed'] != list(JOINTS):
        raise ValueError('URDF chain to gripper_base does not match recorded Nero joints')
    prepared=[]
    for joint in reversed(chain):
        origin=joint.find('origin')
        xyz=np.fromstring(origin.get('xyz','0 0 0'),sep=' ') if origin is not None else np.zeros(3)
        rpy=np.fromstring(origin.get('rpy','0 0 0'),sep=' ') if origin is not None else np.zeros(3)
        axis=None
        if joint.get('type')!='fixed':
            axis=np.fromstring(joint.find('axis').get('xyz'),sep=' ')
            axis/=np.linalg.norm(axis)
        prepared.append((xyz,Rotation.from_euler('xyz',rpy).as_matrix(),axis))
    return prepared


class ToolKinematics:
    """Evaluate a registered arm and solve world-frame tool waypoints."""
    def __init__(self,path,base_pose):
        self.chain=_chain(path)
        self.base_position=np.asarray(base_pose['position_m'],dtype=float)
        self.base_rotation=Rotation.from_quat(np.asarray(base_pose['quaternion_wxyz'])[[1,2,3,0]]).as_matrix()

    def solve(self,position,orientation,seed,limits):
        """Return the best bounded pose and its remaining position/orientation errors."""
        lower,upper=limits.T
        joints=np.clip(seed,lower,upper)
        if orientation is None:
            orientation=Rotation.from_matrix(self.tool(joints[None])[1][0])
        best_error=float('inf')
        for _ in range(64):
            tcp,rot,jacobian=self.tool(joints[None])
            delta_pos=position-tcp[0]
            delta_rot=(orientation*Rotation.from_matrix(rot[0]).inv()).as_rotvec()
            distance,angle=np.linalg.norm(delta_pos),np.linalg.norm(delta_rot)
            delta=np.r_[delta_pos,.14*delta_rot]
            error=np.linalg.norm(delta)
            if error<best_error:
                best,best_error,residual=joints,error,(float(distance),float(np.rad2deg(angle)))
            if distance<.0015 and angle<np.deg2rad(1.):
                break
            jac=jacobian[0]
            step=jac.T@np.linalg.solve(jac@jac.T+np.eye(6)*.0016,delta)
            updated=np.clip(joints+np.clip(step,-.18,.18),lower,upper)
            if np.linalg.norm(updated-joints)<1e-5:
                break
            joints=updated
        return best,residual

    def tool(self,positions):
        point,rotation,axes,origins=self.forward(positions)
        tcp=point+rotation@np.array([0.,0.,.13])
        jacobian=np.concatenate((np.cross(axes,tcp-np.asarray(origins)),.14*np.asarray(axes)),axis=-1).transpose(1,2,0)
        return tcp,rotation,jacobian

    def forward(self,positions):
        rotation=np.broadcast_to(self.base_rotation,(len(positions),3,3)).copy()
        point=np.broadcast_to(self.base_position,(len(positions),3)).copy()
        axes,origins=[],[]
        for xyz,offset_rotation,axis in self.chain:
            point+=rotation@xyz
            rotation=rotation@offset_rotation
            if axis is None:
                continue
            axes.append(rotation@axis)
            origins.append(point.copy())
            rotation=rotation@Rotation.from_rotvec(positions[:,len(axes)-1,None]*axis).as_matrix()
        return point,rotation,np.asarray(axes),np.asarray(origins)


def joint_positions_world(path, base_pose, positions):
    point,_,_,origins = ToolKinematics(path,base_pose).forward(positions)
    base = np.broadcast_to(base_pose['position_m'],point.shape)
    return np.stack([base,*origins,point],axis=1)


def tool_jacobians(path, base_pose, positions, *, full=False):
    tcp,_,jacobian=ToolKinematics(path,base_pose).tool(positions)
    inverse = jacobian.transpose(0,2,1) @ np.linalg.inv(jacobian@jacobian.transpose(0,2,1)+np.eye(6)*.0009)
    return tcp,inverse if full else inverse[:,:,:3]
