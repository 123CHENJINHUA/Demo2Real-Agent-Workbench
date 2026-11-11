from pathlib import Path
import os,json
from pxr import Usd,Sdf
ROOT=Path(__file__).resolve().parents[1]
changes=[]
for path in (ROOT/"assets/connector_visuals").glob("connector_*.usdc"):
    stage=Usd.Stage.Open(str(path))
    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            v=attr.Get()
            if isinstance(v,Sdf.AssetPath) and v.path and v.path.startswith("../../../assets/objects/connector/"):
                target=ROOT/"assets"/v.path.split("assets/",1)[1]
                target.resolve().relative_to(ROOT)
                if not target.is_file():raise FileNotFoundError(target)
                relative=os.path.relpath(target,path.parent)
                attr.Set(Sdf.AssetPath(relative));changes.append({"stage":str(path.relative_to(ROOT)),"from":v.path,"to":relative})
    stage.GetRootLayer().Save()
print(json.dumps({"changes":changes},indent=2))
