"""Follow-up acceptance: UI retry and original LeRobot demo, no hardware calls."""
from pathlib import Path
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
def read(path):
 with urllib.request.urlopen(BASE+'/api'+path) as r:return json.load(r)
cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait("document.body.innerText.includes('本地服务已连接')")
open_run_card('5ae490795fcd4cef')
wait("[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='重试')")
before=read('/runs/5ae490795fcd4cef')
if next(t for t in before['tasks'] if t['stage']=='demo_import')['status']!='succeeded':
 click('重试')
 time.sleep(.7)
# Observe real progress; no synthetic state or completion expectation.
end=time.time()+50
while time.time()<end:
 run=read('/runs/5ae490795fcd4cef')
 if run['status'] not in ['running','queued']:break
 time.sleep(.5)
assert next(t for t in run['tasks'] if t['stage']=='demo_import')['status']=='succeeded',run
screenshot('08-retried-run')
click('返回工作台')
click('导入示教');wait("document.querySelector('[role=dialog]')!==null")
fill('input[placeholder="/home/…/datasets/my_demo"]',str(ROOT/'data/reference/connector/source_demo'))
fill('input[placeholder="例如：双臂连接器装配"]','浏览器验收 · Connector 原始LeRobot示教')
js("document.querySelector('.import-options summary').click()")
fill('#task-goal','分析双臂连接器抓取、协作对键和插入，最终原地松手')
js("(()=>{const el=document.querySelector('#asset-paths');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,"+json.dumps(str(ROOT/'assets/objects/connector'))+");el.dispatchEvent(new Event('input',{bubbles:true}));})()")
click('复制并创建任务');wait("document.querySelector('[role=dialog]')===null")
end=time.time()+55
while time.time()<end:
 runs=read('/runs')['runs'];actual=next((r for r in runs if r['name']=='浏览器验收 · Connector 原始LeRobot示教'),None)
 if actual:
  actual=read('/runs/'+actual['id'])
  if actual['status'] not in ['running','queued']:break
 time.sleep(.5)
assert actual
assert next(t for t in actual['tasks'] if t['stage']=='demo_import')['status']=='succeeded',actual
assert next(t for t in actual['tasks'] if t['stage']=='data_audit')['status']=='succeeded',actual
assert next(t for t in actual['tasks'] if t['stage']=='experience')['status']=='succeeded',actual
assert next(t for t in actual['tasks'] if t['stage']=='geometry')['status']=='succeeded',actual
assert next(t for t in actual['tasks'] if t['stage']=='task_analysis')['status']=='needs_configuration',actual
wait("document.querySelector('.run-detail .status-needs_configuration')!==null")
screenshot('09-original-demo-audited')
for filename,value in [('converted-retry.json',run),('original-demo-run.json',actual)]:
 (OUT/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2))
report={'status':'passed','real_api':True,'retry_run':run['id'],'original_demo_run':actual['id'],'stages':{t['stage']:t['status'] for t in actual['tasks']},'semantic_analysis_configured':False,'hardware_commands_sent':0,'scope':'UI+real-copy+audit+knowledge+geometry only; no simulation/training success inferred'}
(OUT/'real-demo-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
