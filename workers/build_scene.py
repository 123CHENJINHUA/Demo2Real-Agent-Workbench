"""Thin heavy-worker entry, starts Kit only after validating args and paths."""
from pathlib import Path
import argparse,sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument("--spec",required=True);p.add_argument("--output",required=True);args,kit=p.parse_known_args()
out=Path(args.output).resolve();out.relative_to(ROOT);spec=Path(args.spec).resolve();spec.relative_to(ROOT)
sys.argv=[sys.argv[0],*kit]
from isaacsim import SimulationApp
app=SimulationApp(dict(headless=True,multi_gpu=False,width=1280,height=800,enable_crashreporter=False,limit_cpu_threads=8,fast_shutdown=True,extra_args=[f"--/log/file={out}/kit.log","--/app/settings/persistent=false","--/app/settings/saveOnExit=false","--/app/settings/loadUserConfig=false","--/app/extensions/registryEnabled=false"]))
try:
    from modules.scene_building.isaac_builder import build
    build(ROOT,spec,out,app)
except BaseException as exc:
    import traceback
    traceback.print_exc();(out/"build_report.json").write_text(json.dumps({"status":"failed","error":type(exc).__name__+": "+str(exc)},indent=2));raise
finally:app.close()
