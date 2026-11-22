"""Supported Codex process locations, isolated from global Codex state."""
from pathlib import Path
import os,subprocess
from modules.environment.service import local_environment,resolve

def executable(root):
    path=resolve(root,"codex_executable",False)
    if path is None or not path.is_file() or not os.access(path,os.X_OK):raise RuntimeError("请在environments.yaml配置codex_executable")
    return path

def environment(root):
    root=Path(root).resolve();env=local_environment(root)
    # Clear inherited inference credentials/endpoint overrides; use project Codex auth.
    for key in ("OPENAI_API_KEY","CODEX_API_KEY","OPENAI_BASE_URL","CODEX_PROFILE","CODEX_CONFIG","CODEX_THREAD_ID","CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
        env.pop(key,None)
    home=root/".runtime/codex-home";home.mkdir(parents=True,exist_ok=True);home.chmod(0o700)
    state=root/".runtime/codex-sqlite";state.mkdir(parents=True,exist_ok=True)
    env["CODEX_HOME"]=str(home);env["CODEX_SQLITE_HOME"]=str(state)
    # HOME remains the actual OS home. No global auth/config is copied or modified.
    return env

def auth_status(root, *, process_env=None):
    root=Path(root).resolve();result={"available":False,"authenticated":False,"configured":False,"status":"unavailable","auth_home":str(root/".runtime/codex-home"),"model":"","version":"","detail":"尚未找到登记的Codex CLI"}
    try:
        path=executable(root);env=process_env if process_env is not None else environment(root)
        result.update(available=True,executable=str(path),auth_home=env['CODEX_HOME'])
        v=subprocess.run([str(path),"--version"],env=env,cwd=root,capture_output=True,text=True,timeout=10)
        result["version"]=next((line for line in v.stdout.splitlines() if "codex" in line.casefold()),"")
        p=subprocess.run([str(path),"--no-daemon","-c",'cli_auth_credentials_store="file"',"login","status"],env=env,cwd=root,capture_output=True,text=True,timeout=10)
        authenticated=p.returncode==0 and "logged in" in (p.stdout+p.stderr).casefold()
        result.update(authenticated=authenticated,configured=authenticated,status="authenticated" if authenticated else "needs_login",detail="项目内Codex登录可用；模型访问仍需真实推理核验" if authenticated else "项目内尚未登录；不会复制或修改全局Codex登录")
    except (RuntimeError,OSError,subprocess.SubprocessError) as exc:
        result.update(status="unavailable",detail=type(exc).__name__+": "+str(exc))
    return result
