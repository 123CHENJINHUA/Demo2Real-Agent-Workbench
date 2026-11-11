"""Frozen held-out classification audit; never substitutes for robot success."""
from pathlib import Path
import hashlib,json
import numpy as np
from contracts.policies import ObservationPacket
from modules.policies.neural.state_supervisor import StateSupervisor,LABELS

def evaluate(checkpoint,dataset,output):
    model=StateSupervisor(checkpoint)
    if hashlib.sha256(Path(dataset).read_bytes()).hexdigest()!=model.metadata["dataset_sha256"]:raise ValueError("frozen dataset changed")
    with np.load(dataset,allow_pickle=False) as d:x=d["features"].copy();y=d["labels"].copy();split=d["split"].copy()
    m=split=="test"
    if m.sum()<5:raise ValueError("no held-out episodes")
    model.reset("evaluation");cm=np.zeros((5,5),dtype=int)
    for i,(features,target) in enumerate(zip(x[m],y[m])):
        obs=ObservationPacket(i,float(i),"evaluation",(),(),features=tuple(features),source="camera_estimate")
        pred=model.predict(obs);cm[int(target),LABELS.index(pred.skill)]+=1
    report={"status":"succeeded","scope":"held_out_skill_classification_only","samples":int(m.sum()),"accuracy":float(np.trace(cm)/m.sum()),"confusion_matrix":cm.tolist(),"closed_loop_task_evaluated":False,"hardware_verified":False,"dependency_signature":model.metadata["dependency_signature"],"checkpoint_sha256":hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()}
    output=Path(output);output.mkdir(parents=True,exist_ok=True);(output/"evaluation_report.json").write_text(json.dumps(report,indent=2));return report

def run(ctx):
    conf=ctx["input"].get("evaluation",{})
    if not conf.get("checkpoint") or not conf.get("dataset"):return {"status":"needs_configuration","reason":"需要冻结checkpoint和独立留出集；Connector专家历史成绩不能继承为学生策略评价"}
    root=Path(ctx["root"]).resolve();paths=[]
    for key in ("checkpoint","dataset"):
        p=(root/conf[key]).resolve();p.relative_to(root);paths.append(p)
    return evaluate(*paths,ctx["output_dir"])
