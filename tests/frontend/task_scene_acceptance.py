"""Read-only browser acceptance of a real TaskSpec-bound Isaac scene build.

Usage: project-python tests/frontend/task_scene_acceptance.py SCENE_RUN_ID
Requires dedicated project-owned Chrome CDP on 9338; never clicks mutation controls.
"""
from pathlib import Path
from urllib.parse import urlparse
import re,sys
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
RUN=sys.argv[1]
assert re.fullmatch(r'[a-f0-9]+',RUN), 'Expected actual scene run id'
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
def open_run(run_id):
 wait("[...document.querySelectorAll('.task-row')].some(b=>b.textContent.includes("+json.dumps(run_id)+"))")
 js("[...document.querySelectorAll('.task-row')].find(b=>b.textContent.includes("+json.dumps(run_id)+")).click()")
 wait("document.querySelector('.run-id')?.innerText.includes("+json.dumps(run_id)+")")
def tab(text):
 js("[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()==="+json.dumps(text)+").click()")
detail=read('/runs/'+RUN);run=detail.get('run',detail);tasks=detail.get('tasks',[]);artifacts=detail['artifacts']
build=next(t for t in tasks if t['stage']=='scene_build')
assert build['status']=='succeeded',build
validation=next((t for t in tasks if t['stage']=='scene_validation'),None)
result=build['result'];source=run['input']['source_run_id'];envelope=read('/runs/'+source+'/task-spec');spec=envelope['spec']
assert result.get('task_spec_hash') and result.get('spec_revision')
prefix='runs/'+RUN+'/scene_build/'
scenes=[a for a in artifacts if re.fullmatch(re.escape(prefix)+r'build_\d+/scene\.png',a.get('path',''))]
assert scenes
scene=max(scenes,key=lambda a:int(a['path'].split('/')[-2].split('_')[-1]));directory=scene['path'].rsplit('/',1)[0]+'/'
expected=[scene]+[a for a in artifacts if a.get('path') in [directory+'camera_0_rgb.png',directory+'camera_1_rgb.png']]
assert all(any(a.get('path')==directory+name for a in artifacts) for name in ['scene.usda','scene_manifest.json','task_spec.json','build_report.json'])
synthetic=spec.get('source',{}).get('synthetic_test_fixture') is True
failed_history=[entry for entry in envelope.get('scene_builds',[]) if entry.get('status')=='failed' and entry['run_id']!=RUN]
expected_stale=result['task_spec_hash']!=spec['content_hash'] or result['spec_revision']!=spec['revision']
cdp('Page.enable');cdp('Runtime.enable');cdp('Network.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait(has('本地服务已连接'))
open_run(RUN)
wait("document.querySelector('.run-pane:not([hidden]) .scene-preview-panel')!==null")
js("document.querySelector('.run-pane:not([hidden]) .scene-preview-panel').scrollIntoView({block:'center'})")
for artifact in expected:
 selector='.run-pane:not([hidden]) .scene-preview-panel img[src="/api/artifacts/'+artifact['id']+'"]'
 wait('document.querySelector('+json.dumps(selector)+')?.complete&&document.querySelector('+json.dumps(selector)+')?.naturalWidth>0')
assert js("document.querySelector('.run-pane:not([hidden]) .scene-spec-binding').innerText.includes("+json.dumps(result['task_spec_hash'])+")")
assert js("document.querySelector('.run-previews')===null"),'Demo contact sheet is not a built scene'
if validation:
 assert js("document.querySelector('.run-pane:not([hidden]) .scene-validation-summary')!==null")
 if validation['status']=='failed':
  assert js("document.querySelector('.run-pane:not([hidden]) .scene-validation-summary').classList.contains('failed')")
  assert js(has('场景局部检查：检查失败'))
  assert js(has('几何/物理检查与完整动作、松手后保持和真机验证分别验收。'))
js('window.scrollTo(0,0)');screenshot('task-scene-'+RUN+'-render')
tab('任务定义 / 场景');wait("document.querySelector('.task-spec-version-strip')!==null")
wait('document.querySelector(".task-profile-selection select").value==='+json.dumps(spec.get('profile_id','')))
assert js('document.querySelector(".spec-hash").innerText.includes('+json.dumps(spec['content_hash'])+')')
assert js("document.querySelector('.task-source-context').classList.contains('stale')")==expected_stale
assert js(has('动作完成 action')) and js(has('松手后保持 retention'))
assert js(has('冻结与预览不等于物理或真机验证'))
if synthetic:
 assert js(has('程序生成场景夹具')) and js(has('没有第二段录制示教或抓放策略成功证据。'))
for filename in ['scene.usda','scene_manifest.json','task_spec.json','build_report.json']:
 artifact=next(a for a in artifacts if a.get('path')==directory+filename)
 selector='.run-pane:not([hidden]) .scene-evidence-links a[href="/api/artifacts/'+artifact['id']+'"]'
 assert js('document.querySelector('+json.dumps(selector)+')!==null')
 assert js("(async()=>{const r=await fetch('/api/artifacts/"+artifact['id']+"');return r.ok&&(await r.text()).length>0})()")
assert js('document.querySelectorAll(".spec-object-table tbody tr").length')==len(spec.get('objects',[]))
js('window.scrollTo(0,0)');screenshot('task-scene-'+RUN+'-contract')
tab('JSON 草案编辑');wait("document.querySelector('textarea[aria-label=任务定义JSON草案]')!==null")
assert json.loads(js("document.querySelector('textarea[aria-label=任务定义JSON草案]').value"))==spec
assert js("[...document.querySelectorAll('.task-spec-actions button')].find(b=>b.innerText.includes('保存新 revision')).disabled")
tab('版本与场景记录');wait("document.querySelector('.spec-build-history')!==null")
assert js('document.querySelector(".spec-build-history").innerText.includes('+json.dumps(RUN)+')')
for failed in failed_history:
 assert js('document.querySelector(".spec-build-history").innerText.includes('+json.dumps(failed['run_id'])+')')
js('window.scrollTo(0,0)');screenshot('task-scene-'+RUN+'-history')
tab('结构概览')
cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
screenshot('task-scene-'+RUN+'-mobile')
assert js('document.documentElement.scrollWidth<=window.innerWidth'),'Horizontal overflow on mobile'
cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
js("document.querySelector('.task-source-context button').click()")
wait('document.querySelector(".run-id")?.innerText.includes('+json.dumps(source)+')')
robot=read('/robots/status');assert not robot['connected'] and robot['hardware_commands_sent']==0
js('document.title');assert requests and all(r['method']=='GET' for r in requests),requests
assert not errors,errors
report={'status':'passed','run_id':RUN,'source_run_id':source,'workflow_status':run['status'],'build_status':build['status'],'local_validation_status':validation['status'] if validation else None,'local_validation_failure_visible':validation['status']=='failed' if validation else False,'spec_revision':spec['revision'],'spec_hash':spec['content_hash'],'actual_output_revision':result['spec_revision'],'actual_output_hash':result['task_spec_hash'],'stale_matches_source_comparison':expected_stale,'render_artifacts':[a['path'] for a in expected],'actual_images_loaded':True,'task_tabs_and_json_verified':True,'history_and_source_navigation_verified':True,'prior_failed_build_ids_retained':[entry['run_id'] for entry in failed_history],'usd_manifest_spec_report_get_verified':True,'synthetic_fixture':synthetic,'synthetic_evidence_notice_visible':synthetic,'action_retention_and_execution_boundaries_visible':True,'mobile_no_horizontal_overflow':True,'browser_api_methods':sorted(set(r['method'] for r in requests)),'browser_model_requests':0,'gpu_jobs_started':0,'hardware_commands_sent':robot['hardware_commands_sent'],'browser_errors':errors,'evidence_scope':'actual read-only UI and rendered scene artifacts; no task execution or hardware claim'}
(OUT/('task-scene-'+RUN+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
