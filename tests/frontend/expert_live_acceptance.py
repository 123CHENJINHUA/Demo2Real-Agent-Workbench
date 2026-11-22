"""GET-only actual offline expert UI acceptance. Never clicks the launch button.
Usage: project-python tests/frontend/expert_live_acceptance.py EXPERT_RUN_ID
"""
from pathlib import Path
from urllib.parse import urlparse
import sys,re
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
RUN=sys.argv[1];assert re.fullmatch(r'[a-f0-9]+',RUN)
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
def has(text):return 'document.body.innerText.includes('+json.dumps(text)+')'
detail=read('/runs/'+RUN);run=detail.get('run',detail);tasks=detail['tasks'];artifacts=detail['artifacts']
assert run['input']['expert_run'] is True
assert [t['stage'] for t in tasks]==['experience','scene_validation','expert_plan','expert_execution','expert_validation']
execution=next(t for t in tasks if t['stage']=='expert_execution');validation=next(t for t in tasks if t['stage']=='expert_validation');result={**(execution.get('result') or {}),**(validation.get('result') or {})}
scene=run['input']['scene_run_id']
cdp('Page.enable');cdp('Runtime.enable');cdp('Network.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait(has('本地服务已连接'))
open_run_card(scene);wait("document.querySelector('.expert-launch')!==null")
assert js(has('Ground truth 专家')) and js(has('脚本专家使用仿真真值与录制轨迹'))
wait("[...document.querySelectorAll('.expert-launch button')].some(b=>b.innerText.includes('运行离线示教专家')&&!b.disabled)")
screenshot('expert-'+RUN+'-launch-readonly')
open_run_card(RUN);wait("document.querySelectorAll('.expert-criterion').length===6")
assert js('document.querySelectorAll(".stage-card").length')==len(tasks)
wait('document.querySelector(".expert-source-links").innerText.includes('+json.dumps(run['input']['spec_hash'])+')')
assert js("document.querySelector('.expert-launch')===null")
wait(has('专家执行报告'))
verified_reports=[]
for title,path in [('专家执行报告',(execution.get('result') or {}).get('report_path')),('独立验收报告',(validation.get('result') or {}).get('report_path'))]:
 if path:
  artifact=next(a for a in artifacts if a['path']==path)
  selector='.expert-run-panel a[href="/api/artifacts/'+artifact['id']+'"]'
  assert js('document.querySelector('+json.dumps(selector)+')!==null')
  assert js("(async()=>{const r=await fetch('/api/artifacts/"+artifact['id']+"');return r.ok&&(await r.text()).length>0})()")
  verified_reports.append(path)
stage_results=(validation.get('result') or {}).get('stage_results',{})
expected_retention=stage_results.get('retention',{}).get('passed',result.get('retention_passed'))
if expected_retention is False:
 assert js(has('松手后保持未通过；动作完成不能作为完整任务成功。'))
 assert js("[...document.querySelectorAll('.expert-criterion')].find(e=>e.innerText.includes('完整任务 overall')).classList.contains('failed')")
if result.get('action_passed') is True:
 assert js("[...document.querySelectorAll('.expert-criterion')].find(e=>e.innerText.includes('动作完成 action')).classList.contains('passed')")
js('window.scrollTo(0,0)');screenshot('expert-'+RUN+'-actual-result')
cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});screenshot('expert-'+RUN+'-mobile')
assert js('document.documentElement.scrollWidth<=window.innerWidth')
cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
js("[...document.querySelectorAll('.expert-source-links button')].find(b=>b.innerText.includes('查看来源场景')).click()")
wait('document.querySelector(".run-detail")?.dataset.runId==='+json.dumps(scene))
assert read('/health')['hardware_commands_enabled'] is False
js('document.title');assert requests and all(r['method']=='GET' for r in requests),requests
assert not errors,errors
report={'status':'passed','run_id':RUN,'scene_run_id':scene,'workflow_status':run['status'],'execution_status':execution['status'],'independent_validation_status':validation['status'],'action_passed':result.get('action_passed'),'retention_passed':result.get('retention_passed'),'full_task_passed':result.get('full_task_passed'),'ground_truth_only_label_visible':True,'explicit_launch_available_but_never_clicked':True,'actual_stage_count':len(tasks),'actual_stages_visible':True,'verified_report_paths':verified_reports,'available_reports_get_verified':True,'actual_scene_binding_and_navigation_verified':True,'retention_stage_passed':expected_retention,'retention_failure_not_hidden':expected_retention is False,'mobile_no_horizontal_overflow':True,'browser_api_methods':sorted(set(r['method'] for r in requests)),'browser_errors':errors,'browser_experts_launched':0,'gpu_jobs_started':0,'hardware_commands_sent':0,'evidence_scope':'GET-only expert UI verification; actual expert outcomes retained without RGBD/learning/hardware claims'}
(OUT/('expert-'+RUN+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
