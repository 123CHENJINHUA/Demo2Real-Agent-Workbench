"""Whitelisted plugin construction and offline config switching."""
from pathlib import Path
import json,uuid
import yaml
from modules.policies.scripted.rule_supervisor import RuleSupervisor
from modules.policies.scripted.demo_motion import DemoMotion
from modules.policies.neural.state_supervisor import StateSupervisor

ROLES={"rule_supervisor_v1":"supervisor","state_mlp_v1":"supervisor","demo_motion_v1":"motion"}

def get_active(root):
    value=yaml.safe_load((Path(root)/"config/policies.yaml").read_text()) or {}
    return value|{"mode":"offline_only","hardware_motion_triggered":False}

def set_active(root,payload):
    root=Path(root).resolve();role=payload.get("role");plugin=payload.get("plugin_id")
    if role not in ("supervisor","motion") or ROLES.get(plugin)!=role:raise ValueError("插件角色不兼容或未实现")
    value=get_active(root);value.pop("mode",None);value.pop("hardware_motion_triggered",None)
    config=value["implementations"][plugin]
    if plugin=="state_mlp_v1":
        raw=payload.get("checkpoint") or config.get("checkpoint")
        if not raw:raise ValueError("神经策略需要真实checkpoint")
        path=(root/raw).resolve();path.relative_to(root)
        model=StateSupervisor(path)
        if payload.get("dependency_signature")!=model.metadata["dependency_signature"]:raise ValueError("必须显式匹配任务/观测/动作/资产/标定/评价冻结签名")
        config["checkpoint"]=str(path.relative_to(root));config["dependency_signature"]=model.metadata["dependency_signature"]
    value["active"][role]=plugin
    path=root/"config/policies.yaml";tmp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp");tmp.write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False));tmp.replace(path)
    return get_active(root)

def create(root,role,*,clock=None,reference=None):
    root=Path(root).resolve();config=get_active(root);plugin=config["active"].get(role)
    if ROLES.get(plugin)!=role:raise ValueError("Unknown/incompatible policy plugin")
    if plugin=="rule_supervisor_v1":return RuleSupervisor()
    if plugin=="state_mlp_v1":
        spec=config["implementations"][plugin];path=(root/spec["checkpoint"]).resolve();path.relative_to(root);policy=StateSupervisor(path)
        if policy.metadata["dependency_signature"]!=spec.get("dependency_signature"):raise ValueError("active checkpoint dependency changed")
        return policy
    if plugin=="demo_motion_v1":
        if clock is None or not reference:raise ValueError("Motion reference and monotonic clock required")
        return DemoMotion(reference["times"],reference["joints"],clock)
    raise ValueError("Unimplemented policy")

def list_packages(root):
    root=Path(root).resolve();items=[];directory=root/"deployments"
    if directory.exists():
        for p in directory.glob("*/manifest.json"):
            if p.is_symlink() or not p.resolve().is_relative_to(root):continue
            try:
                m=json.loads(p.read_text());items.append({"path":str(p.parent.relative_to(root)),"manifest":m})
            except (ValueError,OSError):continue
    return {"items":items,"real_motion_authorized":False}
