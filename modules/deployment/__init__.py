"""Export content-addressed offline/shadow package; no motor access."""
from pathlib import Path
import hashlib,json,shutil
from modules.policies.neural.state_supervisor import StateSupervisor

def export(root,checkpoint,evaluation,output):
    root=Path(root).resolve();checkpoint=Path(checkpoint).resolve();evaluation=Path(evaluation).resolve();output=Path(output).resolve()
    for p in (checkpoint,evaluation,output):p.relative_to(root)
    model=StateSupervisor(checkpoint);report=json.loads(evaluation.read_text());digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if report.get("checkpoint_sha256")!=digest or report.get("dependency_signature")!=model.metadata["dependency_signature"]:raise ValueError("evaluation/checkpoint signature mismatch")
    output.mkdir(parents=True,exist_ok=True);shutil.copy2(checkpoint,output/"policy.npz");shutil.copy2(evaluation,output/"evaluation.json")
    manifest={"schema_version":1,"plugin_id":"state_mlp_v1","role":"supervisor","mode":"offline_shadow_only","real_motion_authorized":False,"hardware_verified":False,"policy_sha256":digest,"dependency_signature":model.metadata["dependency_signature"],"versions":model.metadata["versions"],"evaluation_scope":report["scope"],"synthetic_test_fixture":bool(model.metadata.get("source_metadata",{}).get("synthetic_test_fixture",False)),"reason":"尚无本任务闭环接触/恢复和硬件评价，不开放真实运动"}
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2));return {"status":"succeeded","package":str(output.relative_to(root)),"manifest":manifest}

def run(ctx):
    config=ctx["input"].get("deployment",{})
    if not all(config.get(k) for k in ("checkpoint","evaluation")):return {"status":"needs_configuration","reason":"归档离线策略包需要绑定权重与配套评价报告"}
    root=Path(ctx["root"]).resolve();return export(root,root/config["checkpoint"],root/config["evaluation"],ctx["output_dir"])
