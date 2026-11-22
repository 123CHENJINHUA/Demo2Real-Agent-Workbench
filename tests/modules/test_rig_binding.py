import json

import numpy as np
from scipy.spatial.transform import Rotation

from modules.rig_binding import NERO_MODELS, discover
from modules.scene_building.rig_preview import registered_preview
from modules.task_specs import generate, load_snapshot


def test_nero_package_is_read_and_pinned_to_new_task(tmp_path):
    def write(path,value):
        path.write_text(json.dumps(value))

    models=tmp_path/"assets/nero"
    models.mkdir(parents=True)
    mesh=models/"link.stl"
    mesh.write_text("mesh fixture")
    joints="".join(f'<joint name="joint{i}" type="revolute"/>' for i in range(1,8))
    for value in NERO_MODELS.values():
        (tmp_path/value).write_text(f'<robot>{joints}<link name="base"><visual><geometry><mesh filename="{mesh.name}"/></geometry></visual></link></robot>')
    folder=tmp_path/"assets/calibration/teach_fixture"
    folder.mkdir(parents=True)
    identity=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
    relative=[row.copy() for row in identity]
    relative[1][3]=.72
    cameras={}
    tcps={}
    for arm in ("left","right","center"):
        camera_name=arm+"_wrist.json" if arm!="center" else "center.json"
        # Source bundle paths may predate a directory move.
        cameras[arm]="config/calibration/teach_fixture/"+camera_name
        camera={"serial":arm,"width":640,"height":480,"intrinsics":{"K":[[600,0,320],[0,600,240],[0,0,1]]},
                "T_color_depth":identity}
        if arm=="center":
            camera["T_world_color"]={"matrix":identity}
        else:
            flange=f"nero_{arm}_follower/flange"
            camera["hand_eye"]={"matrix":identity,"transform_name":"T_sdk_flange_color","parent_frame":flange}
            tcp_path=arm+"_tcp.json"
            tcps[arm]="config/calibration/teach_fixture/"+tcp_path
            write(folder/tcp_path,{"parent_frame":flange,"T_gripper_base_sdk_flange":identity,"T_flange_tcp":identity})
        write(folder/camera_name,camera)
    bundle={"schema_version":1,"synthetic":False,"complete":True,"world_frame":"calibration_board",
            "validation_basis":"user_override","measured_validation_passed":False,
            "camera_calibrations":cameras,"tcp_calibrations":tcps,
            "transforms":{"T_world_nero_left_follower_base":{"matrix":identity},
                "T_world_nero_right_follower_base":{"matrix":relative},
                "T_nero_left_follower_base_nero_right_follower_base":{"matrix":relative}}}
    write(folder/"bundle.json",bundle)
    metadata={"robot_type":"nero_dual_follower"}
    assert discover(tmp_path,{"robot_type":"other"}) is None
    result=generate(tmp_path,"fixture",dependencies={"demo_import":{"metadata":metadata}})
    spec=load_snapshot(tmp_path,result["spec_path"],result["spec"]["content_hash"])
    rig=spec["robot"]["recorded_rig"]
    assert rig["bundle_id"]=="teach_fixture"
    assert rig["status"]=="needs_scene_registration"
    assert rig["T_left_sdk_base_right_sdk_base"][1][3]==.72
    assert not rig["measured_validation_passed"] and not spec["contact_execution_authorized"]
    assert set(rig["cameras"])=={"left","right","center"}
    assert set(rig["T_sdk_flange_tcp"])=={"left","right"}
    assert "assets/nero/link.stl" in spec["dependency_hashes"]["assets"]
    for values in rig["calibration_files"].values():
        assert values["path"].startswith("assets/calibration/teach_fixture/")
        assert spec["dependency_hashes"]["evidence"][values["path"]]==values["sha256"]
    assert discover(tmp_path,metadata,"wrong")["status"]=="needs_configuration"
    spec["scene"]["table"]={"position_m":[0,0,.7],"size_m":[1.2,.6,.1]}
    preview=registered_preview(tmp_path,spec,rig,None)
    assert "arms" not in spec["robot"]
    assert preview["robot"]["preview_registration"]["status"]=="estimated"
    assert preview["robot"]["preview_registration"]["initial_pose_source"]=="urdf_zero_pose"
    assert not preview["contact_execution_authorized"]
    arms=preview["robot"]["arms"]
    assert [arm["id"] for arm in arms]==["left","right"]
    transform=np.eye(4)
    transform[:3,:3]=Rotation.from_quat(np.asarray(arms[0]["base_pose"]["quaternion_wxyz"])[[1,2,3,0]]).as_matrix()
    separation=np.asarray(arms[1]["base_pose"]["position_m"])-arms[0]["base_pose"]["position_m"]
    assert np.allclose(transform[:3,:3].T @ separation,[0,.72,0])
    assert np.allclose([arm["base_pose"]["position_m"][2] for arm in arms],.75)
    assert all(arm["base_pose"]["position_m"][1]<-.3 for arm in arms)
    assert registered_preview(tmp_path,preview,rig,None) is None
