"""Isolated Isaac entry for demonstration learning and frozen placement holdouts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args,kit = parser.parse_known_args()
    out = args.output.resolve()
    out.relative_to(root)
    plan_file = args.plan.resolve()
    plan_file.relative_to(root)
    plan = json.loads(plan_file.read_text())
    for ref,digest in plan['hashes'].items():
        path = (root/ref).resolve()
        path.relative_to(root)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen expert learning input changed: '+ref)
    sys.argv = [sys.argv[0],*kit]
    from isaacsim import SimulationApp
    app = SimulationApp(dict(headless=True,disable_viewport_updates=True,multi_gpu=False,width=640,height=480,enable_crashreporter=False,
        limit_cpu_threads=8,fast_shutdown=True,extra_args=[f'--/log/file={out}/kit.log','--/app/settings/persistent=false',
        '--/app/settings/saveOnExit=false','--/app/settings/loadUserConfig=false','--/app/extensions/registryEnabled=false']))
    target = out/'expert_learning_report.json'
    try:
        if plan.get('engine')=='demonstration_action_program/v1':
            from application.action_training import train
        else:
            from application.placement_training import train
        result = train(root,out,plan,app)
    except Exception as exc:
        traceback.print_exc()
        result = {'status':'failed','error':f'{type(exc).__name__}: {exc}',
                  'failure_stage':'simulation_test','failure_category':'runtime_environment'}
    finally:
        if 'result' in locals():
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        app.close()


if __name__=='__main__':
    main()
