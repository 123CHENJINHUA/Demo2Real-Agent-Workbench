"""Thin, subprocess-isolated entrypoint; concrete binding lives in application."""
import argparse, json, traceback, signal
from pathlib import Path
from backend.store import Store
from application.registry import execute_stage

def main():
    def interrupted(signum, frame):raise InterruptedError(f"worker interrupted by signal {signum}")
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--run-id',required=True);parser.add_argument('--stage',required=True);args=parser.parse_args()
    root=Path(args.root).resolve();store=Store(root/'.runtime/workbench.sqlite3');run=store.get_run(args.run_id)
    task=next(t for t in run['tasks'] if t['stage']==args.stage);out=root/'runs'/args.run_id/args.stage;out.mkdir(parents=True,exist_ok=True)
    def emit(kind,payload):
        store.event(args.run_id,task['id'],kind,payload)
        if kind in ('progress','task.progress'):store.execute('UPDATE tasks SET progress=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),task['id']))
    def check_cancel():
        row=store.query('SELECT status FROM tasks WHERE id=?',(task['id'],))[0]
        if row['status']=='cancelled':raise InterruptedError('cancelled')
    ctx={'root':root,'run_id':args.run_id,'output_dir':out,'input':run['input'],'config':{},'dependencies':{t['stage']:t['result'] for t in run['tasks'] if t['result']},'emit':emit,'check_cancel':check_cancel}
    try:result=execute_stage(args.stage,ctx)
    except InterruptedError:result={'status':'cancelled','error':'cancelled'}
    except Exception as exc:
        traceback.print_exc();result={'status':'failed','error':str(exc),'exception':type(exc).__name__}
    tmp=out/'result.json.tmp';tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));tmp.replace(out/'result.json')
if __name__=='__main__':main()
