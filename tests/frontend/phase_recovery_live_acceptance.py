"""Read-only actual browser check for phase recovery and physical release/home results; never launches experiments."""
from pathlib import Path
import os,subprocess,shutil,sys,signal,time,json,urllib.request
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from modules.environment.service import local_environment
rid=sys.argv[1];detail=json.load(urllib.request.urlopen('http://127.0.0.1:8787/api/runs/'+rid));batch_id=detail['input']['batch_id'];output=ROOT/'runs'/batch_id/'frontend-phase';output.mkdir(exist_ok=True)
chrome=shutil.which('google-chrome') or shutil.which('chromium');assert chrome
log=(output/'chrome.log').open('wb');profile=ROOT/'.runtime/phase-browser';process=subprocess.Popen([chrome,'--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check','--remote-debugging-port=9339','--user-data-dir='+str(profile),'--disk-cache-dir='+str(ROOT/'.runtime/phase-browser-cache'),'about:blank'],cwd=ROOT,env=local_environment(ROOT),stdout=log,stderr=log,start_new_session=True)
try:
 for _ in range(40):
  try:urllib.request.urlopen('http://127.0.0.1:9339/json',timeout=1).read();break
  except OSError:time.sleep(.25)
 helper=(ROOT/'tests/frontend/browser_smoke.py').read_text().split("cdp('Page.enable');")[0].replace('127.0.0.1:9338','127.0.0.1:9339');helper=helper.replace("targets[0]['webSocketDebuggerUrl']","next(t for t in targets if t.get('type')=='page')['webSocketDebuggerUrl']");exec(helper)
 OUT=output;cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE})
 open_run_card(rid)
 wait("document.body.innerText.includes('插接阶段恢复与失败归位')")
 assert js('document.querySelectorAll(".stage-card").length')==9
 actual=next(t['result'] for t in detail['tasks'] if t['stage']=='phase_validation')
 assert actual['phase_behavior_passed'] is True,actual
 if actual['actual_outcome']=='abandon_reset':
  wait("document.body.innerText.includes('已放弃并归位')")
  assert actual['failure_exit_passed'] is True and actual['action_passed'] is False
  wait("document.body.innerText.includes('归位最大关节误差')")
  wait("[...document.querySelectorAll('a')].some(a=>a.innerText.includes('松爪归位执行报告'))")
 else:
  assert actual['actual_outcome']=='action' and actual['action_passed'] is True
  wait("document.body.innerText.includes('完成动作')")
 assert not js("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('使能'))")
 screenshot('placement-result')
 cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});assert js('document.documentElement.scrollWidth<=window.innerWidth');screenshot('placement-mobile')
 assert not errors,errors
 report={'status':'passed','run_id':rid,'nine_real_stages_visible':True,'actual_outcome':actual['actual_outcome'],'failure_exit_passed':actual['failure_exit_passed'],'task_action_passed':actual['action_passed'],'actual_report_links':True,'mobile_no_overflow':True,'browser_errors':errors,'experiments_launched_by_browser':0,'model_requests':0,'hardware_commands_sent':0}
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
