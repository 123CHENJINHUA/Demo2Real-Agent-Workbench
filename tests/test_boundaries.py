from pathlib import Path
import ast
import pytest
from modules.environment.service import save,load,resolve,local_environment
from modules.simulation.isaac.connector import command
ROOT=Path(__file__).resolve().parents[1]

def imported(tree):
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            for name in node.names:yield name.name
        elif isinstance(node,ast.ImportFrom):yield node.module or ""

def test_pure_contracts_and_policies_have_no_device_imports():
    forbidden=("torch","isaac","pyAgxArm","fastapi","modules.robot_backends","modules.simulation")
    for p in list((ROOT/"contracts").glob("*.py"))+list((ROOT/"modules/policies").rglob("*.py")):
        assert not any(name.startswith(forbidden) for name in imported(ast.parse(p.read_text()))),str(p)

def test_runtime_legacy_no_old_business_paths():
    for p in (ROOT/"tasks/connector/legacy").rglob("*.py"):
        assert "/home/cjh/Downloads/TP-KMPs-nero-isaacsim" not in p.read_text()
        assert "'/home/cjh" not in p.read_text()

def test_offline_runtime_does_not_import_sdk():
    for base in ("backend","orchestration","contracts","modules","application","workers"):
        for p in (ROOT/base).rglob("*.py"):
            assert not any(x.startswith("pyAgxArm") for x in imported(ast.parse(p.read_text()))),str(p)

def test_local_environment_and_relative_configuration(tmp_path):
    (tmp_path/"config").mkdir();(tmp_path/"config/environments.yaml").write_text("schema_version: 1\nenvironments: {}\n")
    save(tmp_path,{"app_python":"${PROJECT_ROOT}/bin/python"})
    assert resolve(tmp_path,"app_python")==tmp_path/"bin/python"
    env=local_environment(tmp_path)
    assert Path(env["TMPDIR"]).is_relative_to(tmp_path)
    assert env["PYTHONPATH"]==str(tmp_path)
    with pytest.raises(ValueError):save(tmp_path,{"app_python":"relative/path"})
    with pytest.raises(ValueError):save(tmp_path,{"arbitrary_command":"evil"})

def test_sim_only_builds_known_bounded_commands():
    args=command(ROOT,ROOT/"runs/test-command","keyed_bench")
    assert "--keyed-bench" in args
    with pytest.raises(ValueError):command(ROOT,ROOT/"runs/test-command","untrusted-script")
    with pytest.raises(ValueError):command(ROOT,ROOT/"runs/test-command","physics",spread=.2)


def test_diagnostic_api_checks_bounded_perturbation_parameters(tmp_path):
    import shutil
    from fastapi.testclient import TestClient
    from backend.api import create_app
    for name in ("config","knowledge"):
        shutil.copytree(ROOT/name,tmp_path/name)
    with TestClient(create_app(tmp_path,start_scheduler=False)) as client:
        token=client.get("/api/session").json()["token"];headers={"X-Session-Token":token}
        bad=client.post("/api/diagnostics",json={"kind":"connector_physics","spread_m":.2},headers=headers)
        assert bad.status_code==400
        good=client.post("/api/diagnostics",json={"kind":"connector_physics","seed":712,"spread_m":.025,"yaw_spread_deg":8,"pose_noise_m":.0003,"pose_delay_s":.066,"disturbance":"drop"},headers=headers)
        assert good.status_code==200
        value=good.json()["input"]
        assert value["spread"]==.025 and value["pose_delay"]==.066 and value["disturbance"]=="drop"


def test_completed_action_still_archives_failed_retention(tmp_path,monkeypatch):
    import shutil,sys,types
    from application.registry import REGISTRY,execute_stage
    from modules.experience import list_incidents
    shutil.copytree(ROOT/"knowledge",tmp_path/"knowledge")
    out=tmp_path/"runs/partial/simulation";out.mkdir(parents=True)
    (out/"worker.log").write_text("action complete; retention false")
    evidence=out/"partial.json";evidence.write_text('{"action_passed":true,"retained":false}')
    issue={"failure_category":"physics_contact","failure_stage":"simulation_test","error":"retention false","evidence_paths":[str(evidence.relative_to(tmp_path))]}
    module=types.ModuleType("partial_validation_fixture");module.run=lambda ctx:{"status":"succeeded","issues":[issue]}
    monkeypatch.setitem(sys.modules,"partial_validation_fixture",module)
    monkeypatch.setitem(REGISTRY,"partial_validation_fixture",("partial_validation_fixture","run"))
    result=execute_stage("partial_validation_fixture",{"root":tmp_path,"run_id":"partial","output_dir":out,"input":{},"config":{},"check_cancel":lambda:None,"emit":lambda *args:None})
    assert result["status"]=="succeeded"
    incident=next(v for v in list_incidents(tmp_path) if v["incident_id"]==result["issue_records"][0]["incident_id"])
    assert incident["primary_category"]=="physics_contact"
    assert incident["hardware_verified"] is False
    assert incident["diagnosis"]["state"]=="unknown"
