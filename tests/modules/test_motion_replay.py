import json
from pathlib import Path

import numpy as np

from modules.motion_replay import inspect
from modules.motion_replay.inspect import JOINTS, limit_violations
from modules.motion_replay.timing import scene_keyframes, scene_motion


def test_scene_motion_detects_contacts_as_candidates_and_pauses_without_gripping():
    root=Path(__file__).resolve().parents[2]
    times=np.linspace(0.,5.,151)
    pose=np.interp(times,[0.,1.,2.,3.,5.],[0.,.4,.4,.8,.8])
    joints=np.zeros((len(times),2,7))
    joints[:,:,0]=pose[:,None]
    openings=np.full((len(times),2),.045)
    openings[:,0]=np.interp(times,[0.,1.3,1.6,3.,3.4,5.],[.045,.045,.018,.018,.045,.045])
    motion=scene_motion(root,{'left':'assets/nero/nero.urdf','right':'assets/nero/nero_dual_2.urdf'},
                        times,joints,openings)
    json.dumps(motion,allow_nan=False)
    assert {'closing','opening','pause'}<={event['kind'] for event in motion['events'] if event['arm']=='left'}
    assert {event['kind'] for event in motion['events'] if event['arm']=='right'}=={'pause'}
    samples=scene_keyframes(motion['events'],4)
    assert {'closing','opening','pause'}<={event['kind'] for event in samples}


def test_urdf_limit_report_keeps_recorded_values_and_first_frame():
    times=np.array([0., .1, .2, .3])
    values=np.zeros((4,len(JOINTS)))
    values[:,3]=[2.,2.16,2.19,2.14]
    limits=dict.fromkeys(JOINTS,(-1.01,2.14))
    violations=limit_violations(times,values,limits)
    assert violations==[{"joint":"joint4","sample_count":2,"minimum_recorded_rad":2.,
        "maximum_recorded_rad":2.19,"lower_limit_rad":-1.01,"upper_limit_rad":2.14,
        "first_frame":1,"first_timestamp_s":.1}]
    assert list(values[:,3])==[2.,2.16,2.19,2.14]


def test_replay_clips_to_bound_scene_models_and_preserves_original(tmp_path, monkeypatch):
    robot_records=[]
    for side,lower,upper in (("left",-1.01,2.14),("right",-1.2,2.2)):
        path=tmp_path/(side+".urdf")
        joints="".join(f'<joint name="{name}" type="revolute"><limit lower="{lower}" upper="{upper}"/></joint>'
                       for name in JOINTS)
        path.write_text("<robot>"+joints+"</robot>")
        robot_records.append({"id":side,"urdf_path":path.name})
    (tmp_path/"manifest.json").write_text("{}")
    (tmp_path/"scene.json").write_text(json.dumps({"robots":robot_records}))
    times=np.array([0.,.1,.2,.3])
    positions=np.zeros((4,2,len(JOINTS)))
    positions[:,0,3]=[1.9,2.19,2.14,np.nextafter(2.14,np.inf)]
    positions[1,1,0]=-1.25
    feedback=np.arange(8,dtype=float).reshape(4,2)
    monkeypatch.setattr(inspect,"read_episode",lambda *_: ({},times,positions,feedback,{}))

    report=inspect.inspect_episode(tmp_path,"manifest.json","scene.json",0,tmp_path)
    assert report["replay_limit_check_passed"]
    assert report["limit_adjustment"]["changed_joint_samples"]==3
    assert report["limit_adjustment"]["changed_frames"]==2
    assert report["reason"]=="该任务还需接入并独立验证接触专家"
    assert report["arms"]["left"]["limit_violations"][0]["sample_count"]==2
    assert not report["arms"]["left"]["replay_limit_violations"]
    with np.load(tmp_path/report["recorded_trajectory_path"]) as raw, np.load(tmp_path/report["trajectory_path"]) as replay:
        np.testing.assert_array_equal(raw["joint_positions_rad"],positions)
        expected=positions.copy()
        expected[[1,3],0,3]=2.14
        expected[1,1,0]=-1.2
        np.testing.assert_array_equal(replay["joint_positions_rad"],expected)
        np.testing.assert_array_equal(replay["timestamps_s"],times)
        np.testing.assert_array_equal(replay["gripper_feedback"],feedback)
