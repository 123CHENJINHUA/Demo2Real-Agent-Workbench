"""The calibration uses SDK MDH flange, not URDF gripper_base or firmware TCP."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[4]


def calibration():
    data = json.loads((ROOT/'assets/cameras/connector_hand_eye.json').read_text())
    # Constant fixed-frame conversion, checked against SDK MDH FK over episode 0.
    gripper_flange = np.array([[0., -1., 0., 0.], [0., 0., -1., -.0235],
                               [1., 0., 0., -.0375], [0., 0., 0., 1.]])
    return np.array(data['intrinsics']['K']), gripper_flange @ np.array(data['hand_eye']['matrix'])


def project(points, camera_pose, K):
    p = (np.linalg.inv(camera_pose) @ np.c_[points, np.ones(len(points))].T).T[:, :3]
    uvw = p @ K.T
    return uvw[:, :2] / uvw[:, 2:3]


def horizontal_pose(x, y, z, yaw):
    pose = np.eye(4)
    pose[:3, :3] = Rotation.from_euler('z', yaw).as_matrix()
    pose[:3, 3] = [x, y, z]
    return pose
