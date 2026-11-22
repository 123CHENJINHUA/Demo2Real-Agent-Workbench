"""Thin, subprocess-isolated entrypoint; concrete binding lives in application."""
import argparse, json, traceback, signal
from pathlib import Path
from backend.store import Store
from application.registry import execute_stage
from contracts.artifacts import run_directory


def publish_scene(store,root,directory,run_id,task,payload):
    build=payload['build']
    preview=(root/build['preview_path']).resolve()
    manifest=(root/build['manifest_path']).resolve()
    if not preview.is_relative_to(directory.resolve()) or manifest.parent!=preview.parent:
        raise ValueError('发布场景必须属于当前任务的构建目录')
    required=(preview,manifest)
    references={*required, *preview.parent.glob('*.png')}
    references.update(preview.parent/name for name in ('scene.usda','task_spec.json','build_report.json','frame_axes.svg'))
    for key in ('validation_path','repair_report_path','visual_comparison_path'):
        if payload.get(key):
            references.add(root/payload[key])
    for path in sorted(references):
        resolved=path.resolve()
        if not resolved.is_relative_to(directory.resolve()):
            raise ValueError('场景工件超出当前示教任务目录')
        artifact=store.register_artifact(root,run_id,task['id'],resolved.relative_to(root))
        if path in required and not artifact:
            raise ValueError('发布场景缺少渲染或场景清单')
    store.event(run_id,task['id'],'scene.published',payload | {'stage':task['stage']})


def main():
    def interrupted(signum, frame):raise InterruptedError(f"worker interrupted by signal {signum}")
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--run-id',required=True);parser.add_argument('--stage',required=True);args=parser.parse_args()
    root=Path(args.root).resolve();store=Store(root/'.runtime/workbench.sqlite3');run=store.get_run(args.run_id)
    task=next(t for t in run['tasks'] if t['stage']==args.stage);directory=root/run_directory(run);out=directory/args.stage;out.mkdir(parents=True,exist_ok=True)
    def emit(kind,payload):
        if kind=='scene.published':
            publish_scene(store,root,directory,args.run_id,task,payload)
            return
        store.event(args.run_id,task['id'],kind,payload)
        if kind in ('progress','task.progress'):store.execute('UPDATE tasks SET progress=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),task['id']))
    def check_cancel():
        row=store.query('SELECT status FROM tasks WHERE id=?',(task['id'],))[0]
        if row['status']=='cancelled':raise InterruptedError('cancelled')
    ctx={'root':root,'run_id':args.run_id,'task_id':task['id'],'stage_role':task['role'],'output_dir':out,'input':run['input'],'config':{},'dependencies':{t['stage']:t['result'] for t in run['tasks'] if t['result']},'emit':emit,'check_cancel':check_cancel,
         'active_scene':run['active_scene'],'user_replies':[
             {'question':row['prompt'],'answer':row['answer']} for row in run['questions']
             if row['task_id']==task['id'] and row['answer'] is not None][-5:]}
    try:result=execute_stage(args.stage,ctx)
    except InterruptedError:result={'status':'cancelled','error':'cancelled'}
    except Exception as exc:
        traceback.print_exc();result={'status':'failed','error':str(exc),'exception':type(exc).__name__}
    tmp=out/'result.json.tmp';tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));tmp.replace(out/'result.json')
if __name__=='__main__':main()
