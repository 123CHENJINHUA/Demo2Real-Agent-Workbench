"""Evaluate actual student intervention responses separately from task completion."""
from pathlib import Path
import json,hashlib
import numpy as np
from backend.store import Store

def evaluate(root,batch_id,learning_run):
 root=Path(root).resolve();store=Store(root/'.runtime/workbench.sqlite3');learning=store.get_run(learning_run)
 deployed=next(t['result'] for t in learning['tasks'] if t['stage']=='recovery_deployment' and t['status']=='succeeded');package=root/deployed['package'];policy=package/'policy.npz';policy_sha=hashlib.sha256(policy.read_bytes()).hexdigest()
 manifest=json.loads((package/'manifest.json').read_text());classification=json.loads((package/'evaluation.json').read_text())
 if policy_sha!=manifest['policy_sha256'] or policy_sha!=classification['checkpoint_sha256']:raise ValueError('Deployed weights changed after classification evaluation')
 batch=root/'runs'/batch_id;submission=json.loads((batch/'submitted.json').read_text());suite=json.loads((batch/'suite.json').read_text())
 if suite['id'] not in ('connector_recovery_student_v1','connector_recovery_student_v2'):raise ValueError('Explicit student suite required')
 if len(suite['cases'])!=6 or len(submission['runs'])!=6 or {e['case_id'] for e in submission['runs']}!={c['id'] for c in suite['cases']}:raise ValueError('All six frozen student cases required')
 results=[]
 for entry in submission['runs']:
  job=store.get_run(entry['run_id'])
  if job['status'] in ('running','queued'):raise ValueError('Student evaluation still running')
  expected=next(c for c in suite['cases'] if c['id']==entry['case_id']);cfg=expected['recovery'];kind=cfg['kind'];arm=cfg['arm'];errors=[];raw={};summary={};raw_modes=[];actions=[];observations=np.empty((0,24))
  checks={}
  try:
   execution=next(t['result'] for t in job['tasks'] if t['stage']=='expert_execution');raw_path=root/execution['report_path'];raw=json.loads(raw_path.read_text());summary=raw['report']['recovery_policy'];plan=json.loads((root/raw['plan_path']).read_text())
   with np.load(root/raw['supervision_path'],allow_pickle=False) as d:raw_modes=d['raw_modes'];actions=d['executed_modes'];observations=d['observations']
   checks['learned_policy_executed']=raw['learned_policy_executed'] is True
   checks['weight_bound_to_plan']=plan['input_hashes'].get(str(policy.relative_to(root)))==policy_sha
   checks['actual_checkpoint_selected']=summary['policy_checkpoint']==str(policy.relative_to(root))
   checks['no_hardware_commands']=raw['hardware_commands_sent']==0
   checks['observation_guard_accounted']=int(np.count_nonzero(raw_modes!=actions))==summary['observation_guard_overrides']
   checks['intervention_triggered']=kind=='none' or any(e['kind']==kind and e['arm']==arm for e in summary['events'])
   validation=next((t['result'] or {} for t in job['tasks'] if t['stage']=='expert_validation'),{})
   if kind=='remove':checks['expected_response']=summary['requires_human'] is True and 4 in raw_modes and 4 in actions and raw['action_passed'] is False
   else:
    checks['independent_task_action']=validation.get('action_passed') is True and validation.get('experiment_passed') is True
    checks['expected_response']=not summary['requires_human']
    if kind=='occlude':checks['expected_response'] &= bool(np.any(((observations[:,0]<.5)|(observations[:,10]<.5))&(raw_modes==1)&(actions==1)))
    if kind in ('drop','relocate'):checks['expected_response'] &= (2+arm in actions and any(r['arm']==arm and r['passed'] is True for r in summary['recoveries']))
  except (ValueError,KeyError,StopIteration,OSError) as exc:errors.append(str(exc));checks['complete_evidence']=False
  results.append(dict(entry,disturbance=kind,arm=arm,checks=checks,response_passed=bool(checks) and all(checks.values()),action_passed=raw.get('action_passed'),retention_passed=raw.get('retention_passed'),requires_human=summary.get('requires_human'),recoveries=summary.get('recoveries'),decision_counts=summary.get('counts'),raw_decision_counts={str(k):int(np.count_nonzero(raw_modes==k)) for k in range(5)},guard_overrides=summary.get('observation_guard_overrides'),errors=errors))
 report={'schema_version':'recovery-closed-loop/v1','suite_id':suite['id'],'learning_run':learning_run,'batch_id':batch_id,'policy_sha256':policy_sha,'evaluation_scope':'actual closed-loop high-level recovery with deterministic demonstration motion; simulated pose only','cases':results,'responses_passed':sum(r['response_passed'] for r in results),'case_count':len(results),'action_applicable_cases':sum(r['disturbance']!='remove' for r in results),'action_passes':sum(r['action_passed'] is True for r in results),'all_responses_passed':all(r['response_passed'] for r in results),'retention_record_only':True,'hardware_verified':False,'hardware_commands_sent':0,'test_tuning':False}
 out=batch/'student_closed_loop.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));copy=package/'closed_loop.json';copy.write_text(json.dumps(report,ensure_ascii=False,indent=2));manifest=json.loads((package/'manifest.json').read_text());manifest.update(closed_loop_evaluated=True,closed_loop_passed=report['all_responses_passed'],closed_loop_report_sha256=hashlib.sha256(copy.read_bytes()).hexdigest(),closed_loop_report=str(out.relative_to(root)));(package/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 evidence_task=next(t['id'] for t in learning['tasks'] if t['stage']=='recovery_evaluation')
 for path,name in [(out,'学生物理闭环评价（含失败）'),(package/'manifest.json','学生仿真包最终清单')]:
  store.add_artifact(learning_run,evidence_task,{'path':str(path.relative_to(root)),'name':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'media_type':'application/json'})
 store.event(learning_run,evidence_task,'recovery.closed_loop_evaluated',{'batch_id':batch_id,'responses_passed':report['responses_passed'],'case_count':report['case_count'],'all_responses_passed':report['all_responses_passed'],'hardware_commands_sent':0})
 return report
