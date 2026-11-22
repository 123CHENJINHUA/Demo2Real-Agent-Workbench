"""Build a task snapshot through isolated Isaac; no physics imports in API."""
from pathlib import Path
import json,hashlib,subprocess,os,signal,time,fcntl
from contracts.tasks import validate_task_spec,canonical_hash
from modules.environment.service import resolve,local_environment

def checked_spec(root,reference):
    root=Path(root).resolve();path=(root/str(reference)).resolve();path.relative_to(root)
    if not path.is_file():raise ValueError("Missing immutable task spec")
    spec=json.loads(path.read_text());validate_task_spec(spec)
    if spec.get("content_hash")!=canonical_hash(spec):raise ValueError("Task content hash mismatch")
    deps=spec.get("dependency_hashes",{})
    groups=[deps.get("assets",{}),deps.get("evidence",{})]
    if deps.get("profile"):groups.append({deps["profile"]["path"]:deps["profile"]["sha256"]})
    for assets in groups:
        for value,expected in assets.items():
            item=(root/value).resolve();item.relative_to(root)
            if not item.is_file() or hashlib.sha256(item.read_bytes()).hexdigest()!=expected:raise ValueError("Task dependency changed: "+value)
    missing=[]
    if not spec.get("objects"):missing.append("objects")
    table=spec.get("scene",{}).get("table",{})
    for key in ("size_m","position_m"):
        if table.get(key) is None:missing.append("scene.table."+key)
    for i,obj in enumerate(spec.get("objects",[])):
        for key in ("position_m","quaternion_wxyz"):
            if obj.get("pose",{}).get(key) is None:missing.append(f"objects[{i}].pose.{key}")
        if obj.get("scale") is None:missing.append(f"objects[{i}].scale")
        dimensions={"box":["size_m"],"cylinder":["radius_m","height_m"],"sphere":["radius_m"]}.get(obj.get("geometry_type"),[])
        for field in dimensions:
            if obj.get(field) is None:missing.append(f"objects[{i}].{field}")
        if obj.get("mass_kg") is None:missing.append(f"objects[{i}].mass_kg")
        if obj.get("collision",{}).get("type","unknown")=="unknown":missing.append(f"objects[{i}].collision")
        if obj.get("geometry_type") not in ("mesh","box","cylinder"):missing.append(f"objects[{i}].geometry_type")
        if obj.get("geometry_type")=="mesh" and not obj.get("asset_path"):missing.append(f"objects[{i}].asset_path")
    return path,spec,missing

def _stop(process):
    if process and process.poll() is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=5)

def run(ctx):
    root=Path(ctx["root"]).resolve();out=Path(ctx["output_dir"]).resolve();out.relative_to(root)
    deps=ctx.get("dependencies",{});value=deps.get("scene_layout",{}).get("spec_path") or ctx["input"].get("spec_path") or deps.get("task_spec",{}).get("spec_path")
    if not value:return {"status":"needs_configuration","reason":"需要完整任务定义的不可变spec_path；不会从文件名猜场景"}
    try:path,spec,missing=checked_spec(root,value)
    except (ValueError,OSError) as exc:return {"status":"blocked","reason":str(exc)}
    if missing:return {"status":"needs_configuration","reason":"场景草案缺少明确参数："+", ".join(missing),"missing_fields":missing,"task_spec_hash":spec["content_hash"]}
    sim=resolve(root,"isaac_sim_root",False)
    if sim is None or not (sim/"python.sh").is_file():return {"status":"needs_configuration","reason":"配置Isaac Sim路径后才能真实构建/渲染"}
    out.mkdir(parents=True,exist_ok=True);build=out/("build_"+str(time.time_ns()));build.mkdir()
    snapshot=build/"task_spec.json";snapshot.write_text(json.dumps(spec,ensure_ascii=False,indent=2))
    command=[str(sim/"python.sh"),str(root/"workers/build_scene.py"),"--spec",str(snapshot),"--output",str(build),"--portable-root",str(root/".runtime/isaac-scene")]
    env=local_environment(root);env["OMP_NUM_THREADS"]="2";env["OPENBLAS_NUM_THREADS"]="2"
    lock=(root/".runtime/gpu.lock").open("a+")
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();return {"status":"blocked","reason":"GPU场景任务正被占用，请完成后重试"}
    process=None;log_path=build/"isaac_build.log"
    ctx["emit"]("scene.build_requested",{"spec_hash":spec["content_hash"],"spec_revision":spec["revision"],"scope":"scene_preview_and_local_settling_only"})
    try:
        with log_path.open("wb") as log,log_path.open("r",errors="replace") as reader:
            process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            started=time.monotonic();heartbeat=started
            while process.poll() is None:
                ctx["check_cancel"]()
                if time.monotonic()-started>600:raise TimeoutError("场景构建/渲染超过10分钟")
                for line in reader:
                    if line.startswith("SCENE_EVENT "):
                        try:event=json.loads(line[len("SCENE_EVENT "):]);ctx["emit"](event["type"],event["payload"])
                        except (ValueError,KeyError):pass
                if time.monotonic()-heartbeat>5:
                    ctx["emit"]("scene.heartbeat",{"pid":process.pid,"elapsed_s":round(time.monotonic()-started,1),"log":str(log_path.relative_to(root))});heartbeat=time.monotonic()
                time.sleep(.2)
        result_path=build/"build_report.json"
        if not result_path.is_file():return {"status":"failed","error":"Isaac构建退出但未生成build_report；查看isaac_build.log","failure_category":"runtime_environment","failure_stage":"scene_build","evidence_paths":[str(log_path.relative_to(root))]}
        result=json.loads(result_path.read_text())
        if process.returncode:result.update(status="failed",error=result.get("error") or "Isaac场景构建失败",failure_category="runtime_environment",failure_stage="scene_build",evidence_paths=[str(result_path.relative_to(root)),str(log_path.relative_to(root))])
        binding = spec.get("scene_approximation",{})
        return result|{"task_spec_hash":spec["content_hash"],"spec_revision":spec["revision"],"spec_path":str(path.relative_to(root)),"build_output":str(build.relative_to(root)),"hardware_verified":False,"contact_execution_authorized":False,"source_task_spec_hash":binding.get("source_task_spec_hash"),"scene_approximation":bool(binding)}
    finally:
        _stop(process);fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
