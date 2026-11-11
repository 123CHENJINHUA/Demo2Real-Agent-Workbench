"""Object-relative targets and bounded Nero IK, independent of Isaac Sim."""
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation, Slerp

ROOT = Path(__file__).resolve().parents[4]
TABLE = .78
BASES = np.array([[0., -.375, .802], [0., .375, .802]])
CONTACT_OFFSET = np.array([0., 0., .130])
GRIP_X = (.016, -.019)
ASSEMBLY_R = np.array([[0., 1., 0.], [-1., 0., 0.], [0., 0., 1.]])
SEEDS = np.array([[-.766, .8733, .2238, 1.4612, -2.4415, -.7235, -.4569],
                  [.7022, .8600, -.1314, 1.4733, -.7347, -.6841, .4888]])


def transform(rotation=None, position=None):
    result = np.eye(4)
    if rotation is not None:
        result[:3, :3] = rotation
    if position is not None:
        result[:3, 3] = position
    return result


def quat(matrix):
    return Rotation.from_matrix(matrix[:3, :3]).as_quat()[[3, 0, 1, 2]]


def from_pose(position, quaternion):
    return transform(Rotation.from_quat(np.asarray(quaternion)[[1, 2, 3, 0]]).as_matrix(), position)


def blend(first, second, fraction):
    u = np.clip(fraction, 0., 1.)
    u = u*u*(3-2*u)
    rotation = Slerp([0., 1.], Rotation.from_matrix(np.stack((first[:3, :3], second[:3, :3]))))([u]).as_matrix()[0]
    return transform(rotation, (1-u)*first[:3, 3]+u*second[:3, 3])


def grasp_target(object_pose, arm, clearance=0.):
    """Approach the rear barrel from above; grasp is recomputed after repositioning."""
    x = object_pose[:3, 0].copy()
    x[2] = 0.
    x /= np.linalg.norm(x)
    z = np.array([0., 0., -1.])
    rotation = np.column_stack((x, np.cross(z, x), z))
    contact = object_pose[:3, 3]+object_pose[:3, :3]@np.array([GRIP_X[arm], 0., 0.])
    position = contact-rotation@CONTACT_OFFSET
    position[2] += clearance
    return transform(rotation, position)


def assembly_targets(gap, center=(-.35, 0., .93)):
    return [transform(ASSEMBLY_R, np.array(center)+[0., sign*gap/2, 0.]) for sign in (-1, 1)]


def align_axis(rotation, target_axis):
    """Align cylinder axes while retaining unconstrained roll about the shaft."""
    source = rotation[:, 0]
    target = np.asarray(target_axis)/np.linalg.norm(target_axis)
    cross = np.cross(source, target)
    sine = np.linalg.norm(cross)
    cosine = np.clip(source@target, -1., 1.)
    if sine < 1e-10:
        return rotation.copy() if cosine > 0 else Rotation.from_rotvec(np.pi*rotation[:, 1]).as_matrix()@rotation
    return Rotation.from_rotvec(cross/sine*np.arctan2(sine, cosine)).as_matrix()@rotation


def insertion_error(objects):
    male, female = objects
    tip = male[:3, 3]+male[:3, :3]@[-.028, 0., 0.]
    mouth = female[:3, 3]+female[:3, :3]@[.029, 0., 0.]
    axis = female[:3, 0]
    difference = tip-mouth
    # Female +X points out of the socket. Insertion is negative along this axis.
    depth = -float(difference@axis)
    lateral = float(np.linalg.norm(difference-(difference@axis)*axis))
    angle = float(np.arccos(np.clip(male[:3, 0]@female[:3, 0], -1., 1.)))
    return dict(depth_m=depth, lateral_error_m=lateral, angle_rad=angle)


class NeroIK:
    """URDF FK and bounded IK; joint limits are enforced, never silently clipped."""
    def __init__(self):
        xml = ET.parse(ROOT/"assets/nero/nero.urdf").getroot()
        self.origins, self.limits = [], []
        self.tail = np.eye(4)
        for joint in xml.findall("joint"):
            if joint.find("child").get("link") in ("gripper_link1", "gripper_link2"):
                continue
            origin = joint.find("origin")
            t = transform(Rotation.from_euler("xyz", np.fromstring(origin.get("rpy", "0 0 0"), sep=" ")).as_matrix(), np.fromstring(origin.get("xyz", "0 0 0"), sep=" "))
            if joint.get("type") == "revolute":
                self.origins.append(t)
                limit = joint.find("limit")
                self.limits.append([float(limit.get("lower")), float(limit.get("upper"))])
            else:
                self.tail = self.tail@t
        self.limits = np.array(self.limits)

    def fk(self, joints):
        pose = np.eye(4)
        for origin, angle in zip(self.origins, joints):
            c, s = np.cos(angle), np.sin(angle)
            pose = pose@origin@np.array([[c, -s, 0., 0.], [s, c, 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]])
        return pose@self.tail

    def solve(self, world_target, arm, seed, multistart=False):
        target = world_target.copy()
        target[:3, 3] -= BASES[arm]
        def residual(q):
            current = self.fk(q)
            return np.r_[(current[:3, 3]-target[:3, 3])*10.,
                         Rotation.from_matrix(target[:3, :3].T@current[:3, :3]).as_rotvec(),
                         1e-5*(q-seed)]
        seeds = [seed]
        if multistart:
            seeds += [SEEDS[arm], *np.random.default_rng(17+arm).uniform(self.limits[:, 0], self.limits[:, 1], (24, 7))]
        best = None
        for initial in seeds:
            initial = np.clip(initial, self.limits[:, 0]+1e-7, self.limits[:, 1]-1e-7)
            result = least_squares(residual, initial, bounds=(self.limits[:, 0], self.limits[:, 1]), max_nfev=70,
                                   ftol=1e-9, xtol=1e-9, gtol=1e-9)
            error = residual(result.x)
            if best is None or np.linalg.norm(error[:6]) < best[0]:
                best = np.linalg.norm(error[:6]), result.x
            if np.linalg.norm(error[:3]) < .003 and np.linalg.norm(error[3:6]) < .008:
                return result.x
        raise RuntimeError(f"Unreachable target for arm {arm}: {target[:3,3].tolist()}, residual={best[0]:.5f}")

    def solve_near_demo(self, world_target, arm, seed, reference):
        """Keep the demonstrated redundant-arm posture while respecting URDF limits."""
        target = world_target.copy()
        target[:3, 3] -= BASES[arm]
        def residual(q):
            current = self.fk(q)
            return np.r_[100.*(current[:3,3]-target[:3,3]),
                         Rotation.from_matrix(target[:3,:3].T@current[:3,:3]).as_rotvec(),
                         .008*(q-reference), .003*(q-seed)]
        initial = np.clip(seed, self.limits[:,0]+1e-7, self.limits[:,1]-1e-7)
        fit = least_squares(residual, initial, bounds=(self.limits[:,0],self.limits[:,1]),
                            max_nfev=100, ftol=1e-8, xtol=1e-8, gtol=1e-8)
        error = residual(fit.x)
        if np.linalg.norm(error[:3])/100 > .002 or np.linalg.norm(error[3:6]) > .035:
            # Retraction may need a different feasible redundant-arm branch.
            restart = np.clip(reference,self.limits[:,0]+1e-7,self.limits[:,1]-1e-7)
            candidate = least_squares(residual,restart,bounds=(self.limits[:,0],self.limits[:,1]),max_nfev=150)
            if np.linalg.norm(residual(candidate.x)[:6])<np.linalg.norm(error[:6]):
                fit = candidate
                error = residual(fit.x)
            if np.linalg.norm(error[:3])/100 > .002 or np.linalg.norm(error[3:6]) > .035:
                raise RuntimeError(f'Demo-retarget IK arm={arm}: position={np.linalg.norm(error[:3])/100:.4f}m, rotation={np.linalg.norm(error[3:6]):.4f}rad')
        return fit.x
