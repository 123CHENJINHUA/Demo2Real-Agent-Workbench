from pathlib import Path
import os,time,json
from modules.model_accounts.codex_runtime import environment,auth_status
from modules.model_accounts.codex_login import CodexLoginManager

def fixture_root(tmp_path):
    (tmp_path/"config").mkdir()
    executable=tmp_path/"fake-codex"
    executable.write_text("#!/usr/bin/python3\nimport os,sys,time\nfrom pathlib import Path\nargs=sys.argv[1:]\nif '--version' in args: print('codex-cli test');sys.exit(0)\nif args[-2:]==['login','status']:\n    if (Path(os.environ['CODEX_HOME'])/'TEST_AUTHORIZED').exists():print('Logged in using ChatGPT');sys.exit(0)\n    print('Not logged in');sys.exit(1)\nif '--device-auth' in args:\n    print('https://auth.openai.com/codex/device',flush=True)\n    print('ABCD-EFGHI',flush=True)\n    while not (Path(os.environ['CODEX_HOME'])/'TEST_AUTHORIZED').exists():time.sleep(.03)\n    sys.exit(0)\nsys.exit(2)\n")
    executable.chmod(0o755)
    (tmp_path/"config/environments.yaml").write_text("environments:\n  codex_executable: "+str(executable)+"\n")
    return tmp_path

def test_codex_runtime_does_not_change_real_home_or_inherit_api_keys(tmp_path,monkeypatch):
    root=fixture_root(tmp_path);monkeypatch.setenv("OPENAI_API_KEY","test-do-not-forward");before=os.environ.get("HOME")
    env=environment(root)
    assert env.get("HOME")==before
    assert env["CODEX_HOME"]==str(root/".runtime/codex-home")
    assert "OPENAI_API_KEY" not in env
    assert not auth_status(root)["authenticated"]

def test_real_login_process_parses_five_character_code_and_cancels(tmp_path):
    root=fixture_root(tmp_path);manager=CodexLoginManager(root)
    try:
        manager.start();result={}
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=manager.status()
            if result.get("user_code"):break
            time.sleep(.04)
        assert result["user_code"]=="ABCD-EFGHI"
        assert result["verification_url"]=="https://auth.openai.com/codex/device"
        assert "CODEX_HOME=" in result["login_command"]
        assert manager.cancel()["status"]=="cancelled"
        assert manager.process.poll() is not None
        assert not auth_status(root)["authenticated"]
    finally:manager.close()

def test_project_login_becomes_authenticated_without_global_credentials(tmp_path):
    root=fixture_root(tmp_path);manager=CodexLoginManager(root)
    try:
        manager.start();(root/".runtime/codex-home/TEST_AUTHORIZED").write_text("fixture only")
        manager.process.wait(timeout=3)
        assert manager.status()["authenticated"]
    finally:manager.close()
