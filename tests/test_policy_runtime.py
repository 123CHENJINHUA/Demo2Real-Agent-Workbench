from dataclasses import replace
from pathlib import Path
import json
import numpy as np
import pytest
from contracts.policies import ObservationPacket,MotionProposal,dependency_signature
from modules.policies.scripted.rule_supervisor import RuleSupervisor
from modules.policies.scripted.demo_motion import DemoMotion
from modules.policies.neural.state_supervisor import StateSupervisor,FEATURES,LABELS
from modules.robot_backends.mock_robot import MockRobot
from modules.execution.gate import ExecutionGate
from modules.learning.supervisor_bc import train
from modules.evaluation import evaluate
from modules.deployment import export

VERSIONS={"task":"unit-test-fixture/v1","observation":"robot-camera-state/v1","action":"skill-decision/v1","asset":"test/v1","calibration":"test/v1","evaluation":"classification-only/v1"}

def observation(**kw):
    return replace(ObservationPacket(0,10.,"e1",(0.,)*7,(.02,.02),features=(.95,1.,.01,1.,.001,.1,.001,1.),skill="transport",source="mock"),**kw)

def test_policy_has_no_device_authority_and_handles_dropped_object():
    p=RuleSupervisor();p.reset("e1")
    assert p.predict(observation()).skill=="follow_reference"
    assert p.predict(observation(features=(.95,1.,.01,0.,.001,.1,.001,1.))).skill=="regrasp"
    assert p.predict(observation(features=(.95,1.,1.,1.,.001,.1,.001,1.))).skill=="wait"
    with pytest.raises(ValueError):p.predict(observation(source="simulator_truth"))
    with pytest.raises(ValueError):p.predict(observation(epoch="old"))

def test_action_gate_rejects_replay_epoch_expiry_and_bounds():
    robot=MockRobot((0.,)*7);gate=ExecutionGate(robot,lambda:10.,[(-2.,2.)]*7);gate.begin("e1")
    proposal=MotionProposal((.01,)*7,"e1",0,9.99,10.1)
    assert gate.apply(proposal,robot.joints)["accepted"]
    for bad in (proposal,replace(proposal,epoch="old",observation_sequence=1),replace(proposal,valid_until=9.99,observation_sequence=1),replace(proposal,joints_rad=(.5,)*7,observation_sequence=1)):
        with pytest.raises(ValueError):gate.apply(bad,robot.joints)
    gate.stop();assert robot.held
    with pytest.raises(ValueError):gate.apply(replace(proposal,observation_sequence=1),robot.joints)

def test_demo_motion_does_not_send_and_returns_valid_proposal():
    clock=[10.];p=DemoMotion([0.,1.],[(0.,)*7,(.02,)*7],lambda:clock[0]);p.reset("e1");clock[0]=10.5
    proposal=p.predict(observation(captured_at=10.5))
    assert proposal.joints_rad==pytest.approx((.01,)*7)
    assert p.predict(observation(captured_at=9.)) is None

def make_dataset(path):
    rng=np.random.default_rng(9);rows=[];labels=[];episodes=[];splits=[]
    for split in ("train","validation","test"):
        for label in range(5):
            for i in range(20):
                f=rng.normal(0,.025,8);f[label]+=1;rows.append(f);labels.append(label);episodes.append(split+str(label));splits.append(split)
    meta={"features":list(FEATURES),"observation_source":"camera_estimate","dependency_signature":dependency_signature(VERSIONS),"synthetic_test_fixture":True,"purpose":"implementation_conformance_only"}
    np.savez(path,features=rows,labels=labels,episode_ids=episodes,split=splits,metadata=json.dumps(meta))

def test_real_bc_heldout_export_and_policy_swap(tmp_path):
    dataset=tmp_path/"labeled.npz";make_dataset(dataset)
    result=train(dataset,tmp_path/"train",versions=VERSIONS,epochs=150)
    checkpoint=Path(result["checkpoint"]);model=StateSupervisor(checkpoint);model.reset("e1")
    scripted=RuleSupervisor();scripted.reset("e1")
    assert model.describe().action_contract==scripted.describe().action_contract
    assert model.predict(observation()).skill in LABELS
    report=evaluate(checkpoint,dataset,tmp_path/"evaluation")
    assert report["accuracy"]>.8
    assert report["closed_loop_task_evaluated"] is False
    package=export(tmp_path,checkpoint,tmp_path/"evaluation/evaluation_report.json",tmp_path/"package")
    assert package["manifest"]["real_motion_authorized"] is False
    assert package["manifest"]["mode"]=="offline_shadow_only"
    with (tmp_path/"evaluation/evaluation_report.json").open("w") as handle:json.dump({"checkpoint_sha256":"wrong"},handle)
    with pytest.raises(ValueError):export(tmp_path,checkpoint,tmp_path/"evaluation/evaluation_report.json",tmp_path/"bad")

def test_config_factory_switches_same_role_and_blocks_cross_role(tmp_path):
    import shutil
    from modules.policies.service import create,set_active,get_active
    root=Path(__file__).resolve().parents[1]
    (tmp_path/"config").mkdir();shutil.copy2(root/"config/policies.yaml",tmp_path/"config/policies.yaml")
    set_active(tmp_path,{"role":"supervisor","plugin_id":"rule_supervisor_v1"})
    assert isinstance(create(tmp_path,"supervisor"),RuleSupervisor)
    dataset=tmp_path/"labeled.npz";make_dataset(dataset)
    trained=train(dataset,tmp_path/"train",versions=VERSIONS,epochs=10)
    rel=str(Path(trained["checkpoint"]).relative_to(tmp_path))
    with pytest.raises(ValueError):set_active(tmp_path,{"role":"motion","plugin_id":"state_mlp_v1"})
    with pytest.raises(ValueError):set_active(tmp_path,{"role":"supervisor","plugin_id":"state_mlp_v1","checkpoint":rel,"dependency_signature":"wrong"})
    set_active(tmp_path,{"role":"supervisor","plugin_id":"state_mlp_v1","checkpoint":rel,"dependency_signature":dependency_signature(VERSIONS)})
    assert isinstance(create(tmp_path,"supervisor"),StateSupervisor)
    set_active(tmp_path,{"role":"supervisor","plugin_id":"rule_supervisor_v1"})
    assert isinstance(create(tmp_path,"supervisor"),RuleSupervisor)
