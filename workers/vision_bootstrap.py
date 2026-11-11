"""Avoid editable .pth hooks from shared vision environment business sources."""
from pathlib import Path
import os,sys,runpy,json,importlib.util
ROOT=Path(__file__).resolve().parents[1]
site=Path(os.environ["WORKBENCH_VISION_SITE"]).resolve()
# -S preserves interpreter stdlib but intentionally skips site and .pth execution.
base_site=Path(sys.base_prefix)/"lib"/("python"+str(sys.version_info.major)+"."+str(sys.version_info.minor))/"site-packages"
sys.path[:0]=[str(ROOT),str(ROOT/"tasks/connector/legacy"),str(site),str(base_site)]
# Source packages always come from the copied project, before installed packages.
sys.path.insert(0,str(ROOT/"vendor/vision/third_party/sam3"))
if "--check-isolation" in sys.argv:
    print(json.dumps({name:importlib.util.find_spec(name).origin if importlib.util.find_spec(name) else None for name in ("sam3","torch","nvdiffrast","pytorch3d")},indent=2))
else:
    runpy.run_path(str(ROOT/"tasks/connector/legacy/scripts/connector_rgbd_vision_worker.py"),run_name="__main__")
