"""Small portable NumPy MLP; no devices, simulation or training imports."""
from pathlib import Path
import json
import numpy as np
from contracts.policies import PolicyDescriptor, SkillDecision, OBSERVATION, SUPERVISOR_ACTION

LABELS=("observe","follow_reference","regrasp","wait","abort")
FEATURES=("pose_confidence","visible_fraction","observation_age_s","holding_score","object_shift_m","joint_speed_rad_s","reference_error_m","target_visible")

class StateSupervisor:
    def __init__(self, path):
        with np.load(path,allow_pickle=False) as data:
            self.weights={k:data[k].copy() for k in ("mean","scale","w1","b1","w2","b2")}
            metadata=json.loads(str(data["metadata"].item()))
        if metadata.get("observation_contract")!=OBSERVATION or metadata.get("action_contract")!=SUPERVISOR_ACTION or metadata.get("features")!=list(FEATURES) or metadata.get("labels")!=list(LABELS):
            raise ValueError("checkpoint contract mismatch")
        w=self.weights
        if w["mean"].shape!=(8,) or w["scale"].shape!=(8,) or w["w1"].shape[0]!=8 or w["w2"].shape!=(w["w1"].shape[1],5) or w["b1"].shape!=(w["w1"].shape[1],) or w["b2"].shape!=(5,):
            raise ValueError("invalid MLP shapes")
        if any(not np.isfinite(v).all() for v in w.values()) or (w["scale"]<=0).any():raise ValueError("invalid weights")
        self.metadata=metadata
    def describe(self):
        return PolicyDescriptor("state_mlp_v1","supervisor",OBSERVATION,SUPERVISOR_ACTION,"1")
    def reset(self,epoch):self.epoch=epoch
    def predict(self,obs):
        obs.validate()
        if obs.epoch!=getattr(self,"epoch",None):raise ValueError("epoch mismatch")
        if len(obs.features)!=8:return SkillDecision("observe","需要8维特征")
        w=self.weights
        x=(np.asarray(obs.features)-w["mean"])/w["scale"]
        logits=np.tanh(x@w["w1"]+w["b1"])@w["w2"]+w["b2"]
        return SkillDecision(LABELS[int(logits.argmax())],"MLP技能建议；执行层独立验证时效/范围")
