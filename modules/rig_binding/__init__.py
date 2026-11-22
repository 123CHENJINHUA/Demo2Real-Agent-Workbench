"""Discover a recorded robot's local model and calibration references."""
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree

import numpy as np

from contracts.tasks import rigid_transform


NERO_MODELS = {"left":"assets/nero/nero.urdf", "right":"assets/nero/nero_dual_2.urdf"}


def _project_file(root, path):
    if path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("所需配置文件不可用："+str(path.relative_to(root)))
    return path


def _signature(path):
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        while block := stream.read(1024*1024):
            digest.update(block)
        return digest.hexdigest()


def _matrix(value, label):
    matrix = value.get("matrix") if isinstance(value,dict) else value
    if not isinstance(matrix,list):
        raise ValueError(label+" 缺少 4×4 矩阵")
    rigid_transform(matrix,label)
    return np.array(matrix,dtype=float)


def _nero_bundle(data):
    transform_names = ("T_world_nero_left_follower_base", "T_world_nero_right_follower_base")
    return (isinstance(data,dict) and data.get("schema_version")==1 and data.get("synthetic") is False
            and all(key in data.get("transforms",{}) for key in transform_names))


def _select_bundle(root, requested):
    if requested is not None and (not isinstance(requested,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",requested)):
        raise ValueError("标定包名称无效")
    candidates = []
    for folder in (root/"calibration", root/"assets/calibration"):
        for path in sorted(folder.glob("*/bundle.json")):
            if requested and path.parent.name != requested:
                continue
            data = json.loads(_project_file(root,path).read_text())
            if _nero_bundle(data):
                candidates.append((path,data))
    if len(candidates)>1:
        raise ValueError("发现多个匹配 Nero 的标定包，请指定示教使用的标定包")
    return candidates[0] if candidates else (None,None)


def _models(root):
    models, assets = {}, {}
    for arm, relative in NERO_MODELS.items():
        urdf = _project_file(root,root/relative)
        tree = ElementTree.parse(urdf)
        joint_ids = {joint.get("name") for joint in tree.findall("joint") if joint.get("type")=="revolute"}
        if joint_ids != {f"joint{i}" for i in range(1,8)}:
            raise ValueError(f"{arm} URDF 与 Nero 录制关节定义不匹配")
        refs = {urdf}
        for mesh in tree.findall(".//mesh"):
            filename = mesh.get("filename","")
            if not filename or Path(filename).is_absolute() or ".." in Path(filename).parts or "://" in filename:
                raise ValueError("Nero URDF 包含无法登记的网格引用")
            refs.add(_project_file(root,urdf.parent/filename))
        assets.update({path.relative_to(root).as_posix():_signature(path) for path in sorted(refs) if path.relative_to(root).as_posix() not in assets})
        models[arm]={"urdf_path":relative,"sha256":assets[relative]}
    return models,assets


def joint_limits(path, names):
    robot = ElementTree.parse(path).getroot()
    limits = {}
    for name in names:
        joint = robot.find(f"joint[@name='{name}']")
        limit = joint.find("limit") if joint is not None and joint.get("type") == "revolute" else None
        if limit is None:
            raise ValueError(f"URDF 缺少关节限位 {name}")
        lower, upper = float(limit.get("lower")), float(limit.get("upper"))
        if not np.isfinite([lower, upper]).all() or lower >= upper:
            raise ValueError(f"URDF 关节限位无效 {name}")
        limits[name] = (lower, upper)
    return limits


def discover(root, metadata, requested=None):
    """Resolve only a recording with a supported, declared robot identity."""
    if metadata.get("robot_type")!="nero_dual_follower":
        return None
    root=Path(root).resolve()
    report={"robot_type":"nero_dual_follower","status":"needs_configuration","models":{},
            "assets":{},"calibration_files":{},"contact_execution_authorized":False}
    try:
        report["models"],report["assets"] = _models(root)
        selection = metadata.get("calibration_bundle") if requested is None else requested
        path,bundle = _select_bundle(root,selection)
        if not path:
            raise ValueError("未找到与该示教匹配的 Nero 标定包")
        if bundle.get("complete") is not True:
            raise ValueError("Nero 标定包的配置尚未齐备")
        folder=path.parent
        files={"bundle":path}
        # The bundle may have been moved as a unit. Resolve declared files in its own folder.
        for key,catalog in (("camera","camera_calibrations"),("tcp","tcp_calibrations")):
            roles=("left","right","center") if key=="camera" else ("left","right")
            for arm in roles:
                ref=Path(bundle[catalog][arm])
                if ref.parent.name!=folder.name or ref.suffix!=".json":
                    raise ValueError("标定包包含跨目录引用")
                files[key+"_"+arm]=_project_file(root,folder/ref.name)
        entries={key:json.loads(file.read_text()) for key,file in files.items() if key!="bundle"}
        transforms=bundle["transforms"]
        bases={arm:_matrix(transforms[f"T_world_nero_{arm}_follower_base"],arm+" 基座") for arm in NERO_MODELS}
        relative=_matrix(transforms["T_nero_left_follower_base_nero_right_follower_base"],"双臂基座关系")
        if not np.allclose(np.linalg.inv(bases["left"])@bases["right"],relative,rtol=0,atol=1e-5):
            raise ValueError("标定包的双臂基座变换不闭合")
        wrists={}
        tcps={}
        cameras={}
        for arm in ("left","right","center"):
            data=entries["camera_"+arm]
            K=data["intrinsics"]["K"]
            if np.shape(K)!=(3,3) or not np.isfinite(K).all() or K[0][0]<=0 or K[1][1]<=0:
                raise ValueError("相机内参无效")
            cameras[arm]={"serial":data["serial"],"width":data["width"],"height":data["height"],"intrinsics":K,
                          "T_color_depth":_matrix(data["T_color_depth"],arm+" RGB/深度外参").tolist()}
            if arm=="center":
                cameras[arm]["T_board_color"]=_matrix(data["T_world_color"],"中间相机外参").tolist()
                continue
            wrapper=data["hand_eye"]
            flange=f"nero_{arm}_follower/flange"
            if wrapper.get("transform_name")!="T_sdk_flange_color" or wrapper.get("parent_frame")!=flange:
                raise ValueError("腕部相机父坐标与 Nero 法兰不匹配")
            tcp=entries["tcp_"+arm]
            if tcp.get("parent_frame")!=flange:
                raise ValueError("TCP 父坐标与 Nero 法兰不匹配")
            wrists[arm]={"T_sdk_flange_color":_matrix(wrapper["matrix"],arm+" 手眼").tolist(),
                         "T_gripper_base_sdk_flange":_matrix(tcp["T_gripper_base_sdk_flange"],arm+" 夹爪到 SDK 法兰").tolist()}
            tcps[arm]=_matrix(tcp["T_flange_tcp"],arm+" TCP").tolist()
        report.update(status="needs_scene_registration",bundle_id=folder.name,
                      board_frame=bundle.get("world_frame"),board_placement_id=bundle.get("board_placement_id"),
                      validation_basis=bundle.get("validation_basis"),measured_validation_passed=bundle.get("measured_validation_passed") is True,
                      calibration_files={key:{"path":file.relative_to(root).as_posix(),"sha256":_signature(file)}
                                         for key,file in files.items()},
                      T_board_sdk_base={arm:T.tolist() for arm,T in bases.items()},
                      T_left_sdk_base_right_sdk_base=relative.tolist(),cameras=cameras,wrists=wrists,T_sdk_flange_tcp=tcps,
                      message="已读取 Nero 与标定。还需将标定板坐标注册到当前 Isaac 场景，并确认 SDK 基座与 URDF 根坐标的对应关系。")
    except (OSError,ValueError,KeyError,TypeError,ElementTree.ParseError) as exc:
        report["message"]="Nero 模型或标定读取受阻："+str(exc)
    return report
