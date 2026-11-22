"""Official project-local device login and account replacement."""
from pathlib import Path
import os,re,signal,subprocess,time,threading,uuid,shlex,tempfile
from urllib.parse import urlparse
from .codex_runtime import environment,executable,auth_status

class CodexLoginManager:
    def __init__(self,root):
        self.root=Path(root).resolve();self.process=None;self.log=None;self.path=None;self.started=None;self.finished=None;self.cancelled=False;self.lock=threading.RLock()
        self.staging=None
        self.pending_env=None
        self.account_switch=False
        self.completed_status=None
    def login_command(self):
        env=environment(self.root)
        keys=("CODEX_HOME","CODEX_SQLITE_HOME","TMPDIR","XDG_CACHE_HOME","XDG_CONFIG_HOME","XDG_DATA_HOME")
        return shlex.join(["env",*[key+"="+env[key] for key in keys],str(executable(self.root)),"--no-daemon","-c",'cli_auth_credentials_store="file"',"login","--device-auth"])
    def start(self, *, switch_account=False):
        with self.lock:
            if self.process is not None and not self.completed_status:
                state=self.status()
                if state['status']=='pending':return state
            account=auth_status(self.root)
            if account['authenticated'] and not switch_account:return {'status':'authenticated','authenticated':True,'detail':'项目内Codex已经登录'}
            if not account['available']:return {'status':'unavailable','authenticated':False,'detail':account['detail']}
            directory=self.root/'.runtime/codex-auth';directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
            self.path=directory/(uuid.uuid4().hex+'.log')
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);self.log=os.fdopen(fd,'wb',buffering=0)
            self.started=time.time();self.finished=None;self.cancelled=False;self.completed_status=None
            self.account_switch=switch_account
            args=[str(executable(self.root)),'--no-daemon','-c','cli_auth_credentials_store="file"','login','--device-auth']
            self.pending_env=environment(self.root)
            try:
                if switch_account:
                    self.staging=tempfile.TemporaryDirectory(prefix='switch-',dir=directory)
                    pending=Path(self.staging.name)
                    (pending/'sqlite').mkdir(mode=0o700)
                    self.pending_env.update(CODEX_HOME=str(pending),CODEX_SQLITE_HOME=str(pending/'sqlite'))
                self.process=subprocess.Popen(args,cwd=self.root,env=self.pending_env,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True,stdin=subprocess.DEVNULL)
            except OSError:
                self._finish_stream()
                self._cleanup_staging()
                self.process=None
                raise
            return self.status()
    def _finish_stream(self):
        if self.log is not None and not self.log.closed:self.log.close()
    def _cleanup_staging(self):
        if self.staging is not None:
            self.staging.cleanup()
            self.staging=None
        self.pending_env=None
    def _finish_login(self):
        self._finish_stream();self.finished=self.finished or time.time()
        success=not self.cancelled and self.process.returncode==0 and auth_status(self.root,process_env=self.pending_env)['authenticated']
        try:
            if success:
                candidate=Path(self.pending_env['CODEX_HOME'])/'auth.json'
                if self.account_switch:
                    if not candidate.is_file() or candidate.is_symlink():
                        raise OSError('No project credential from the device login')
                    candidate.chmod(0o600)
                    candidate.replace(self.root/'.runtime/codex-home/auth.json')
                elif candidate.is_file():
                    candidate.chmod(0o600)
        except OSError:
            success=False
        finally:
            self._cleanup_staging()
        authenticated=success or (self.account_switch and auth_status(self.root)['authenticated'])
        outcome='authenticated' if success else 'cancelled' if self.cancelled else 'failed'
        if success:
            detail='项目内Codex账号已切换' if self.account_switch else '项目内Codex登录成功'
        elif self.cancelled:
            detail='登录已取消'
        else:
            detail='Codex官方登录尚未成功，请检查网络或重新发起。'
        if not success and self.account_switch and authenticated:
            detail+='项目仍使用原账号。'
        self.completed_status={'status':outcome,'authenticated':authenticated,'detail':detail,'exit_code':self.process.returncode}
        return self.completed_status
    def status(self):
        with self.lock:
            if self.completed_status is not None:
                return self.completed_status
            if self.process is None:
                account=auth_status(self.root)
                return {'status':'authenticated' if account['authenticated'] else 'idle','authenticated':account['authenticated'],'detail':account['detail'],'login_command':self.login_command() if account['available'] else None}
            running=self.process.poll() is None
            if running and time.time()-self.started>600:
                result=self.cancel() | {'status':'expired','detail':'登录窗口已超过10分钟，请重新发起'}
                self.completed_status=result
                return result
            raw=self.path.read_text(errors='replace')[-16000:] if self.path and self.path.exists() else ''
            raw=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',raw)
            if not running:
                return self._finish_login()
            urls=re.findall(r'https://[^\s<>\x1b]+',raw)
            urls=[u.rstrip(').,;') for u in urls if urlparse(u.rstrip(').,;')).hostname in ('auth.openai.com','chatgpt.com')]
            # Only the one-time CLI device code is exposed; auth.json never is read.
            codes=re.findall(r'(?<![A-Z0-9])[A-Z0-9]{4}-[A-Z0-9]{4,8}(?![A-Z0-9])',raw)
            detail='在官方页面用目标 ChatGPT 账号登录并输入一次性代码' if urls and codes else '正在向Codex官方服务申请设备登录，请稍候'
            return {'status':'pending','authenticated':False,'verification_url':urls[0] if urls else None,
                    'login_command':self.login_command() if not self.account_switch else None,
                    'user_code':codes[0] if codes else None,'expires_at':self.started+600,'detail':detail}
    def cancel(self):
        with self.lock:
            if self.completed_status is not None:return self.completed_status
            if self.process is not None and self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=3)
            self.cancelled=True;self._finish_stream()
            if self.process is not None:return self._finish_login()
            return {'status':'cancelled','authenticated':auth_status(self.root)['authenticated'],'detail':'本次项目登录已取消'}
    def close(self):
        if self.process is not None and self.completed_status is None:self.cancel()
