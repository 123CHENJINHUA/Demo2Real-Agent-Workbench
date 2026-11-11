"""Thin isolated Isaac entry for a frozen scene-bound offline motion plan."""
from pathlib import Path
import argparse,json,sys,traceback
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--output',required=True);args,kit=p.parse_known_args()
out=Path(args.output).resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=True)
from modules.planning.expert_binding import load_plan
plan=load_plan(ROOT,args.plan)
sys.argv=[sys.argv[0],*kit]
from isaacsim import SimulationApp
app=SimulationApp(dict(headless=True,multi_gpu=False,width=1280,height=800,enable_crashreporter=False,limit_cpu_threads=8,fast_shutdown=True,
    extra_args=[f'--/log/file={out}/kit.log','--/app/settings/persistent=false','--/app/settings/saveOnExit=false','--/app/settings/loadUserConfig=false','--/app/extensions/registryEnabled=false']))
exit_code=1
try:
    if plan.get('recovery'):
        from modules.motion_expert.recovery_runtime import execute
    else:
        from modules.motion_expert.isaac_runtime import execute
    result=execute(ROOT,plan,out,app);exit_code=0 if result.get('action_passed') else 1
except BaseException as exc:
    traceback.print_exc()
    path=out/'expert_result.json'
    if not path.exists():path.write_text(json.dumps(dict(status='failed',action_passed=False,full_task_passed=False,retention_passed=None,
        error=type(exc).__name__+': '+str(exc),report={'passed':False,'error':str(exc)},hardware_commands_sent=0,expert_perception=plan.get('expert_perception','simulator_ground_truth'),
        plan_path=str(Path(args.plan).resolve()),task_spec_hash=plan.get('task_spec_hash'),spec_revision=plan.get('spec_revision'),scene_manifest_path=plan.get('scene_manifest_path')),ensure_ascii=False,indent=2))
finally:app.close()
sys.exit(exit_code)
