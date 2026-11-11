"""Launch migrated task plugin in its own configured Isaac process."""
from pathlib import Path
import fcntl,json,os,signal,subprocess,time
from modules.environment.service import local_environment, resolve

def command(root, output, mode="keyed_bench", headless=True, seed=42, spread=.0, yaw=0., disturbance="none", pose_noise=0., pose_delay=0.):
    root=Path(root).resolve();output=Path(output).resolve();output.relative_to(root)
    if mode not in ("keyed_bench","physics","rgbd"):raise ValueError("unknown simulation mode")
    if not isinstance(seed,int) or not 0<=seed<=2**31-1 or not 0<=float(spread)<=.05 or not 0<=float(yaw)<=30:raise ValueError("unsupported placement bounds")
    if not 0<=float(pose_noise)<=.005 or not 0<=float(pose_delay)<=.5:raise ValueError("unsupported observation noise/delay")
    if disturbance not in ("none","push","drop","relocate","occlude","remove"):raise ValueError("unknown disturbance")
    sim=resolve(root,"isaac_sim_root");launcher=sim/"python.sh"
    if not launcher.is_file():raise RuntimeError("Isaac Sim python.sh missing")
    args=[str(launcher),str(root/"tasks/connector/legacy/sim_bridge/connector_physics.py"),"--output-dir",str(output),"--connector-model","keyed","--portable-root",str(root/".runtime/isaac"),"--seed",str(seed)]
    if headless:args.append("--headless")
    if mode=="keyed_bench":args.append("--keyed-bench")
    else:
        args += ["--controller","dexmimic","--dex-contact-refine","--robust","--stop-after","release","--spread",str(spread),"--yaw-spread",str(yaw),"--disturbance",disturbance,"--perception","rgbd" if mode=="rgbd" else "surrogate","--pose-noise",str(pose_noise),"--pose-delay",str(pose_delay)]
        resolve(root,"isaac_lab_root");resolve(root,"training_python")
        if mode=="rgbd":
            resolve(root,"vision_python");args.append("--rgbd-visualize")
    return args

def run(ctx):
    root=Path(ctx["root"]).resolve();output=Path(ctx["output_dir"]).resolve();output.relative_to(root)
    inp=ctx["input"]
    if inp.get("task_plugin")!="connector":return {"status":"needs_configuration","reason":"通用任务场景构建需要完成资产/功能坐标/冻结判据配置。可显式选择已迁移Connector诊断插件。","supported_plugins":["connector"]}
    mode=inp.get("simulation_mode","physics")
    simout=output/("isaac_"+str(time.time_ns()))
    try:args=command(root,simout,mode,inp.get("headless",True),int(inp.get("seed",42)),float(inp.get("spread",0.)),float(inp.get("yaw_spread",0.)),inp.get("disturbance","none"),float(inp.get("pose_noise",0.)),float(inp.get("pose_delay",0.)))
    except (ValueError,RuntimeError) as exc:return {"status":"needs_configuration","reason":str(exc)}
    output.mkdir(parents=True,exist_ok=True)
    env=local_environment(root)
    env["PYTHONPATH"]=os.pathsep.join([str(root/"tasks/connector/legacy"),str(root)])
    env["OMP_NUM_THREADS"]="2";env["OPENBLAS_NUM_THREADS"]="2"
    lock=(root/".runtime/gpu.lock").open("a+")
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();return {"status":"blocked","reason":"GPU诊断资源正被另一个任务占用，请待其结束后重试"}
    ctx["emit"]("simulation.launch",{"mode":mode,"command":args,"perception":"rgbd" if mode=="rgbd" else "fixture" if mode=="keyed_bench" else "pose_surrogate","hardware_commands_sent":0})
    process=None
    try:
        with (output/"isaac_worker.log").open("wb") as log:
            process=subprocess.Popen(args,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            began=time.monotonic();heartbeat=began
            while process.poll() is None:
                ctx["check_cancel"]()
                if time.monotonic()-began>1800:raise TimeoutError("Isaac诊断超过30分钟上限")
                if time.monotonic()-heartbeat>=5:
                    ctx["emit"]("simulation.heartbeat",{"pid":process.pid,"elapsed_s":round(time.monotonic()-began,1),"log":str((output/"isaac_worker.log").relative_to(root))});heartbeat=time.monotonic()
                time.sleep(.2)
        name="bench_summary.json" if mode=="keyed_bench" else "summary.json"
        path=simout/name
        report=json.loads(path.read_text()) if path.is_file() else None
        result={"status":"succeeded" if process.returncode==0 and report and report.get("passed") else "failed","mode":mode,"returncode":process.returncode,"report":str(path.relative_to(root)) if path.exists() else None,"result":report,"pose_noise_m":float(inp.get("pose_noise",0.)),"pose_delay_s":float(inp.get("pose_delay",0.)),"hardware_verified":False,"perception":"rgbd" if mode=="rgbd" else "fixture" if mode=="keyed_bench" else "pose_surrogate"}
        retention_failures=[v for v in (report or {}).get("results",[]) if v.get("post_release_retained") is False]
        if retention_failures:
            warning="插入与松手动作完成时，松手后落桌的双卡扣保持验证仍未通过；不得算作完整锁止可靠性通过。"
            result["message"]=warning
            result["validation_warnings"]=[warning]
            result["issues"]=[{"failure_category":"physics_contact","failure_stage":"simulation_test","error":warning,"evidence_paths":[str(path.relative_to(root)),str((output/"isaac_worker.log").relative_to(root))]}]
            ctx["emit"]("validation.warning",{"category":"physics_contact","message":warning,"action_scope_only":True})
        if result["status"] == "failed":
            errors=[str(v.get("error")) for v in (report or {}).get("results",[]) if v.get("error")]
            result["error"]="; ".join(errors) or "Isaac diagnostic failed; inspect physical report and log"
            result["failure_category"]="runtime_environment" if any("ModuleNotFoundError" in e or "ImportError" in e for e in errors) else "planning_control" if any("IK" in e or "saturat" in e for e in errors) else "physics_contact" if report else "runtime_environment"
            result["failure_stage"]="simulation_test"
            result["evidence_paths"]=[str((output/"isaac_worker.log").relative_to(root))]+([str(path.relative_to(root))] if path.exists() else [])
        (output/"simulation_report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
