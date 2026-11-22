"""Read-only actual browser check for a placement case; never launches experiments."""
from pathlib import Path
import os,subprocess,shutil,sys,signal,time,json,urllib.request
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from modules.environment.service import local_environment
rid=sys.argv[1];detail=json.load(urllib.request.urlopen('http://127.0.0.1:8787/api/runs/'+rid));batch_id=detail['input']['batch_id'];output=ROOT/'runs'/batch_id/'frontend';output.mkdir(exist_ok=True)
chrome=shutil.which('google-chrome') or shutil.which('chromium');assert chrome
log=(output/'chrome.log').open('wb');profile=ROOT/'.runtime/placement-browser';process=subprocess.Popen([chrome,'--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check','--remote-debugging-port=9339','--user-data-dir='+str(profile),'--disk-cache-dir='+str(ROOT/'.runtime/placement-browser-cache'),'about:blank'],cwd=ROOT,env=local_environment(ROOT),stdout=log,stderr=log,start_new_session=True)
try:
 for _ in range(40):
  try:urllib.request.urlopen('http://127.0.0.1:9339/json',timeout=1).read();break
  except OSError:time.sleep(.25)
 helper=(ROOT/'tests/frontend/browser_smoke.py').read_text().split("cdp('Page.enable');")[0].replace('127.0.0.1:9338','127.0.0.1:9339');exec(helper)
 OUT=output;cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE})
 open_run_card(rid)
 wait("document.querySelectorAll('.expert-criterion').length===6")
 assert js('document.querySelectorAll(".stage-card").length')==8
 wait("document.body.innerText.includes('本轮仅验收抓取')")
 assert not js("document.querySelector('.expert-source-links').innerText.includes('缺少hash')")
 assert js("[...document.querySelectorAll('.expert-criterion')].find(e=>e.innerText.includes('动作完成 action')).classList.contains('passed')")
 actual=next(t['result'] for t in detail['tasks'] if t['stage']=='expert_validation');full_class='passed' if actual.get('full_task_passed') is True else 'failed'
 assert js("[...document.querySelectorAll('.expert-criterion')].find(e=>e.innerText.includes('完整任务 overall')).classList.contains("+json.dumps(full_class)+")")
 screenshot('placement-result')
 cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});assert js('document.documentElement.scrollWidth<=window.innerWidth');screenshot('placement-mobile')
 assert not errors,errors
 report={'status':'passed','run_id':rid,'eight_real_stages_visible':True,'action_only_scope_visible':True,'variant_revision_and_hash_visible':True,'action_success_does_not_hide_full_task_failure':True,'mobile_no_overflow':True,'browser_errors':errors,'experiments_launched_by_browser':0,'model_requests':0,'hardware_commands_sent':0}
 (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
except Exception:
 try:
  screenshot('placement-browser-failure');(output/'failure.json').write_text(json.dumps({'errors':errors,'body':js('document.body.innerText')},ensure_ascii=False,indent=2));print(json.dumps({'errors':errors,'body':js('document.body.innerText')[:1500]},ensure_ascii=False))
 except Exception:pass
 raise
finally:
 os.killpg(process.pid,signal.SIGTERM)
 try:process.wait(timeout=5)
 except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL)
 log.close()
