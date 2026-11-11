from pathlib import Path
import os,sys,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modules.environment.service import local_environment,resolve
p=argparse.ArgumentParser();p.add_argument("--port",type=int,default=8787);args=p.parse_args()
python=resolve(ROOT,"app_python");env=local_environment(ROOT)
os.execve(str(python),[str(python),"-m","uvicorn","backend.main:app","--host","127.0.0.1","--port",str(args.port)],env)
