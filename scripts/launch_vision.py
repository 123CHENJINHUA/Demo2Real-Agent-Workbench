from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.environment.service import local_environment,resolve

env=local_environment(ROOT);python=resolve(ROOT,"vision_python");cuda=resolve(ROOT,"cuda_home",False)
# Kit exports native paths that must never leak into standalone vision workers.
for key in ("LD_LIBRARY_PATH","LD_PRELOAD","CARB_APP_PATH","EXP_PATH","ISAAC_PATH","ISAACSIM_PATH"):
    env.pop(key,None)
if cuda:
    env["CUDA_HOME"]=str(cuda);env["PATH"]=str(cuda/"bin")+os.pathsep+env.get("PATH","")
    env["CPATH"]=str(cuda/"targets/x86_64-linux/include")
    env["LIBRARY_PATH"]=str(cuda/"targets/x86_64-linux/lib")
    env["LD_LIBRARY_PATH"]=os.pathsep.join([str(cuda/"lib"),str(cuda/"targets/x86_64-linux/lib")])
env["MAX_JOBS"]="2";env["OMP_NUM_THREADS"]="2";env["OPENBLAS_NUM_THREADS"]="2"
env["WORKBENCH_VISION_SITE"] = str(next((python.parent.parent/"lib").glob("python*/site-packages")))
os.chdir(ROOT/".runtime")
os.execve(str(python),[str(python),"-S","-u","-B",str(ROOT/"workers/vision_bootstrap.py"),*sys.argv[1:]],env)
