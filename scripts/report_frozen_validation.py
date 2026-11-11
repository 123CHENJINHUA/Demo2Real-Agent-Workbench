"""Read-only postprocessing for the predeclared frozen expert evaluation."""
from pathlib import Path
import argparse,hashlib,json,time,urllib.request,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modules.experiments.locks import verify_lock,digest
from modules.planning.expert_binding import load_plan

def main():
    p=argparse.ArgumentParser();p.add_argument('batch');p.add_argument('--wait',action='store_true');a=p.parse_args();folder=(ROOT/'runs'/a.batch).resolve();folder.relative_to(ROOT/'runs');submitted=json.loads((folder/'submitted.json').read_text());suite=json.loads((folder/'suite.json').read_text());base='http://127.0.0.1:8787'
    def get(path):return json.load(urllib.request.urlopen(base+path,timeout=60))
    while True:
        runs=[get('/api/runs/'+entry['run_id']) for entry in submitted['runs']]
        if not any(r['status'] in ('queued','running') for r in runs):break
        if not a.wait:print('Evaluation is still running');return
        time.sleep(10)
    frozen=verify_lock(ROOT,submitted['implementation_lock_path'],submitted['implementation_lock_sha256']);lock=json.loads((ROOT/frozen['path']).read_text());external=lock['external_read_only_provenance'];rows=[]
    for entry,run in zip(submitted['runs'],runs):
        tasks={t['stage']:t for t in run['tasks']};execution=tasks['expert_execution'].get('result') or {};validation=tasks['expert_validation'].get('result') or {};raw=json.loads((ROOT/execution['report_path']).read_text()) if execution.get('report_path') else {};trial=raw.get('report',{});evidence_checks=[]
        plan_ref=(tasks['expert_plan'].get('result') or {}).get('plan_path')
        if plan_ref:
            plan=load_plan(ROOT,plan_ref);evidence_checks.append(plan.get('implementation_lock')=={'path':frozen['path'],'sha256':frozen['sha256']})
        else:evidence_checks.append(False)
        hashes=raw.get('source_sha256',{});required=['modules/motion_expert/isaac_runtime.py','tasks/connector/legacy/sim_bridge/connector_coordinator.py','tasks/connector/legacy/sim_bridge/connector_dexmimic.py']
        evidence_checks.extend(hashes.get(k)==frozen['files'][k] for k in required)
        evidence_checks.append(trial.get('generator',{}).get('generator_sha256')==external['mimic_generator_sha256'])
        evidence_checks.append(trial.get('generator',{}).get('isaaclab_version')==external['isaaclab_version'])
        for ref,expected in validation.get('evidence_sha256',{}).items():evidence_checks.append(digest(ROOT/ref)==expected)
        detail=validation.get('stage_results',{});insertion=detail.get('insertion',{}).get('details');depth=insertion.get('depth_m') if isinstance(insertion,dict) else None
        rows.append(dict(entry,status=run['status'],action_passed=validation.get('action_passed'),experiment_passed=validation.get('experiment_passed'),retention_passed=validation.get('retention_passed'),full_task_passed=validation.get('full_task_passed'),stage_results={k:v.get('passed') for k,v in detail.items()},insertion_depth_m=depth,implementation_and_evidence_verified=all(evidence_checks),validation_report=validation.get('report_path'),execution_report=execution.get('report_path'),error=trial.get('error') or validation.get('error'),upstream_errors=[{'stage':t['stage'],'error':(t.get('result') or {}).get('reason') or (t.get('result') or {}).get('error') or t.get('error')} for t in run['tasks'] if t['status'] in ('failed','blocked','needs_configuration')]))
    splits={s:{'cases':sum(r['split']==s for r in rows),'action_passes':sum(r['split']==s and r['action_passed'] is True for r in rows),'experiment_passes':sum(r['split']==s and r['experiment_passed'] is True for r in rows),'evidence_verified':all(r['implementation_and_evidence_verified'] for r in rows if r['split']==s)} for s in ('regression','holdout')}
    expected=suite['acceptance'];gate=all(r['implementation_and_evidence_verified'] for r in rows) and splits['regression']['cases']==5 and splits['holdout']['cases']==10 and splits['regression']['experiment_passes']==expected['regression_required_passes'] and splits['holdout']['experiment_passes']==expected['holdout_required_passes']
    robot=get('/api/robots/status');assert not robot['connected'] and robot['hardware_commands_sent']==0
    report={'schema_version':'frozen-expert-evaluation/v1','batch_id':a.batch,'status':'completed','implementation_lock':{'path':frozen['path'],'sha256':frozen['sha256'],'file_count':len(frozen['files'])},'split_results':splits,'rgbd_pilot_gate_passed':gate,'retention_record_only':True,'training_allowed_by_this_report':False,'holdout_runs_must_not_enter_student_training':True,'results':rows,'hardware_commands_sent':0,'scope':'fixed project expert/config/assets, five regression and ten novel XY/yaw cases; privileged simulation only; no statistical whole-range/hardware reliability claim'}
    report['postprocessor_sha256']=digest(Path(__file__));(folder/'final_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));lines=['# 固定版本专家验证结果','',f"批次：{a.batch}；锁定{len(frozen['files'])}项输入。",'',f"复核：{splits['regression']['experiment_passes']}/5；留出：{splits['holdout']['experiment_passes']}/10。",'', '| 案例 | 分组 | 抓取 | 插入 | 松手 | 动作 | 插深mm | 保持（仅记录） |','| --- | --- | --- | --- | --- | --- | --- | --- |']
    def verdict(value):return '通过' if value is True else '失败' if value is False else '未执行'
    for r in rows:
        stages=r['stage_results'];depth='—' if r['insertion_depth_m'] is None else f"{r['insertion_depth_m']*1000:.2f}"
        lines.append('| '+ ' | '.join([r['case_id'],r['split'],verdict(stages.get('grasp')),verdict(stages.get('insertion')),verdict(stages.get('release')),verdict(r['action_passed']),depth,verdict(r['retention_passed'])])+' |')
    lines+=['','RGBD小试关卡：'+('通过' if gate else '未通过')+'。保持不计入动作通过条件；完整任务事实仍保留。','', '本轮没有调参或替换失败案例。留出回合只供评价，不自动进入学生训练。当前未训练学生，未验证RGBD/干扰/真机。','', '下一步：'+('接回真实渲染RGBD观测进行小规模对照，再生成独立训练位置与扰动数据。' if gate else '保留当前冻结结果，定位失败；修复须另建版本，当前已观察到的失败不能继续冒充全新留出。')]
    (folder/'REPORT.md').write_text('\n'.join(lines)+'\n');print(json.dumps({'batch':a.batch,'split_results':splits,'rgbd_pilot_gate_passed':gate,'report':str((folder/'final_report.json').relative_to(ROOT))},ensure_ascii=False))
if __name__=='__main__':main()
