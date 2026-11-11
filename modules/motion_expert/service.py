"""Launch only a checked, scene-bound expert in an isolated Isaac worker."""
from pathlib import Path
import fcntl,json,os,signal,subprocess,time
from modules.environment.service import resolve,local_environment


def run(ctx):
    from modules.planning.expert_binding import load_plan
    root=Path(ctx['root']).resolve();out=Path(ctx['output_dir']).resolve();out.relative_to(root)
    reference=ctx.get('dependencies',{}).get('expert_plan',{}).get('plan_path') or ctx['input'].get('plan_path')
    if not reference:return {'status':'needs_configuration','reason':'需要已核验的expert_plan；不自动使用旧控制器默认场景'}
    try:plan=load_plan(root,reference)
    except (OSError,ValueError,KeyError) as exc:return {'status':'blocked','reason':str(exc)}
    out.mkdir(parents=True,exist_ok=True);trial=out/('execution_'+str(time.time_ns()));trial.mkdir()
    command=[str(resolve(root,'isaac_sim_root')/'python.sh'),str(root/'workers/execute_expert.py'),'--plan',str(root/reference),'--output',str(trial),'--portable-root',str(root/'.runtime/isaac-expert')]
    env=local_environment(root);env['PYTHONPATH']=os.pathsep.join([str(root/'tasks/connector/legacy'),str(root)]);env['OMP_NUM_THREADS']='2';env['OPENBLAS_NUM_THREADS']='2'
    lock=(root/'.runtime/gpu.lock').open('a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();return {'status':'blocked','reason':'GPU被当前应用的其他仿真占用，待结束后重试'}
    process=None;log_path=trial/'isaac_expert.log'
    ctx['emit']('expert.launch',{'plan_path':reference,'task_spec_hash':plan.get('task_spec_hash'),'spec_revision':plan.get('spec_revision'),'perception':plan.get('expert_perception','simulator_ground_truth'),'hardware_commands_sent':0})
    try:
        with log_path.open('wb') as log,log_path.open('r',errors='replace') as reader:
            process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);started=time.monotonic();last=started
            def forward():
                for line in reader:
                    if line.startswith('EXPERT_EVENT '):
                        try:
                            record=json.loads(line[len('EXPERT_EVENT '):]);ctx['emit'](record.get('type','expert.progress'),record.get('payload',record))
                        except (ValueError,KeyError):pass
                    elif line.startswith(('PHASE ','DEX_SEGMENT ','DEX_GRASPS ','COOPERATIVE_')):ctx['emit']('expert.phase',{'message':line.strip()})
            while process.poll() is None:
                ctx['check_cancel']()
                if time.monotonic()-started>1800:raise TimeoutError('离线专家超过30分钟上限')
                forward()
                if time.monotonic()-last>=5:ctx['emit']('expert.heartbeat',{'pid':process.pid,'elapsed_s':round(time.monotonic()-started,1),'log':str(log_path.relative_to(root))});last=time.monotonic()
                time.sleep(.2)
            forward()
        path=trial/'expert_result.json'
        if not path.is_file():path=trial/'expert_report.json'
        if not path.is_file():return {'status':'failed','error':'专家退出但没有结果报告；检查真实Isaac日志','failure_category':'runtime_environment','failure_stage':'simulation_test','evidence_paths':[str(log_path.relative_to(root))]}
        result=json.loads(path.read_text())
        trace=result.get('trajectory_path')
        if not trace or not (root/trace).is_file():
            return dict(result,status='failed',error=result.get('error') or result.get('report',{}).get('error') or '执行未产生实际轨迹',report_path=str(path.relative_to(root)),plan_path=reference,task_spec_hash=plan.get('task_spec_hash'),spec_revision=plan.get('spec_revision'),failure_category='runtime_environment',failure_stage='simulation_test',evidence_paths=[str(path.relative_to(root)),str(log_path.relative_to(root))])
        # A completed tool may produce a failed action. Always let the independent
        # evaluator inspect that evidence; never convert task failure into success.
        result.update(status='succeeded',execution_process_returncode=process.returncode,execution_completed=True,report_path=str(path.relative_to(root)),plan_path=reference,task_spec_hash=plan.get('task_spec_hash'),spec_revision=plan.get('spec_revision'),expert_perception=plan.get('expert_perception','simulator_ground_truth'),hardware_verified=False,hardware_commands_sent=0,scope='offline_expert_execution_evidence_pending_independent_evaluation')
        ctx['emit']('expert.execution_finished',{'action_passed':result.get('action_passed'),'retention_passed':result.get('retention_passed'),'full_task_passed':result.get('full_task_passed'),'independent_validation_pending':True})
        return result
    finally:
        if process and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=5)
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
