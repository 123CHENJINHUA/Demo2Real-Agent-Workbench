"""Durable supervisor; all module execution happens in child worker processes."""
import json, os, signal, subprocess, sys, threading, time, fcntl
from pathlib import Path
from contracts.artifacts import run_directory
from contracts.analysis_agents import DISPATCH_STAGE
from contracts.task_dialog import WAITING_INPUT, UPSTREAM_BLOCKED
from orchestration.workflow import delegated_stages

class Scheduler:
    def __init__(self, root, store, concurrency=2, environment_builder=None, incident_reporter=None, interpreter_selector=None):
        self.root=Path(root);self.store=store;self.concurrency=concurrency;self.environment_builder=environment_builder;self.incident_reporter=incident_reporter;self.interpreter_selector=interpreter_selector
        self.processes={};self.stop_event=threading.Event();self.lock=threading.RLock()
    def start(self,restore=None):
        self.lock_file=(self.root/'.runtime/scheduler.lock').open('a+')
        try:fcntl.flock(self.lock_file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close();raise RuntimeError('此项目已有调度服务；禁止同时启动第二个调度器。')
        try:
            self.store.recover()
            if restore:
                restore(self.store)
        except Exception:
            self.lock_file.close()
            raise
        self.thread=threading.Thread(target=self.loop,name='workflow-supervisor',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if hasattr(self,'thread'):self.thread.join(timeout=3)
        with self.lock:
            for tid,record in list(self.processes.items()):
                self.terminate(record)
                self.store.transition(tid,'interrupted',error='服务关闭；离线任务需要显式重试。',finished_at=time.time(),pid=None)
            self.processes.clear()
        self.store.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        if hasattr(self,'lock_file') and not self.lock_file.closed:
            fcntl.flock(self.lock_file.fileno(),fcntl.LOCK_UN);self.lock_file.close()
    def terminate(self,record):
        process=record['process']
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        record['log'].close()
        self.store.execute('UPDATE jobs SET status=?,exit_code=?,finished_at=? WHERE id=?',('interrupted',process.poll(),time.time(),record.get('job_id')))
    def cancel(self,rid):
        with self.lock:
            if not self.store.run_exists(rid):raise KeyError(rid)
            for tid,record in list(self.processes.items()):
                if record['run_id']==rid:
                    self.terminate(record);self.processes.pop(tid)
                    self.store.execute('UPDATE jobs SET status=? WHERE id=?',('cancelled',record['job_id']))
            for task in self.store.query("SELECT * FROM tasks WHERE run_id=? AND status NOT IN ('succeeded','cancelled')",(rid,)):
                self.store.transition(task['id'],'cancelled',finished_at=time.time(),pid=None)
            self.store.execute('UPDATE runs SET status=?,updated_at=? WHERE id=?',('cancelled',time.time(),rid))
            self.store.event(rid,None,'run.cancelled',{})
    def retry(self,rid):
        with self.lock:
            if not self.store.run_exists(rid):raise KeyError(rid)
            if any(record['run_id']==rid for record in self.processes.values()):raise ValueError('任务仍在执行，请先取消或等待完成')
            if any(row['pending'] for row in self.store.questions(rid)):
                raise ValueError('请先在 Agent 对话中回复待处理的问题')
            for task in self.store.query("SELECT * FROM tasks WHERE run_id=? AND status!='succeeded'",(rid,)):
                self.store.transition(task['id'],'queued',error=None,result=None,progress={},started_at=None,finished_at=None,pid=None)
            self.store.execute('UPDATE runs SET status=?,updated_at=? WHERE id=?',('queued',time.time(),rid))
            self.store.event(rid,None,'run.retried',{})
    def answer(self,rid,qid,answer):
        with self.lock:
            self.store.answer_question(rid,qid,answer)
    def archive(self,rid):
        with self.lock:
            if any(record['run_id']==rid for record in self.processes.values()):
                raise ValueError('运行仍在执行，请先取消或等待结束')
            return self.store.archive_run(rid)
    def loop(self):
        while not self.stop_event.wait(.3):
            try:
                with self.lock:self.tick()
            except Exception as exc:
                self.store.event(None,None,'scheduler.error',{'error':str(exc)})
    def launch(self,run,task):
        out=self.root/run_directory(run)/task['stage'];out.mkdir(parents=True,exist_ok=True)
        (out/'result.json').unlink(missing_ok=True)
        env=os.environ.copy()
        for key in ('PYTHONPATH','PYTHONHOME'):env.pop(key,None)
        env.update({'PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(self.root/'.runtime/tmp'),'XDG_CACHE_HOME':str(self.root/'.runtime/cache'),'XDG_CONFIG_HOME':str(self.root/'.runtime/config'),'XDG_DATA_HOME':str(self.root/'.runtime/data'),'MPLCONFIGDIR':str(self.root/'.runtime/cache/matplotlib')})
        if self.environment_builder:env=self.environment_builder(self.root)
        log=(out/'worker.log').open('ab')
        started_at=time.time()
        try:
            interpreter=self.interpreter_selector(self.root,task['stage']) if self.interpreter_selector else sys.executable
            process=subprocess.Popen([interpreter,'-m','workers.run_stage','--root',str(self.root),'--run-id',run['id'],'--stage',task['stage']],cwd=self.root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        except (OSError,ValueError) as exc:
            log.close()
            message=f'执行进程启动失败：{exc}'
            self.store.transition(task['id'],'failed',error=message,result={'status':'failed','error':message},
                                  finished_at=time.time(),pid=None)
            self.store.event(run['id'],task['id'],'supervisor.runtime_failure',{'stage':task['stage'],'message':message})
            return False
        job_id=task['id']+':'+str(task['attempt']+1)
        self.store.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',(job_id,run['id'],task['id'],task['attempt']+1,'running',process.pid,str((out/'worker.log').relative_to(self.root)),None,started_at,None))
        self.processes[task['id']]={'job_id':job_id,'process':process,'log':log,'run_id':run['id'],'stage':task['stage'],'output_dir':out,'heartbeat':0,'resource':'gpu' if task['stage'] in ('scene_build','scene_validation','simulation','expert_execution','learning','evaluation') else 'cpu'}
        if task['stage']==DISPATCH_STAGE:
            self.processes[task['id']]['specialist_count']=run['input'].get('specialist_count')
        self.store.transition(task['id'],'running',attempt=task['attempt']+1,started_at=started_at,heartbeat_at=time.time(),pid=process.pid)
        return True
    def tick(self):
        for tid,record in list(self.processes.items()):
            process=record['process'];code=process.poll()
            if code is None:
                if time.time()-record['heartbeat']>3:
                    record['heartbeat']=time.time()
                    self.store.execute('UPDATE tasks SET heartbeat_at=? WHERE id=?',(time.time(),tid))
                    self.store.execute('UPDATE agents SET heartbeat_at=? WHERE task_id=?',(time.time(),tid))
                continue
            record['log'].close();self.processes.pop(tid)
            result_path=record['output_dir']/'result.json'
            try:
                result=json.loads(result_path.read_text())
                if not isinstance(result,dict):
                    raise ValueError('执行结果缺少阶段记录')
            except (OSError,ValueError):result={'status':'failed','error':f'worker退出码 {code}，没有有效result.json'}
            state=result.get('status','failed')
            if state not in ('succeeded','blocked','needs_configuration','failed','cancelled',WAITING_INPUT):state='failed'
            question=result.get('question') if state==WAITING_INPUT else None
            if state==WAITING_INPUT and not question:
                state='failed'
                result={'status':state,'error':'阶段未提供可回复的问题'}
            delegated=None
            if state=='succeeded' and record.get('stage')==DISPATCH_STAGE:
                try:delegated=delegated_stages(result,record.get('specialist_count'))
                except (ValueError,KeyError) as exc:
                    state='failed';result=dict(result,status=state,error=f'派工契约校验失败：{exc}')
            if state=='failed' and not result.get('supervision_report_path'):
                summary=result.get('error') or result.get('reason') or '执行进程未提交监督记录'
                self.store.event(record['run_id'],tid,'supervisor.runtime_failure',
                                 {'stage':record['stage'],'exit_code':code,'message':summary})
            self.store.transition(tid,state,delegated=delegated,question=question,result=result,error=result.get('error') or result.get('reason'),finished_at=time.time(),pid=None)
            self.store.execute('UPDATE jobs SET status=?,exit_code=?,finished_at=? WHERE id=?',(state,code,time.time(),record['job_id']))
            if state=='failed':
                log_path=str((record['output_dir']/'worker.log').relative_to(self.root))
                details={'worker_log':log_path}
                if self.incident_reporter:
                    try:details['experience_record']=self.incident_reporter(self.root,record['run_id'],tid,result.get('error','worker failed'),log_path,result)
                    except Exception as exc:self.store.event(record['run_id'],tid,'experience.archive_failed',{'error':str(exc)})
                self.store.incident(record['run_id'],tid,details.get('experience_record',{}).get('primary_category','workflow_integrity'),result.get('error','worker failed'),details)
            candidates=list(result.get('artifacts',[]))
            candidates.extend({'path':str(p.relative_to(self.root))} for p in (record['output_dir']/'worker.log',result_path) if p.is_file())
            seen=set()
            for artifact in candidates:
                if isinstance(artifact,str):artifact={'path':artifact}
                rel=artifact.get('path','');path=(self.root/rel).resolve()
                if not path.is_relative_to(self.root.resolve()) or not path.is_file() or str(path) in seen:continue
                seen.add(str(path))
                self.store.register_artifact(self.root,record['run_id'],tid,rel,artifact.get('name'))
        runs=self.store.query("""SELECT * FROM runs WHERE status IN ('queued','running')
            AND id NOT IN (SELECT run_id FROM archived_runs) ORDER BY created_at""")
        for run in runs:
            tasks=self.store.query('SELECT * FROM tasks WHERE run_id=? ORDER BY rowid',(run['id'],));states={t['stage']:t['status'] for t in tasks}
            for task in tasks:
                if task['status']!='queued':continue
                deps=[states[d] for d in task['deps']]
                if any(s in ('failed','blocked','needs_configuration','interrupted','cancelled') for s in deps):
                    self.store.transition(task['id'],'blocked',error=UPSTREAM_BLOCKED);states[task['stage']]='blocked'
                elif all(s=='succeeded' for s in deps) and len(self.processes)<self.concurrency and not (task['stage'] in ('scene_build','scene_validation','simulation','expert_execution','learning','evaluation') and any(p['resource']=='gpu' for p in self.processes.values())):
                    states[task['stage']]='running' if self.launch(run,task) else 'failed'
            values=list(states.values())
            runnable=any(states[task['stage']]=='queued' and all(states[dep]=='succeeded' for dep in task['deps']) for task in tasks)
            if 'running' in values or runnable:
                status='running'
            elif WAITING_INPUT in values:
                status=WAITING_INPUT
            else:
                status='succeeded' if all(s=='succeeded' for s in values) else ('failed' if 'failed' in values else 'blocked')
            self.store.execute('UPDATE runs SET status=?,updated_at=? WHERE id=?',(status,time.time(),run['id']))
