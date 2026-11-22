"""Fit a continuous Nero reference and condition it on independent placement changes."""
import numpy as np
from scipy.spatial.transform import Rotation

from modules.motion_replay.inspect import JOINTS
from modules.motion_replay.kinematics import tool_jacobians
from modules.rig_binding import joint_limits


def fit(root, arms, times, joints, gaps, panel_pose, panel_size, source, goal):
    """Learn time bases from measured continuous actions, then fit placement conditioning."""
    duration = float(times[-1]-times[0])
    if not 0 < duration <= 180:
        raise ValueError('The demonstration duration is outside the offline rollout budget')
    at = np.linspace(0,duration,int(np.ceil(duration*12))+1)
    t = (times-times[0])/duration
    centers = np.linspace(0,1,min(181,len(times)))
    bandwidth = 1.1/max(1,len(centers)-1)
    def features(phase):
        basis = np.exp(-.5*((phase[:,None]-centers)/bandwidth)**2)
        return basis/basis.sum(axis=1,keepdims=True)
    train = features(t)
    targets = np.column_stack((joints.reshape(len(times),14),gaps))
    coefficients = np.linalg.solve(train.T@train+np.eye(len(centers))*.0002,train.T@targets)
    prediction = features(at/duration)@coefficients
    reference = prediction[:,:14].reshape(len(at),2,7)
    reference[0] = joints[0]
    per_arm_limits = [joint_limits(root/arm['urdf_path'],JOINTS) for arm in arms]
    limits = np.array([[bounds[joint] for joint in JOINTS] for bounds in per_arm_limits])
    reference = np.clip(reference,limits[:,:,0],limits[:,:,1])
    tools = [tool_jacobians(root/arm['urdf_path'],arm['base_pose'],reference[:,i]) for i,arm in enumerate(arms)]
    tcp = np.stack([entry[0] for entry in tools],axis=1)
    horizontal = np.asarray(goal[:2])-np.asarray(source[:2])
    direction = horizontal/np.linalg.norm(horizontal)
    travel = (tcp[:,:,:2]-tcp[0,:,:2])@direction
    arm = int(np.argmax(np.ptp(travel,axis=0)))
    progress = travel[:,arm]
    # Keep the source offset during approach, and use the destination offset once the major transfer arrives.
    low,high = np.percentile(progress,[12,90])
    if high-low < .08:
        raise ValueError('The demonstration does not show enough tool travel along the placement axis')
    blend = np.maximum.accumulate(np.clip((progress-low)/(high-low),0.,1.))
    blend = blend*blend*(3-2*blend)
    feedback = prediction[:,14:]
    close_range,open_range = np.percentile(gaps,[15,90],axis=0)
    opening = np.where(feedback < close_range+.34*(open_range-close_range),0.,np.clip(feedback,0.,.09))
    closures = np.argwhere((opening[:-1]>.004)&(opening[1:]<=.004))+[1,0]
    seed = np.zeros((2,3))
    if len(closures):
        center = np.asarray(panel_pose['position_m'])
        grasp_frame, grasp_arm = min(closures,key=lambda row:np.linalg.norm(tcp[row[0],row[1]]-center))
        q = np.asarray(panel_pose['quaternion_wxyz'])
        rotation = Rotation.from_quat(q[[1,2,3,0]])
        source_tcp = tcp[grasp_frame,grasp_arm]
        proposal = rotation.inv().apply(source_tcp-center)
        grasp = np.clip(proposal,-.37*np.asarray(panel_size),.37*np.asarray(panel_size))
        grasp[1] = 0.
        seed[0] = np.clip(center+rotation.apply(grasp)-source_tcp,-.15,.15)
        # Start the transport from the corrected contact plane and remove only the normal error at the goal.
        normal = rotation.as_matrix()[:,1]
        seed[1] = normal*np.dot(seed[0],normal)
    return {'times_s':at,'reference_rad':reference,'gripper_opening_m':opening,'goal_weight':blend,
            'jacobian_inverse_m_to_rad':np.stack([entry[1] for entry in tools],axis=1),'limits_rad':limits,
            'grasp_alignment_seed_m':seed,
            'basis_centers':centers,'basis_bandwidth':np.array(bandwidth),'fitted_coefficients':coefficients,
            'fitting_rmse_rad':np.array(np.sqrt(np.mean((train@coefficients[:,:14]-targets[:,:14])**2)))}


def actions(model, source_offset, goal_offset, fitted_bias):
    weight = model['goal_weight'][:,None]
    offsets = np.asarray([np.r_[source_offset,0.],np.r_[goal_offset,0.]])+fitted_bias
    correction = (1-weight)*offsets[0]+weight*offsets[1]
    positions = model['reference_rad']+np.einsum('taij,tj->tai',model['jacobian_inverse_m_to_rad'],correction)
    limits = model['limits_rad']
    return np.clip(positions,limits[:,:,0],limits[:,:,1])
