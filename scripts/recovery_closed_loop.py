"""Replay held-out conditions with the actual learned supervisor, then audit responses."""
from pathlib import Path
import argparse,json,sys,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.store import Store
from modules.environment.service import local_environment

def main():
 p=argparse.ArgumentParser();p.add_argument('--learning-run',required=True);p.add_argument('--version',choices=['v1','v2'],default='v1');p.add_argument('--batch',help='Only evaluate existing student batch');p.add_argument('--wait',action='store_true');a=p.parse_args();store=Store(ROOT/'.runtime/workbench.sqlite3')
 learning=store.get_run(a.learning_run);deployed=next(t['result'] for t in learning['tasks'] if t['stage']=='recovery_deployment' and t['status']=='succeeded')
 if a.batch:batch=a.batch
 else:
  suite=json.loads((ROOT/f'config/experiments/connector_recovery_{a.version}.json').read_text());suite['id']='connector_recovery_student_'+a.version;suite['scope_reason']='Frozen held-out initial conditions replayed using the learned supervisor; no test-based retuning';suite['cases']=[c for c in suite['cases'] if c['split']=='test']
  for case in suite['cases']:
   case['id']='student_'+case['id'];case['split']='closed_loop';case['recovery']['policy_path']=deployed['checkpoint']
  target=ROOT/f'config/experiments/connector_recovery_student_{a.version}.json';value=json.dumps(suite,ensure_ascii=False,indent=2)
  if target.exists() and target.read_text()!=value:raise ValueError('Student suite already frozen; preserve prior evaluation')
  target.write_text(value)
  command=[sys.executable,str(ROOT/'scripts/placement_suite.py'),'--suite',suite['id']]
  if a.wait:command.append('--wait')
  process=subprocess.Popen(command,cwd=ROOT,env=local_environment(ROOT),stdout=subprocess.PIPE,text=True)
  first=process.stdout.readline();print(first,end='',flush=True);batch=json.loads(first)['batch_id']
  for line in process.stdout:print(line,end='',flush=True)
  if process.wait():raise RuntimeError('Student suite submission/poll failed')
  if not a.wait:return
 from modules.evaluation.recovery_closed_loop import evaluate
 report=evaluate(ROOT,batch,a.learning_run);print(json.dumps({'batch_id':batch,'responses_passed':report['responses_passed'],'cases':report['case_count'],'action_passes':report['action_passes'],'action_applicable_cases':report['action_applicable_cases']},ensure_ascii=False))
if __name__=='__main__':main()
