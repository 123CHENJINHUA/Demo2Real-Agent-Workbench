"""Check recorded inputs at the handoff from scene building to contact simulation."""
import json
from pathlib import Path
import subprocess
import time

from modules.environment.service import local_environment, resolve


def run(ctx):
    source=ctx["dependencies"].get("demo_import") or {}
    build=ctx["dependencies"].get("scene_build") or {}
    scene=build.get("manifest_path")
    manifest=source.get("manifest_path")
    if not scene or not manifest:
        return {"status":"needs_configuration","reason":"场景已完成，目前缺少绑定的示教轨迹或可用接触专家。",
                "contact_execution_authorized":False}
    root=Path(ctx["root"]).resolve()
    interpreter=resolve(root,"data_python",False)
    if not interpreter or not interpreter.is_file():
        return {"status":"needs_configuration","reason":"请配置数据 Python，才能读取录制关节和校验轨迹。",
                "contact_execution_authorized":False}
    output=Path(ctx["output_dir"])/("replay_check_"+str(time.time_ns()))
    output.mkdir(parents=True)
    command=[str(interpreter),"-B","-m","modules.motion_replay.inspect","--root",str(root),
             "--manifest",manifest,"--scene",scene,"--episode",str(ctx["input"].get("episode_index",0)),
             "--output",str(output)]
    bundle=ctx["input"].get("calibration_bundle")
    if bundle is not None:
        command.extend(["--calibration-bundle",str(bundle)])
    ctx["check_cancel"]()
    ctx["emit"]("task.progress",{"message":"按 URDF 限位生成离线回放轨迹并核对场景绑定"})
    try:
        with (output/"replay_check.log").open("wb") as log:
            process=subprocess.run(command,cwd=root,env=local_environment(root),stdout=log,stderr=subprocess.STDOUT,timeout=90)
    except (OSError,subprocess.TimeoutExpired):
        return {"status":"failed","error":"读取示教轨迹失败或超时。","contact_execution_authorized":False}
    ctx["check_cancel"]()
    report_path=output/"replay_preflight.json"
    if process.returncode or not report_path.is_file():
        return {"status":"failed","error":"轨迹审查进程没有生成报告，请查看回放审查日志。",
                "contact_execution_authorized":False}
    report=json.loads(report_path.read_text())
    ctx["emit"]("motion.replay_checked",{"status":report["status"],"sample_count":report.get("sample_count"),
              "reason":report.get("reason"),"replay_limit_check_passed":report.get("replay_limit_check_passed"),
              "limit_adjustment":report.get("limit_adjustment")})
    return report | {"report_path":report_path.relative_to(root).as_posix()}
