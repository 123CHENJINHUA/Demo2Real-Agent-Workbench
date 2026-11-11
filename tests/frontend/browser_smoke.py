"""Real-browser UI acceptance using host Chrome CDP (no simulated API)."""
from pathlib import Path
import base64,json,time,urllib.request,websocket
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'runs'/'acceptance'/'frontend';OUT.mkdir(parents=True,exist_ok=True)
BASE='http://127.0.0.1:8787'
with urllib.request.urlopen('http://127.0.0.1:9338/json') as r:targets=json.load(r)
ws=websocket.create_connection(targets[0]['webSocketDebuggerUrl'],suppress_origin=True,timeout=30)
seq=0
errors=[]
def cdp(method,params=None):
 global seq
 seq+=1;sid=seq;ws.send(json.dumps({'id':sid,'method':method,'params':params or {}}))
 while True:
  msg=json.loads(ws.recv())
  if msg.get('method')=='Runtime.exceptionThrown':errors.append(msg['params'])
  if msg.get('id')==sid:
   if 'error' in msg:raise RuntimeError(msg['error'])
   return msg.get('result',{})
def js(source):
 result=cdp('Runtime.evaluate',{'expression':source,'returnByValue':True,'awaitPromise':True})
 if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
 return result.get('result',{}).get('value')
def wait(source,timeout=20):
 end=time.time()+timeout
 while time.time()<end:
  if js(source):return
  time.sleep(.15)
 raise AssertionError('Timed out: '+source)
def click(text):
 source="(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==="+json.dumps(text)+");if(!b)throw Error('Button not found');b.click();return true})()"
 js(source)
def fill(selector,value):
 js("(()=>{const el=document.querySelector("+json.dumps(selector)+");const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(el,"+json.dumps(value)+");el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));})()")
def screenshot(name):
 image=cdp('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})
 (OUT/(name+'.png')).write_bytes(base64.b64decode(image['data']))
cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
cdp('Page.navigate',{'url':BASE});wait("document.body.innerText.includes('从一次示教')");wait("document.body.innerText.includes('本地服务已连接')")
screenshot('01-workbench')
click('经验与失败记录');wait("document.body.innerText.includes('CONN-002')")
click('卡槽');wait("document.querySelectorAll('.knowledge-card').length>0");wait("document.body.innerText.includes('CONN-002')")
assert js("document.querySelectorAll('.knowledge-card').length")>0
screenshot('02-experience')
click('环境与模型配置');wait("document.querySelector('#isaac_sim_root')!==null")
wait("document.querySelector('#isaac_sim_root').value.length>0")
screenshot('03-environments')
click('策略与真机');wait("document.body.innerText.includes('无运动权限')")
wait("document.querySelectorAll('.policy-list>div').length>0")
assert not js("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('使能'))")
screenshot('04-policies-and-robot')
click('任务工作台');click('导入示教');wait("document.querySelector('[role=dialog]')!==null")
fill('input[placeholder="/home/…/datasets/my_demo"]',str(ROOT/'data/reference/connector/converted'))
fill('input[placeholder="例如：双臂连接器装配"]','浏览器验收 · 已录制Connector转换数据')
click('浏览');wait("document.querySelector('.folder-browser')!==null");click('使用当前文件夹')
screenshot('05-import')
click('复制并创建任务');wait("document.querySelector('[role=dialog]')===null");wait("document.querySelector('.run-detail')!==null")
wait("document.querySelectorAll('.stage-card').length>=1")
screenshot('06-real-run')
cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
screenshot('07-mobile')
assert js('document.documentElement.scrollWidth<=window.innerWidth'), 'Horizontal overflow on mobile'
cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
assert not errors,errors
report={'status':'passed','base_url':BASE,'real_api':True,'tests':['health connection','knowledge feature search CONN-002','environment paths displayed','actual policy catalog','no robot enable control','folder browser','create real converted-demo run','mobile overflow'],'screenshots':sorted(p.name for p in OUT.glob('*.png')),'browser_errors':errors}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
