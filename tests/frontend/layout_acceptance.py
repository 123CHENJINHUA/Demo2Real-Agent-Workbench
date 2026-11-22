"""Read-only layout/evidence acceptance against an existing real demo run."""
from pathlib import Path
exec((Path(__file__).parent/'browser_smoke.py').read_text().split("cdp('Page.enable');")[0])
cdp('Page.enable');cdp('Runtime.enable');cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False});cdp('Page.navigate',{'url':BASE});wait("document.body.innerText.includes('本地服务已连接')")
open_run_card('e7320b6960634a63')
wait("document.querySelectorAll('.preview-grid img').length===3")
wait("[...document.querySelectorAll('.preview-grid img')].every(i=>i.complete&&i.naturalWidth>0)")
screenshot('10-workbench-evidence-preview')
click('产物文件')
assert 0 < js("document.querySelector('.artifact-list').getBoundingClientRect().height")<=301
assert js("document.querySelectorAll('.artifact-list a').length")<81
assert not js("[...document.querySelectorAll('.artifact-list a')].some(a=>/\\.(mp4|parquet)/.test(a.textContent))")
assert js('document.documentElement.scrollHeight')<4000
js("(()=>{const s=document.querySelector('[aria-label=工件分类]');s.value='all';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
wait("document.querySelectorAll('.artifact-list a').length===81")
fill('[aria-label=搜索工件]','contact_sheet');wait("document.querySelectorAll('.artifact-list a').length===3")
screenshot('11-artifact-filter')
cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});screenshot('12-mobile-evidence')
assert js('document.documentElement.scrollWidth<=window.innerWidth')
cdp('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
click('环境与模型配置');wait("document.querySelector('#data_python')!==null");wait("document.querySelector('#data_python').value.length>0")
# A test without a configured provider must never be presented as a successful model request.
click('测试实际模型调用');wait("document.querySelector('[role=alert]')!==null")
assert not js("document.body.innerText.includes('真实模型请求已通过')")
screenshot('13-provider-needs-configuration')
report={'status':'passed','run_id':'e7320b6960634a63','three_real_contact_sheets_loaded':True,'all_81_artifacts_accessible':True,'default_filters_dataset':True,'artifact_scroll_height_lte_300':True,'contact_sheet_search_count':3,'mobile_no_horizontal_overflow':True,'unconfigured_provider_not_success':True,'browser_errors':errors}
assert not errors
(OUT/'layout-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));ws.close()
