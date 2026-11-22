"""Estimate a common world frame for a recorded dual-arm rig in a tabletop preview."""
import copy

import numpy as np
from scipy.spatial.transform import Rotation

from contracts.tasks import canonical_hash, validate_task_spec
from modules.rig_binding import joint_limits


def registered_preview(root, spec, rig, initial, audit_reference=None):
    table = spec["scene"].get("table", {})
    if spec["robot"].get("arms") or not rig or "T_board_sdk_base" not in rig:
        return None
    if table.get("position_m") is None or table.get("size_m") is None:
        return None

    left, right = (np.asarray(rig["T_board_sdk_base"][side]) for side in ("left", "right"))
    midpoint = (left[:3, 3] + right[:3, 3]) / 2
    up = left[:3, 2] + right[:3, 2]
    if np.linalg.norm(up) < 1e-6:
        raise ValueError("双臂基座无法估计共用桌面法向")
    up /= np.linalg.norm(up)
    across = right[:3, 3] - left[:3, 3]
    across -= up * np.dot(across, up)
    if np.linalg.norm(across) < 1e-6:
        raise ValueError("双臂基线无法估计桌面左右方向")
    across /= np.linalg.norm(across)
    rotation = np.stack((across, np.cross(up, across), up))
    center = np.asarray(rig["cameras"]["center"]["T_board_color"])
    center_offset = rotation @ (center[:3, 3] - midpoint)
    look = rotation @ center[:3, 2]
    _, depth, thickness = table["size_m"]
    # Keep the estimated mounting frame outside the tabletop, while aiming the center camera at it.
    setback = .12
    if center_offset[2] > 0 and look[2] < -.1 and look[1] > 0:
        tabletop_ray_y = center_offset[1] - look[1] * center_offset[2] / look[2]
        setback = float(np.clip(tabletop_ray_y - depth / 2, .09, .22))
    cx, cy, cz = table["position_m"]
    top = cz + thickness / 2
    world_board = np.eye(4)
    world_board[:3, :3] = rotation
    world_board[:3, 3] = [cx, cy - depth / 2 - setback, top] - rotation @ midpoint

    derived = copy.deepcopy(spec)
    derived["state"] = "draft"
    derived.pop("frozen_at", None)
    robot = derived["robot"]
    arms = []
    preview_adjustments = []
    for side, base in (("left", left), ("right", right)):
        path = rig["models"][side]["urdf_path"]
        world_base = world_board @ base
        q = Rotation.from_matrix(world_base[:3, :3]).as_quat()
        positions = {}
        if initial:
            for joint, (lower, upper) in joint_limits(root/path, initial["joints"][side]).items():
                recorded = initial["joints"][side][joint]
                shown = float(np.clip(recorded, lower, upper))
                positions[joint] = shown
                if shown != recorded:
                    preview_adjustments.append({"arm":side,"joint":joint,"recorded_rad":recorded,"preview_rad":shown})
            opening=(initial.get('gripper_opening_m') or {}).get(side)
            if opening is not None:
                half=float(np.clip(opening,0.,.09))/2
                positions.update(gripper_joint1=half,gripper_joint2=-half)
        arms.append({"id":side,"urdf_path":path,"base_pose":{"position_m":world_base[:3, 3].tolist(),
            "quaternion_wxyz":q[[3,0,1,2]].tolist()},"initial_joint_positions":positions,
            "camera":{"parent_link":"gripper_base","calibration_path":rig["calibration_files"]["camera_"+side]["path"],
                "T_parent_sdk_flange":rig["wrists"][side]["T_gripper_base_sdk_flange"]}})
    registration = {"status":"estimated","method":"tabletop_and_calibrated_dual_base_geometry/v1",
        "scope":"offline_visual_preview","bundle_id":rig["bundle_id"],"input_spec_hash":spec["content_hash"],
        "T_scene_calibration_board":world_board.tolist(),
        "T_sdk_base_urdf_root_assumption":"identity","tabletop_height_m":top,"base_setback_m":setback,
        "initial_pose_source":"first_recorded_joint_frame" if initial else "urdf_zero_pose",
        "initial_pose_evidence":initial,"preview_joint_adjustments":preview_adjustments,
        "center_camera":{"T_world_camera_optical":(world_board @ center).tolist(),
            "calibration_path":rig["calibration_files"]["camera_center"]["path"]},
        "message":"已按桌面和双臂基座关系自动估计场景对齐，用于离线预览。"}
    robot.update(arms=arms, recorded_rig=rig, preview_registration=registration)
    hashes = derived["dependency_hashes"]
    hashes.setdefault("assets", {}).update(rig["assets"])
    hashes.setdefault("evidence", {}).update({entry["path"]:entry["sha256"] for entry in rig["calibration_files"].values()})
    if initial:
        hashes["evidence"].update(initial["source_data_hashes"])
        if audit_reference:
            path, digest = audit_reference
            hashes["evidence"][path] = digest
    approx = derived.setdefault("scene_approximation", {"source_task_spec_hash":spec["content_hash"],
        "source_revision":spec["revision"],"proxy_objects":[],"scope":"approximate_scene_preview_only",
        "metric_geometry_verified":False})
    approx["robot_registration"] = registration["status"]
    derived["field_provenance"]["/robot/arms"] = {"source":registration["method"],"validation_scope":registration["scope"],
        "evidence_refs":[entry["path"] for entry in rig["calibration_files"].values()]}
    derived["assumptions"].append(registration["message"] + " SDK 基座与 URDF 根坐标暂按重合估计。")
    derived["unresolved"] = [value for value in derived["unresolved"] if value != rig.get("message")]
    derived["content_hash"] = canonical_hash(derived)
    return validate_task_spec(derived)
