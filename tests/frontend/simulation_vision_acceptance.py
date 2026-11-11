"""Read-only browser validation of actual completed simulator perception artifacts."""
from pathlib import Path
import re
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
RUN='3035e6ca201844ef'
with urllib.request.urlopen(BASE+'/api/runs/'+RUN) as response:run=json.load(response)
assert run['status']=='succeeded'
prefix='runs/'+RUN+'/simulation/'
candidates=[]
for artifact in run['artifacts']:
 path=artifact.get('path','')
 if not path.startswith(prefix):continue
 match=re.fullmatch(r'isaac_(\d+)/(?:[^/]+/)*vision/visualization/frame_(\d+(?:\.\d+)?)/panels\.png',path[len(prefix):])
 if match:candidates.append((int(match[1]),float(match[2]),artifact))
assert candidates
_,frame,expected=max(candidates,key=lambda v:(v[0],v[1]))
cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait("document.body.innerText.includes('本地服务已连接')")
wait("[...document.querySelectorAll('.task-row')].some(b=>b.textContent.includes('"+RUN+"'))")
js("[...document.querySelectorAll('.task-row')].find(b=>b.textContent.includes('"+RUN+"')).click()")
wait("document.querySelector('.simulation-vision-panel img')!==null")
# Trigger lazy loading without changing the job or simulator state.
js("document.querySelector('.simulation-vision-panel').scrollIntoView({block:'center'})")
wait("document.querySelector('.simulation-vision-panel img').complete&&document.querySelector('.simulation-vision-panel img').naturalWidth>0")
assert js("document.querySelector('.simulation-vision-panel img').src").endswith('/api/artifacts/'+expected['id'])
assert js("document.body.innerText.includes('插入及松手完成；松手后保持验证未通过')")
assert js("document.querySelector('.simulation-validation-notice').getAttribute('role')")=='alert'
assert js("document.querySelector('.run-previews')===null"), 'Simulator run must not fake demo contact sheets'
js('window.scrollTo(0,0)');time.sleep(.15)
screenshot('16-simulation-rgbd-result')
cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});screenshot('17-mobile-simulation-result')
assert js('document.documentElement.scrollWidth<=window.innerWidth')
cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
js("[...document.querySelectorAll('.task-row')].find(b=>b.textContent.includes('e7320b6960634a63')).click()")
wait("document.querySelector('.run-previews')!==null")
assert js("document.querySelector('.simulation-vision-panel')===null"), 'Demo contact sheets must not become simulator results'
js("[...document.querySelectorAll('.task-row')].find(b=>b.textContent.includes('"+RUN+"')).click()")
wait("document.querySelector('.simulation-vision-panel')!==null")
actual_robot=js("(async()=>await(await fetch('/api/robots/status')).json())()")
assert actual_robot['connected'] is False and actual_robot['hardware_commands_sent']==0
report={'status':'passed','run_id':RUN,'workflow_status':run['status'],'saved_panel_candidates':len(candidates),'selected_artifact':expected,'selected_frame_time_s':frame,'actual_simulator_image_loaded':True,'numeric_latest_selection_verified':True,'failed_post_release_retention_visible':True,'demonstration_images_separate':True,'mobile_no_horizontal_overflow':True,'read_only_test':True,'new_diagnostics_triggered':0,'hardware_commands_sent':actual_robot['hardware_commands_sent'],'browser_errors':errors}
assert not errors
(OUT/'simulation-vision-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='selected_artifact'},ensure_ascii=False));ws.close()
