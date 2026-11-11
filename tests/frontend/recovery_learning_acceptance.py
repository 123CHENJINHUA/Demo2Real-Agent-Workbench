"""Read-only browser acceptance of actual recovery training evidence."""
from pathlib import Path
import json,os,subprocess,shutil,sys,signal,time,urllib.request
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from modules.environment.service import local_environment
rid=sys.argv[1];detail=json.load(urllib.request.urlopen('http://127.0.0.1:8787/api/runs/'+rid));assert detail['input']['recovery_learning'] is True;assert detail['status']=='succeeded'
output=ROOT/'runs'/rid/'frontend';output.mkdir(exist_ok=True);evaluation=next(t['result'] for t in detail['tasks'] if t['stage']=='recovery_evaluation');training=next(t['result'] for t in detail['tasks'] if t['stage']=='recovery_learning')
chrome=shutil.which('google-chrome') or shutil.which('chromium');assert chrome
log=(output/'chrome.log').open('wb');process=subprocess.Popen([chrome,'--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check','--remote-debugging-port=9339','--user-data-dir='+str(ROOT/'.runtime/recovery-learning-browser'),'--disk-cache-dir='+str(ROOT/'.runtime/recovery-learning-browser-cache'),'about:blank'],cwd=ROOT,env=local_environment(ROOT),stdout=log,stderr=log,start_new_session=True)
try:
 for _ in range(40):
  try:urllib.request.urlopen('http://127.0.0.1:9339/json',timeout=1).read();break
  except OSError:time.sleep(.25)
 requests=[];helper=(ROOT/'tests/frontend/browser_smoke.py').read_text().split("cdp('Page.enable');")[0].replace('127.0.0.1:9338','127.0.0.1:9339')
 helper=helper.replace("if msg.get('method')=='Runtime.exceptionThrown':", "if msg.get('method')=='Network.requestWillBeSent':requests.append(msg['params']['request'])\n  if msg.get('method')=='Runtime.exceptionThrown':")
 exec(helper);OUT=output;cdp('Page.enable');cdp('Runtime.enable');cdp('Network.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE})
 wait("[...document.querySelectorAll('.task-row')].some(b=>b.textContent.includes("+json.dumps(rid)+"))");js("[...document.querySelectorAll('.task-row')].find(b=>b.textContent.includes("+json.dumps(rid)+")).click()")
 wait("document.body.innerText.includes('状态恢复学生训练')");assert js('document.querySelectorAll(".stage-card").length')==4
 expected=f"{evaluation['test_metrics']['macro_f1']*100:.1f}%";assert js('document.querySelector(".run-detail").innerText.includes('+json.dumps(expected)+')')
 assert js("document.querySelector('.run-detail').innerText.includes('不是双臂任务或恢复成功率')")
 wait("[...document.querySelectorAll('.run-detail a')].some(a=>a.textContent.includes('下载实际checkpoint'))")
 wait("document.querySelector('.run-detail').innerText.includes('实际物理闭环结果')");screenshot('learning-desktop');cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});screenshot('learning-mobile');assert js('document.documentElement.scrollWidth<=window.innerWidth')
 writes=[r['method'] for r in requests if '/api/' in r['url'] and r['method']!='GET'];assert not writes;assert not errors
 report={'status':'passed','run_id':rid,'actual_test_macro_f1':evaluation['test_metrics']['macro_f1'],'actual_selected_epoch':training['selected_epoch'],'four_real_stages':True,'checkpoint_link':True,'mobile_no_overflow':True,'api_writes':writes,'browser_errors':errors,'hardware_commands_sent':0};(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
except Exception:
 try:
  screenshot('failure');(output/'failure.json').write_text(json.dumps({'body':js('document.body.innerText')[:12000],'errors':errors,'requests':[{'url':x['url'],'method':x['method']} for x in requests]},ensure_ascii=False,indent=2))
 except Exception:pass
 raise
finally:
 try:os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=8)
 except (ProcessLookupError,subprocess.TimeoutExpired):
  try:os.killpg(process.pid,signal.SIGKILL)
  except ProcessLookupError:pass
 log.close()
