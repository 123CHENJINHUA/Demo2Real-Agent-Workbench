from pathlib import Path
import json,time,uuid
from dataclasses import asdict
from contracts.policies import ObservationPacket
from modules.robot_backends.nero_sdk import NeroRobotBackend
from modules.policies.scripted.rule_supervisor import RuleSupervisor

def status(root):
    caps=NeroRobotBackend(root).capabilities()
    return dict(backend="nero_sdk",mode="disconnected",connected=False,enabled=False,hardware_commands_sent=0,available_modes=["mock","shadow"],real_motion_available=False,sdk={"copied":caps["sdk_present"],"version":"1.0.0"},reason="SDK已隔离；尚未核验固件/通道/反馈与现场停止条件，不建立真实硬件连接")

def shadow(root,payload):
    if payload.get("policy_id","rule_supervisor_v1")!="rule_supervisor_v1":raise ValueError("此影子入口只使用已注册的规则策略；神经策略需显式已校验包")
    observations=payload.get("observations",[])
    if not isinstance(observations,list) or len(observations)>1000:raise ValueError("最多1000个显式提供的观测")
    policy=RuleSupervisor();decisions=[];epoch=None
    for raw in observations:
        fields=dict(raw)
        for key in ("joints_rad","gripper_m","features","objects"):
            if key in fields:fields[key]=tuple(fields[key])
        obs=ObservationPacket(**fields)
        if epoch!=obs.epoch:policy.reset(obs.epoch);epoch=obs.epoch
        decisions.append({"sequence":obs.sequence,"decision":asdict(policy.predict(obs))})
    result={"status":"succeeded","mode":"shadow","hardware_commands_sent":0,"observations":len(observations),"decisions":decisions}
    directory=Path(root)/"runs"/"shadow";directory.mkdir(parents=True,exist_ok=True)
    path=directory/(uuid.uuid4().hex+".json");path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    result["artifact"]=str(path.relative_to(root));return result
