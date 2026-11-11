"""Read-only real-browser acceptance of the isolated local Codex provider."""
from pathlib import Path
from urllib.parse import urlparse
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
requests=[]
def cdp(method,params=None):
 global seq
 seq+=1;sid=seq;ws.send(json.dumps({'id':sid,'method':method,'params':params or {}}))
 while True:
  msg=json.loads(ws.recv())
  if msg.get('method')=='Runtime.exceptionThrown':errors.append(msg['params'])
  if msg.get('method')=='Network.requestWillBeSent':
   request=msg['params']['request'];parsed=urlparse(request['url'])
   if parsed.hostname in ('127.0.0.1','localhost') and parsed.path.startswith('/api/'):
    requests.append({'method':request['method'],'path':parsed.path})
  if msg.get('id')==sid:
   if 'error' in msg:raise RuntimeError(msg['error'])
   return msg.get('result',{})
def read(path):
 with urllib.request.urlopen(BASE+'/api'+path,timeout=30) as response:return json.load(response)
provider=read('/settings/provider');account=read('/settings/provider/codex/status')
assert provider['kind']=='codex_cli'
assert account['available'] is True
assert account['authenticated'] is True
assert provider['configured'] is True
assert account['auth_home']==str(ROOT/'.runtime/codex-home')
assert account.get('version')
cdp('Page.enable');cdp('Runtime.enable');cdp('Network.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait("document.body.innerText.includes('本地服务已连接')")
click('环境与模型配置');wait("document.querySelector('#provider-kind')?.value==='codex_cli'",30)
wait("document.querySelector('.codex-auth-badge.ready')!==null",30)
assert js("document.body.innerText.includes('项目内已登录')")
assert js("document.querySelector('#api-key')===null")
assert js("document.querySelector('#base-url')===null")
assert js("document.querySelector('#model').value")==provider.get('model','')
if not provider.get('model'):
 assert js("document.querySelector('#model').placeholder.includes('CLI 默认')")
assert js("document.querySelector('.codex-connection-card').innerText.includes("+json.dumps(account['version'])+")")
assert js("document.querySelector('.codex-connection-card').innerText.includes("+json.dumps(account['executable'])+")")
assert js("document.querySelector('.codex-connection-card').innerText.includes("+json.dumps(account['auth_home'])+")")
# A user-requested refresh is read-only. Never press login, model test or save.
js("document.querySelector('[aria-label=刷新项目Codex登录状态]').click()")
wait("document.querySelector('.codex-auth-badge.ready')!==null",30)
wait("document.querySelector('.codex-login-command')!==null",30)
js("document.querySelector('.codex-login-command').open=true")
assert js("document.querySelector('.codex-login-command pre').innerText.includes('CODEX_HOME')")
# Never archive a one-time login code in screenshots even if the login state changes.
js("[...document.querySelectorAll('.device-code')].forEach(e=>e.style.filter='blur(12px)')")
js('window.scrollTo(0,0)');time.sleep(.2);screenshot('18-codex-project-authenticated')
cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});screenshot('19-mobile-codex-settings')
assert js('document.documentElement.scrollWidth<=window.innerWidth')
cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
# Drain protocol events and verify this browser only read API state.
js('document.title');assert requests
assert all(request['method']=='GET' for request in requests),requests
assert not any(request['path'].endswith('/test') or request['path'].endswith('/login/start') for request in requests)
robot=read('/robots/status');assert robot['connected'] is False and robot['hardware_commands_sent']==0
report={'status':'passed','provider_kind':provider['kind'],'project_authenticated':account['authenticated'],'project_auth_home':account['auth_home'],'cli_available':account['available'],'cli_version':account['version'],'cli_executable':account['executable'],'configured_model':provider.get('model',''),'empty_model_uses_cli_default':not bool(provider.get('model')),'http_address_and_api_key_absent_from_codex_form':True,'login_command_from_backend':True,'read_only_status_refresh':True,'browser_api_methods':sorted(set(request['method'] for request in requests)),'browser_model_requests':0,'browser_login_starts':0,'provider_configuration_changes':0,'mobile_no_horizontal_overflow':True,'hardware_commands_sent':robot['hardware_commands_sent'],'browser_errors':errors}
assert not errors
(OUT/'codex-live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
