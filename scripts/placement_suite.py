"""Submit one registered offline suite and persist its actual worker results."""
from pathlib import Path
import argparse,json,time,urllib.request,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from contracts.artifacts import batch_directory
def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',default='connector_small_v1');p.add_argument('--cases',nargs='+',help='Subset of predeclared case IDs');p.add_argument('--wait',action='store_true');p.add_argument('--batch',help='Resume reading an existing project-local batch; does not submit jobs');a=p.parse_args();base='http://127.0.0.1:8787'
    def get(path):return json.load(urllib.request.urlopen(base+path,timeout=30))
    if a.batch:
        directory=batch_directory(ROOT,a.batch);batch=json.loads((directory/'submitted.json').read_text())
    else:
        token=get('/api/session')['token'];request=urllib.request.Request(base+'/api/experiments/connector-placement',data=json.dumps({'suite_id':a.suite,**({'case_ids':a.cases} if a.cases else {})}).encode(),headers={'Content-Type':'application/json','X-Session-Token':token},method='POST');batch=json.load(urllib.request.urlopen(request,timeout=30));print(json.dumps(batch,ensure_ascii=False),flush=True)
        directory=batch_directory(ROOT,batch['batch_id'])
    if not a.wait:return
    while True:
        rows=[]
        for case in batch['runs']:
            run=get('/api/runs/'+case['run_id']);validation=next((t.get('result') or {} for t in run['tasks'] if t['stage']=='expert_validation'),{});execution=next((t.get('result') or {} for t in run['tasks'] if t['stage']=='expert_execution'),{})
            phase=next((t.get('result') or {} for t in run['tasks'] if t['stage']=='phase_validation'),{})
            rows.append(dict(case,phase_behavior_passed=phase.get('phase_behavior_passed'),actual_outcome=phase.get('actual_outcome'),failure_exit_passed=phase.get('failure_exit_passed'),phase_report=phase.get('report_path'),status=run['status'],stages={t['stage']:t['status'] for t in run['tasks']},experiment_passed=validation.get('experiment_passed'),action_passed=validation.get('action_passed'),retention_passed=validation.get('retention_passed'),full_task_passed=validation.get('full_task_passed'),stage_results=validation.get('stage_results'),validation_report=validation.get('report_path'),execution_report=execution.get('report_path'),errors=[{'stage':t['stage'],'error':(t.get('result') or {}).get('error') or (t.get('result') or {}).get('reason') or t.get('error')} for t in run['tasks'] if t['status'] in ('failed','blocked','needs_configuration')]))
        pending=any(r['status'] in ('queued','running') for r in rows);report=dict(batch,status='running' if pending else 'completed',results=rows,action_successes=sum(r['action_passed'] is True for r in rows),experiment_successes=sum(r['experiment_passed'] is True for r in rows),case_count=len(rows),generalization_scope=('predeclared PhysX perturbations; simulated-pose high-level recovery; no RGBD/hardware claim' if 'recovery' in batch['suite_id'] else 'predeclared initial XY/yaw cases; simulator ground-truth expert; no student/RGBD/disturbance claim'))
        if any('phase_validation' in r['stages'] for r in rows):
            report.update(phase_behavior_successes=sum(r['phase_behavior_passed'] is True for r in rows),failure_exit_successes=sum(r['failure_exit_passed'] is True for r in rows),generalization_scope='Explicit scripted phase guard + optional frozen v2 base MLP; physical action and abandon/reset are separate; no new learned v3/RGBD/hardware claim')
        report['split_results']={split:{'cases':len([r for r in rows if r.get('split')==split]),'action_passes':sum(r['action_passed'] is True for r in rows if r.get('split')==split),'experiment_passes':sum(r['experiment_passed'] is True for r in rows if r.get('split')==split)} for split in sorted({r.get('split','development') for r in rows})}
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'batch_id':batch['batch_id'],'status':report['status'],'cases':[(r['case_id'],r['status'],r['action_passed']) for r in rows]},ensure_ascii=False),flush=True)
        if not pending:return
        time.sleep(10)
if __name__=='__main__':main()
