"""Verify an imported Nero episode against the local arm models and scene binding."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from modules.rig_binding import NERO_MODELS, discover, joint_limits
from modules.task_specs import load_snapshot

JOINTS = tuple(f"joint{number}" for number in range(1,8))


def _local_file(root, relative):
    path = root/relative
    if path.resolve()!=path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("输入文件不可用或位于项目外")
    return path


def _sha(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        while chunk:=stream.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def limit_violations(times, positions, limits):
    violations = []
    for joint, values in zip(JOINTS,positions.T):
        lower,upper = limits[joint]
        outside=np.flatnonzero((values<lower)|(values>upper))
        if not outside.size:
            continue
        violations.append({"joint":joint,"sample_count":int(outside.size),
            "minimum_recorded_rad":float(values.min()),"maximum_recorded_rad":float(values.max()),
            "lower_limit_rad":lower,"upper_limit_rad":upper,
            "first_frame":int(outside[0]),"first_timestamp_s":float(times[outside[0]])})
    return violations


def read_episode(root, manifest_ref, episode, *, first_only=False):
    import pyarrow.parquet as parquet

    root=Path(root).resolve()
    manifest_path=_local_file(root,manifest_ref)
    manifest=json.loads(manifest_path.read_text())
    dataset=manifest["result"]["dataset_path"]
    files={entry["path"]:entry["sha256"] for entry in manifest["files"]}
    def imported(relative):
        path=_local_file(root,f"{dataset}/{relative}")
        if _sha(path)!=files[relative]:
            raise ValueError("导入文件与清单哈希不符："+relative)
        return path
    info_path=imported("meta/info.json")
    info=json.loads(info_path.read_text())
    if info.get("robot_type")!="nero_dual_follower":
        raise ValueError("当前示教的机器人型号尚无离线回放绑定")
    fields=info.get("features",{}).get("observation.state",{}).get("names",[])
    channels=[f"{side}.{joint}.pos" for side in NERO_MODELS for joint in JOINTS]
    grippers=[f"{side}.gripper.pos" for side in NERO_MODELS]
    if any(fields.count(name)!=1 for name in channels+grippers):
        raise ValueError("录制关节字段缺失或重复")

    rows=[];sources={}
    references=sorted(path for path in files if path.startswith("data/") and path.endswith(".parquet"))
    if len(references)>256:
        raise ValueError("轨迹分片超出审查范围")
    for reference in references:
        path=imported(reference)
        filters=[("episode_index","=",episode)]
        if first_only:
            filters.append(("frame_index","=",0))
        frames=parquet.read_table(path,columns=["frame_index","timestamp","observation.state"],
                                  filters=filters)
        if frames.num_rows:
            if len(rows)+frames.num_rows>216000:
                raise ValueError("所选回合的轨迹帧数超出审查范围")
            sources[str(path.relative_to(root))]=files[reference]
            rows.extend(frames.to_pylist())
    rows.sort(key=lambda row:row["frame_index"])
    if len(rows)<(1 if first_only else 2) or [row["frame_index"] for row in rows]!=list(range(len(rows))):
        raise ValueError("所选回合的帧号不连续或有重复")
    times=np.asarray([row["timestamp"] for row in rows],dtype=float)
    state=np.asarray([row["observation.state"] for row in rows],dtype=float)
    positions=state[:,[fields.index(name) for name in channels]].reshape(len(rows),2,len(JOINTS))
    widths=state[:,[fields.index(name) for name in grippers]]
    if not all(np.isfinite(array).all() for array in (times,positions,widths)) or np.any(np.diff(times)<=0):
        raise ValueError("录制时间或关节值无效")
    return info,times,positions,widths,sources


def inspect_episode(root, manifest_ref, scene_ref, episode, output, calibration_bundle=None):
    root=Path(root).resolve()
    manifest_path=_local_file(root,manifest_ref)
    info,times,positions,widths,sources=read_episode(root,manifest_ref,episode)
    scene_path=_local_file(root,scene_ref)
    scene=json.loads(scene_path.read_text())
    scene_spec=load_snapshot(root,scene["spec_snapshot_path"],scene["task_spec_hash"]) if scene.get("spec_snapshot_path") else {}
    rig=scene_spec.get("robot",{}).get("recorded_rig") or discover(root,info,calibration_bundle)
    scene_arms={item["id"]:item for item in scene.get("robots",[]) if item.get("id")}
    spec_arms={item["id"]:item for item in scene_spec.get("robot",{}).get("arms",[])}
    replay=positions.copy()
    arms={}
    for index,(side,default_model) in enumerate(NERO_MODELS.items()):
        snapshot_model=spec_arms.get(side,{}).get("urdf_path")
        scene_model=scene_arms.get(side,{}).get("urdf_path")
        if snapshot_model and scene_model and snapshot_model!=scene_model:
            raise ValueError(f"{side} 在场景和任务快照中绑定的 URDF 不一致")
        reference=scene_model or snapshot_model or default_model
        model=_local_file(root,reference)
        limits=joint_limits(model,JOINTS)
        lower,upper=np.asarray([limits[joint] for joint in JOINTS]).T
        replay[:,index]=np.clip(positions[:,index],lower,upper)
        arms[side]={"urdf":reference,"sha256":_sha(model),
                    "limit_violations":limit_violations(times,positions[:,index],limits),
                    "replay_limit_violations":limit_violations(times,replay[:,index],limits)}
    bound_arms=set(scene_arms)
    registration=scene_spec.get("robot",{}).get("preview_registration")
    if rig and registration and set(NERO_MODELS).issubset(bound_arms):
        rig=rig | {"status":"estimated_scene_registration","message":registration["message"],
                   "scene_registration":registration}
    replay_limits_passed=all(not arm["replay_limit_violations"] for arm in arms.values())
    reasons=[]
    if not replay_limits_passed:
        reasons.append("离线回放轨迹的 URDF 限位复查未通过")
    if not set(NERO_MODELS).issubset(bound_arms):
        reasons.append(rig["message"] if rig else "当前场景缺少双臂基座和夹爪绑定")
    if not reasons:
        reasons.append("该任务还需接入并独立验证接触专家")

    output=Path(output).resolve()
    output.relative_to(root)
    timeline={"timestamps_s":times,"gripper_feedback":widths,"arms":list(NERO_MODELS),"joints":JOINTS}
    recorded=output/"recorded_trajectory.npz"
    np.savez_compressed(recorded,joint_positions_rad=positions,**timeline)
    changed=replay!=positions
    adjusted_samples=int(changed.sum())
    trajectory=recorded
    if adjusted_samples:
        trajectory=output/"bounded_replay_trajectory.npz"
        np.savez_compressed(trajectory,joint_positions_rad=replay,**timeline)
    report={"status":"blocked","scope":"recorded_joint_replay_preflight","reason":"；".join(reasons),
            "episode_index":episode,"sample_count":len(times),"duration_s":float(times[-1]-times[0]),
            "source_import_manifest":manifest_ref,"source_import_manifest_sha256":_sha(manifest_path),
            "source_data_hashes":sources,"scene_manifest":scene_ref,"scene_manifest_sha256":_sha(scene_path),
            "scene_task_spec_hash":scene.get("task_spec_hash"),"scene_robot_ids":sorted(value for value in bound_arms if value),
            "arms":arms,"rig_binding":rig,"contact_execution_authorized":False,"trajectory_path":trajectory.relative_to(root).as_posix(),
            "trajectory_sha256":_sha(trajectory),"recorded_trajectory_path":recorded.relative_to(root).as_posix(),
            "recorded_trajectory_sha256":_sha(recorded),"replay_limit_check_passed":replay_limits_passed,
            "limit_adjustment":{"method":"clip_to_urdf_joint_bounds","scope":"offline_replay_only",
                "changed_joint_samples":adjusted_samples,"changed_frames":int(changed.any(axis=(1,2)).sum())}}
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--scene",required=True)
    parser.add_argument("--episode",required=True,type=int)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--calibration-bundle")
    args=parser.parse_args()
    root=args.root.resolve()
    output=args.output.resolve()
    output.relative_to(root)
    output.mkdir(parents=True,exist_ok=True)
    try:
        report=inspect_episode(root,args.manifest,args.scene,args.episode,output,args.calibration_bundle)
    except (ImportError,OSError,ValueError,KeyError,TypeError) as exc:
        report={"status":"needs_configuration","reason":"回放输入无法审查："+str(exc),
                "scope":"recorded_joint_replay_preflight","contact_execution_authorized":False}
    target=output/"replay_preflight.json"
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


if __name__=="__main__":
    main()
