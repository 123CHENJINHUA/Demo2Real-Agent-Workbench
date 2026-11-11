"""Official project-local device login; no credential copying or token parsing."""
from pathlib import Path
import os,re,signal,subprocess,time,threading,uuid,shlex
from urllib.parse import urlparse
from .codex_runtime import environment,executable,auth_status

class CodexLoginManager:
    def __init__(self,root):
        self.root=Path(root).resolve();self.process=None;self.log=None;self.path=None;self.started=None;self.finished=None;self.cancelled=False;self.lock=threading.RLock()
    def login_command(self):
        env=environment(self.root)
        keys=("CODEX_HOME","CODEX_SQLITE_HOME","TMPDIR","XDG_CACHE_HOME","XDG_CONFIG_HOME","XDG_DATA_HOME")
        return shlex.join(["env",*[key+"="+env[key] for key in keys],str(executable(self.root)),"--no-daemon","-c",'cli_auth_credentials_store="file"',"login","--device-auth"])
    def start(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:return self.status()
            account=auth_status(self.root)
            if account['authenticated']:return {'status':'authenticated','authenticated':True,'detail':'项目内Codex已经登录'}
            if not account['available']:return {'status':'unavailable','authenticated':False,'detail':account['detail']}
            directory=self.root/'.runtime/codex-auth';directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
            self.path=directory/(uuid.uuid4().hex+'.log')
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);self.log=os.fdopen(fd,'wb',buffering=0)
            self.started=time.time();self.finished=None;self.cancelled=False
            args=[str(executable(self.root)),'--no-daemon','-c','cli_auth_credentials_store="file"','login','--device-auth']
            self.process=subprocess.Popen(args,cwd=self.root,env=environment(self.root),stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True,stdin=subprocess.DEVNULL)
            return self.status()
    def _finish_stream(self):
        if self.log is not None and not self.log.closed:self.log.close()
    def status(self):
        with self.lock:
            if self.process is None:
                account=auth_status(self.root)
                return {'status':'authenticated' if account['authenticated'] else 'idle','authenticated':account['authenticated'],'detail':account['detail'],'login_command':self.login_command() if account['available'] else None}
            running=self.process.poll() is None
            if running and time.time()-self.started>600:
                self.cancel();return {'status':'expired','authenticated':False,'detail':'登录窗口已超过10分钟，请重新发起'}
            raw=self.path.read_text(errors='replace')[-16000:] if self.path and self.path.exists() else ''
            raw=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',raw)
            if not running:
                self._finish_stream();self.finished=self.finished or time.time()
                success=self.process.returncode==0 and auth_status(self.root)['authenticated']
                credential=self.root/'.runtime/codex-home/auth.json'
                if success and credential.is_file():credential.chmod(0o600)
                return {'status':'authenticated' if success else 'cancelled' if self.cancelled else 'failed','authenticated':success,'detail':'项目内Codex登录成功' if success else '登录已取消' if self.cancelled else 'Codex官方登录尚未成功，请检查网络或重新发起。原始日志仅保存在项目后端。','exit_code':self.process.returncode}
            urls=re.findall(r'https://[^\s<>\x1b]+',raw)
            urls=[u.rstrip(').,;') for u in urls if urlparse(u.rstrip(').,;')).hostname in ('auth.openai.com','chatgpt.com')]
            # Only the one-time CLI device code is exposed; auth.json never is read.
            codes=re.findall(r'(?<![A-Z0-9])[A-Z0-9]{4}-[A-Z0-9]{4,8}(?![A-Z0-9])',raw)
            return {'status':'pending','authenticated':False,'verification_url':urls[0] if urls else None,'login_command':self.login_command(),'user_code':codes[0] if codes else None,'expires_at':self.started+600,'detail':'在官方页面登录并输入一次性代码；登录状态仅写入本项目' if urls and codes else '正在向Codex官方服务申请设备登录，请稍候'}
    def cancel(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=3)
            self.cancelled=True;self._finish_stream()
            return {'status':'cancelled','authenticated':False,'detail':'仅取消本项目尚未完成的登录流程；没有调用全局logout'}
    def close(self):
        if self.process is not None and self.process.poll() is None:self.cancel()
