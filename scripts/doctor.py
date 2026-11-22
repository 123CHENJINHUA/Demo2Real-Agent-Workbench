"""Project/environment self-check, without importing simulation or hardware."""
from pathlib import Path
import sys,json,ast,subprocess,os,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modules.environment.service import validate,local_environment,resolve

def run():
    report={"project_root":str(ROOT),"environment":validate(ROOT),"business_symlinks":[],"syntax_errors":[],"old_business_paths":[],"hardware_commands_sent":0}
    folders=("backend","orchestration","application","workers","contracts","modules","scripts","tasks")
    for folder in folders:
        for p in (ROOT/folder).rglob("*.py"):
            if p.is_symlink():report["business_symlinks"].append(str(p.relative_to(ROOT)))
            try:ast.parse(p.read_text())
            except SyntaxError as exc:report["syntax_errors"].append({"path":str(p.relative_to(ROOT)),"error":str(exc)})
            if str(ROOT.parent/"TP-KMPs-nero-isaacsim") in p.read_text():report["old_business_paths"].append(str(p.relative_to(ROOT)))
    for folder in ("assets","vendor","data/reference"):
        if (ROOT/folder).exists():
            report["business_symlinks"].extend(str(p.relative_to(ROOT)) for p in (ROOT/folder).rglob("*") if p.is_symlink())
    report["status"]="passed" if not any(report[k] for k in ("business_symlinks","syntax_errors","old_business_paths")) and report["environment"]["ok"] else "failed"
    report["missing_capabilities"]={"multimodal_model":"configure provider in frontend; not inherited from chat session","generic_scene_synthesis":"pending task/asset/frame/evaluation plugins beyond Connector"}
    out=ROOT/"runs/acceptance/isolation";out.mkdir(parents=True,exist_ok=True);(out/"doctor.json").write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2));return report
if __name__=="__main__":sys.exit(0 if run()["status"]=="passed" else 1)
