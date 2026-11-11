"""Offline policy selection acceptance. Does not connect to robot hardware."""
from pathlib import Path
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait("document.body.innerText.includes('本地服务已连接')")
click('策略与真机');wait("document.querySelector('#policy-plugin')?.options.length>=2")
wait("document.querySelector('.active-policy-state').innerText.includes('rule_supervisor_v1')")
js("(()=>{const e=document.querySelector('#policy-plugin');e.value='state_mlp_v1';e.dispatchEvent(new Event('change',{bubbles:true}));})()")
wait("document.querySelector('#policy-checkpoint')!==null")
click('应用离线策略');wait("document.querySelector('.active-policy [role=alert]')!==null")
assert js("document.querySelector('.active-policy-state').innerText.includes('rule_supervisor_v1')")
screenshot('14-neural-validation-rejected')
js("(()=>{const e=document.querySelector('#policy-plugin');e.value='rule_supervisor_v1';e.dispatchEvent(new Event('change',{bubbles:true}));})()")
wait("document.querySelector('#policy-checkpoint')===null")
click('应用离线策略');wait("document.body.innerText.includes('离线策略配置已应用')")
screenshot('15-offline-policy-config')
assert not js("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('使能'))")
actual_robot=js("(async()=>await(await fetch('/api/robots/status')).json())()")
assert actual_robot['connected'] is False
assert actual_robot['hardware_commands_sent']==0
report={'status':'passed','empty_neural_checkpoint_rejected':True,'active_script_policy_preserved':True,'script_policy_offline_apply':True,'robot_hardware_connected':actual_robot['connected'],'hardware_commands_sent':actual_robot['hardware_commands_sent'],'browser_errors':errors}
assert not errors
(OUT/'policy-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
