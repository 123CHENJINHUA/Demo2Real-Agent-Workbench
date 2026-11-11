from pathlib import Path
import argparse,json,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modules.simulation import run
p=argparse.ArgumentParser();p.add_argument("--mode",choices=["keyed_bench","physics","rgbd"],default="keyed_bench");p.add_argument("--gui",action="store_true");p.add_argument("--spread",type=float,default=0);p.add_argument("--yaw-spread",type=float,default=0);p.add_argument("--seed",type=int,default=42);p.add_argument("--pose-noise",type=float,default=0.);p.add_argument("--pose-delay",type=float,default=0.);p.add_argument("--disturbance",choices=["none","push","drop","relocate","occlude","remove"],default="none");args=p.parse_args()
output=ROOT/"runs"/("connector_cli_"+time.strftime("%Y%m%d_%H%M%S"))/"simulation"
ctx={"root":ROOT,"run_id":output.parent.name,"output_dir":output,"input":{"task_plugin":"connector","simulation_mode":args.mode,"headless":not args.gui,"spread":args.spread,"yaw_spread":args.yaw_spread,"seed":args.seed,"pose_noise":args.pose_noise,"pose_delay":args.pose_delay,"disturbance":args.disturbance},"config":{},"emit":lambda t,v:print(json.dumps({"type":t,"data":v},ensure_ascii=False),flush=True),"check_cancel":lambda:None}
result=run(ctx);print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(0 if result["status"]=="succeeded" else 1)
