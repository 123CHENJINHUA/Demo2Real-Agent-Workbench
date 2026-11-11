"""Portable high-level behavior cloning; continuous motion remains separate."""
from pathlib import Path
import json,hashlib
import numpy as np
from contracts.policies import dependency_signature,OBSERVATION,SUPERVISOR_ACTION
from modules.policies.neural.state_supervisor import FEATURES,LABELS

def train(path, output, *, versions, epochs=200, seed=7, check_cancel=lambda:None, emit=lambda *args:None):
    signature=dependency_signature(versions)
    with np.load(path,allow_pickle=False) as d:
        x=np.asarray(d["features"],dtype=np.float64);y=np.asarray(d["labels"]);episodes=np.asarray(d["episode_ids"]);split=np.asarray(d["split"]);meta=json.loads(str(d["metadata"].item()))
    if x.ndim!=2 or x.shape[1]!=8 or not np.isfinite(x).all() or y.shape!=(len(x),) or episodes.shape!=y.shape or split.shape!=y.shape:raise ValueError("invalid feature/label dataset")
    if meta.get("features")!=list(FEATURES) or meta.get("observation_source")!="camera_estimate" or meta.get("dependency_signature")!=signature:raise ValueError("dataset observation/dependency contract mismatch")
    if not np.issubdtype(y.dtype,np.integer) or y.min()<0 or y.max()>=5 or not set(np.unique(split))<= {"train","validation","test"}:raise ValueError("invalid label/split")
    masks={name:split==name for name in ("train","validation","test")}
    if any(m.sum()<5 for m in masks.values()):raise ValueError("need >=5 train/validation/test samples each")
    episode_sets=[set(episodes[m].tolist()) for m in masks.values()]
    if any(episode_sets[i]&episode_sets[j] for i in range(3) for j in range(i+1,3)):raise ValueError("episode leakage across frozen splits")
    if not 1<=epochs<=2000:raise ValueError("epochs outside [1,2000]")
    mean=x[masks["train"]].mean(0);scale=np.maximum(x[masks["train"]].std(0),1e-5);z=(x-mean)/scale
    rng=np.random.default_rng(seed);w1=rng.normal(0,.18,(8,32));b1=np.zeros(32);w2=rng.normal(0,.18,(32,5));b2=np.zeros(5)
    zx=z[masks["train"]];yy=y[masks["train"]];trace=[];best=None;best_loss=float("inf")
    for epoch in range(epochs):
        check_cancel();h=np.tanh(zx@w1+b1);logits=h@w2+b2;logits-=logits.max(1,keepdims=True);p=np.exp(logits);p/=p.sum(1,keepdims=True)
        error=p.copy();error[np.arange(len(yy)),yy]-=1;error/=len(yy)
        gw2=h.T@error+1e-4*w2;gb2=error.sum(0);delta=(error@w2.T)*(1-h*h)
        w1-=.04*(zx.T@delta+1e-4*w1);b1-=.04*delta.sum(0);w2-=.04*gw2;b2-=.04*gb2
        val_logits=np.tanh(z[masks["validation"]]@w1+b1)@w2+b2;val_logits-=val_logits.max(1,keepdims=True);vp=np.exp(val_logits);vp/=vp.sum(1,keepdims=True)
        val_loss=float(-np.log(np.maximum(vp[np.arange(len(vp)),y[masks["validation"]]],1e-12)).mean())
        if val_loss<best_loss:best_loss=val_loss;best=[a.copy() for a in (w1,b1,w2,b2)]
        if epoch%10==0 or epoch==epochs-1:
            row={"epoch":epoch+1,"epochs":epochs,"validation_loss":val_loss};trace.append(row);emit("learning.progress",row)
    w1,b1,w2,b2=best;output=Path(output);output.mkdir(parents=True,exist_ok=True)
    metadata={"observation_contract":OBSERVATION,"action_contract":SUPERVISOR_ACTION,"features":list(FEATURES),"labels":list(LABELS),"dependency_signature":signature,"versions":versions,"dataset_sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest(),"scope":"supervised_skill_classification_only","hardware_verified":False,"source_metadata":meta}
    checkpoint=output/"state_supervisor.npz";np.savez_compressed(checkpoint,mean=mean,scale=scale,w1=w1,b1=b1,w2=w2,b2=b2,metadata=json.dumps(metadata))
    report={"status":"succeeded","checkpoint":str(checkpoint),"dependency_signature":signature,"dataset_sha256":metadata["dataset_sha256"],"scope":metadata["scope"],"hardware_verified":False,"synthetic_test_fixture":bool(meta.get("synthetic_test_fixture",False)),"train_samples":int(masks["train"].sum()),"validation_samples":int(masks["validation"].sum()),"test_samples_reserved":int(masks["test"].sum()),"trace":trace}
    (output/"training_report.json").write_text(json.dumps(report,indent=2));return report

def run(ctx):
    config=ctx["input"].get("learning",{});value=config.get("dataset_path")
    if not value:return {"status":"needs_configuration","reason":"需要本任务经相机观测生成并按episode冻结分割的专家标签NPZ；仿真通过不会自动产生已训练策略"}
    root=Path(ctx["root"]).resolve();path=(root/value).resolve();path.relative_to(root)
    return train(path,ctx["output_dir"],versions=config.get("versions",{}),epochs=int(config.get("epochs",200)),check_cancel=ctx["check_cancel"],emit=ctx["emit"])
