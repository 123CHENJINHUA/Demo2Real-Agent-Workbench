from pathlib import Path
import json,sys
from pxr import Usd,Sdf
ROOT=Path(__file__).resolve().parents[1]
items=[];outside=[];missing=[]
for path in (ROOT/"assets/connector_visuals").glob("*.usdc"):
    stage=Usd.Stage.Open(str(path))
    for layer in stage.GetUsedLayers():
        p=Path(layer.realPath).resolve()
        if not p.is_relative_to(ROOT):outside.append(str(p))
    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            v=attr.Get()
            if isinstance(v,Sdf.AssetPath) and v.path:
                resolved=Path(v.resolvedPath) if v.resolvedPath else path.parent/v.path
                resolved=resolved.resolve();items.append({"stage":str(path.relative_to(ROOT)),"asset":v.path,"resolved":str(resolved)})
                if not resolved.is_relative_to(ROOT):outside.append(str(resolved))
                if not resolved.exists():missing.append(str(resolved))
report={"status":"passed" if not outside and not missing else "failed","dependencies":items,"outside":outside,"missing":missing}
output=ROOT/"runs/acceptance/isolation";output.mkdir(parents=True,exist_ok=True);(output/"usd_asset_audit.json").write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));sys.exit(bool(outside or missing))
