"""Score the rigid panel proxy from measured PhysX states."""
import numpy as np
from scipy.spatial.transform import Rotation


def assess(trace, experiment):
    evidence = experiment['acceptance']
    panel, source, goal = trace['panel_m'],trace['source_m'],trace['goal_m']
    panel_size = np.asarray(experiment['sizes_m']['panel'])
    slot_size = np.asarray(experiment['sizes_m']['goal'])
    yaw = experiment.get('object_frame_z_deg',{})
    opening = (experiment.get('slot_openings') or {}).get('goal',{})
    target_local = np.array([0.,opening.get('across_center_m',0.),panel_size[2]/2+opening.get('floor_z_m',-.24*slot_size[2])])
    goal_rotation = Rotation.from_quat(trace['goal_wxyz'][[1,2,3,0]])*Rotation.from_euler('z',yaw.get('goal',0.),degrees=True)
    goal_relative = goal_rotation.inv().apply(panel-goal)
    panel_rotation = Rotation.from_quat(trace['panel_wxyz'][:,[1,2,3,0]])*Rotation.from_euler('z',yaw.get('panel',0.),degrees=True)
    relative_rotation=(goal_rotation.inv()*panel_rotation).as_matrix()
    low,high=np.asarray(opening.get('panel_footprint_xy_bounds_m',[-panel_size[:2]/2,panel_size[:2]/2]))
    depth=opening.get('insertion_depth_m',panel_size[2])
    center=np.r_[(high+low)/2,(depth-panel_size[2])/2]
    half_size=np.r_[(high-low)/2,depth/2]
    footprint=goal_relative+relative_rotation@center
    footprint_extents=np.abs(relative_rotation)@half_size
    displacement = np.linalg.norm(panel[:,:2]-panel[0,:2],axis=1)
    error = goal_relative-target_local
    distance = np.linalg.norm(error[:,:2],axis=1)
    tail = trace['times_s'] >= trace['times_s'][-1]-evidence['hold_s']
    minimum_lift = max(evidence['lift_m'],.37*experiment['sizes_m']['source'][2])
    lift = float(np.max(panel[:,2]-panel[0,2]))
    separation = float(np.linalg.norm(goal[:2]-source[:2]))
    tilt = np.degrees(np.arccos(np.clip(panel_rotation.as_matrix()[:,2,2],-1,1)))
    # Minimum distance from either articulated finger to the rigid panel's box.
    relative_fingers = trace['finger_m']-panel[:,None,:]
    local_fingers = np.einsum('tji,tfj->tfi',panel_rotation.as_matrix(),relative_fingers)
    finger_clearance = np.linalg.norm(np.maximum(np.abs(local_fingers)-panel_size/2,0.),axis=-1)
    seated = bool(np.all(np.abs(error[tail,2])<=evidence['height_error_m'])
                  and np.all(tilt[tail]<=evidence['tilt_deg'])
                  and np.all((np.abs(footprint[:,:2]-target_local[:2])+footprint_extents[:,:2])[tail]
                             <= np.array([slot_size[0],opening.get('gap_m',.64*slot_size[1])])/2+.003))
    released = bool(np.all(finger_clearance[tail]>=evidence['release_clearance_m']))
    held = bool(np.max(np.linalg.norm(panel[tail]-panel[-1],axis=1))<=evidence['hold_translation_m']
                and np.max(trace['panel_speed_m_s'][tail])<=evidence['hold_speed_m_s'])
    transferred = bool(lift>=minimum_lift and np.max(displacement)>=separation*.6)
    # Continuous ranking gives training evidence even when the full frozen checks fail.
    reward = (1.5*float(np.clip((distance[0]-distance[-1])/max(.01,distance[0]),-1,1))
              +min(lift,minimum_lift)/minimum_lift*.75+.5*seated+.25*released+.25*held)
    return {'transferred':transferred,'seated':seated,'released':released,'retained':bool(seated and released and held),
            'passed':bool(transferred and seated and released and held),'reward':float(reward),
            'panel_peak_lift_m':lift,'panel_final_xy_error_m':float(distance[-1]),
            'panel_peak_travel_m':float(np.max(displacement)),'final_tilt_deg':float(tilt[-1]),
            'finger_tail_clearance_m':float(finger_clearance[tail].min())}
