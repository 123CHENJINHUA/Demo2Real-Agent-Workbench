"""Local HTTP adapter. Algorithms and concrete plugins live outside this module."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio, importlib, json, mimetypes, os, secrets, time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.store import Store
from orchestration.scheduler import Scheduler
from orchestration.workflow import STAGES
from orchestration.providers.compatible import Provider
from application.runtime import worker_environment, archive_failure, worker_interpreter, codex_login_manager
from application.incidents import merge_incidents
from contracts.tasks import SpecConflict, SpecPrecondition

def create_app(root=None, start_scheduler=True):
    root=Path(root or Path(__file__).resolve().parents[1]).resolve()
    runtime=root/'.runtime';runtime.mkdir(exist_ok=True)
    for directory in ('tmp','cache','config','data','secrets'):(runtime/directory).mkdir(exist_ok=True)
    token_path=runtime/'secrets/session_token'
    if not token_path.exists():
        fd=os.open(token_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(40))
    token=token_path.read_text().strip()
    store=Store(runtime/'workbench.sqlite3');scheduler=Scheduler(root,store,environment_builder=worker_environment,incident_reporter=archive_failure,interpreter_selector=worker_interpreter);provider=Provider(root);codex_login=codex_login_manager(root)
    @asynccontextmanager
    async def lifespan(app):
        if start_scheduler:scheduler.start()
        yield
        if start_scheduler:scheduler.stop()
        codex_login.close()
    app=FastAPI(title='Demo2Real Agent Workbench',lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])
    app.state.store=store;app.state.scheduler=scheduler;app.state.root=root
    @app.middleware('http')
    async def local_session(request,call_next):
        if request.url.path.startswith('/api/'):
            origin=request.headers.get('origin')
            if origin:
                from urllib.parse import urlparse
                parsed=urlparse(origin)
                if parsed.hostname not in ('localhost','127.0.0.1','::1','testserver') or parsed.scheme not in ('http','https'):
                    return JSONResponse({'detail':'只接受本机前端来源'},status_code=403)
            if request.method not in ('GET','HEAD','OPTIONS'):
                if not secrets.compare_digest(request.headers.get('X-Session-Token',''),token):
                    return JSONResponse({'detail':'会话token缺失或无效'},status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        return response
    def require_run(rid):
        run=store.get_run(rid)
        if run is None:raise HTTPException(404,'任务不存在')
        return run
    def call_service(module,function,*args,**kwargs):
        try:entry=getattr(importlib.import_module(module),function)
        except (ModuleNotFoundError,AttributeError):raise HTTPException(503,f'能力待配置：{module}:{function}')
        return entry(*args,**kwargs)
    def allowed_roots():
        roots=[root,Path.home()/'Downloads']
        import yaml
        path=root/'config/import_roots.yaml'
        if path.exists():
            data=yaml.safe_load(path.read_text()) or {}
            roots.extend(Path(p).expanduser().resolve() for p in data.get('allowed_roots',[]))
        return list(dict.fromkeys(p.resolve() for p in roots if p.is_dir()))
    def check_source(value):
        path=Path(value).expanduser()
        if not path.is_absolute():raise HTTPException(400,'请选择绝对目录路径')
        path=path.resolve()
        if not any(path.is_relative_to(base) for base in allowed_roots()):raise HTTPException(403,'目录不在已允许导入范围；请在config/import_roots.yaml登记')
        base=max((base for base in allowed_roots() if path.is_relative_to(base)),key=lambda p:len(p.parts))
        if any(part.startswith('.') for part in path.relative_to(base).parts):raise HTTPException(403,'不允许导入隐藏配置目录')
        if not path.is_dir():raise HTTPException(400,'示教目录不存在')
        return path
    async def task_request(request,limit=65536):
        body=await request.body()
        if len(body)>limit:raise HTTPException(413,f'任务配置JSON超过{limit//1024}KiB；数组和数据请保存为项目内工件')
        try:data=json.loads(body)
        except (ValueError,UnicodeDecodeError):raise HTTPException(400,'请求不是合法JSON')
        if not isinstance(data,dict):raise HTTPException(400,'请求必须是对象')
        for field in ('learning','evaluation','deployment'):
            if field in data and not isinstance(data[field],dict):raise HTTPException(400,field+'必须是对象')
        return data
    @app.get('/api/session')
    def session():return {'token':token}
    @app.get('/api/health')
    def health():return {'status':'ok','project_root':str(root),'hardware_commands_enabled':False,'version':'0.1.0'}
    @app.get('/api/runs')
    def runs():return {'runs':store.query('SELECT * FROM runs ORDER BY created_at DESC')}
    @app.get('/api/overview')
    def overview():
        runs=store.query('SELECT * FROM runs ORDER BY created_at DESC');agents=store.query('SELECT * FROM agents ORDER BY rowid DESC')
        return {'runs':runs,'agents':agents,'counts':{'runs':len(runs),'agents':len(agents),'running':sum(a['status']=='running' for a in agents),'blocked':sum(a['status'] in ('blocked','needs_configuration') for a in agents)},'recent_events':store.query('SELECT * FROM events ORDER BY id DESC LIMIT 35')}
    @app.post('/api/runs')
    async def create_run(request:Request):
        data=await task_request(request)
        if not isinstance(data,dict):raise HTTPException(400,'请求必须是对象')
        source=check_source(str(data.get('source_path','')))
        payload={k:data[k] for k in ('source_path','goal','query','asset_paths','episode','episode_index','max_total_bytes','max_files','task_plugin','simulation_mode','headless','profile_id','learning','evaluation','deployment') if k in data}
        payload['source_path']=str(source)
        return store.create_run(str(data.get('name') or source.name)[:160],payload,STAGES)
    @app.post('/api/diagnostics')
    async def diagnostics(request:Request):
        data=await task_request(request)
        modes={'connector_bench':'keyed_bench','connector_physics':'physics','connector_rgbd':'rgbd'}
        kind=data.get('kind')
        if kind=='supervisor_bc':
            config=data.get('learning',{})
            if config.get('dataset_path'):
                value=Path(str(config['dataset_path']))
                path=(root/value).resolve()
                if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower()!='.npz':raise HTTPException(400,'学习数据必须是项目内真实NPZ工件')
                from contracts.policies import dependency_signature
                try:dependency_signature(config.get('versions',{}))
                except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
                config=dict(config)|{'dataset_path':str(path.relative_to(root))}
            payload={'learning':config,'diagnostic_only':True,'scope':'supervised_skill_classification_only','hardware_verified':False,'task_success_inherited':False}
            stages=[{'id':'learning','role':'监督技能分类离线训练','kind':'tool','deps':[]}]
            return store.create_run('Supervisor BC 离线技能分类诊断',payload,stages)
        if kind not in modes:raise HTTPException(400,'未注册的离线诊断')
        payload={'source_path':str(root/'data/reference/connector/source_demo'),'task_plugin':'connector','simulation_mode':modes[kind],'headless':True,'diagnostic_only':True,'scope':'已迁移Connector回归/局部机构诊断；不等于新任务泛化或训练验收'}
        numeric={'seed':(0,2147483647,712),'spread_m':(0,.05,.025),'yaw_spread_deg':(0,30,8),'pose_noise_m':(0,.005,0),'pose_delay_s':(0,.5,0)}
        import math
        for key,(low,high,default) in numeric.items():
            value=data.get(key,default)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:raise HTTPException(400,f'{key}超出允许范围[{low},{high}]')
            if key=='seed' and not isinstance(value,int):raise HTTPException(400,'seed必须整数')
            target={'spread_m':'spread','yaw_spread_deg':'yaw_spread','pose_noise_m':'pose_noise','pose_delay_s':'pose_delay'}.get(key,key)
            payload[target]=value
        disturbance=data.get('disturbance','none')
        if disturbance not in ('none','push','drop','relocate','occlude','remove'):raise HTTPException(400,'未注册的扰动')
        payload['disturbance']=disturbance
        stages=[{'id':'simulation','role':'Connector局部机构诊断','kind':'tool','deps':[]}]
        return store.create_run('Connector 离线诊断 / '+modes[kind],payload,stages)
    @app.get('/api/runs/{rid}')
    def run_detail(rid:str):return require_run(rid)
    def spec_call(function,*args,**kwargs):
        try:return call_service('modules.task_specs',function,root,*args,**kwargs)
        except FileNotFoundError as exc:raise HTTPException(404,str(exc))
        except SpecConflict as exc:raise HTTPException(409,str(exc))
        except SpecPrecondition as exc:raise HTTPException(428,str(exc))
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc))
    def spec_response(rid,result,register=False):
        if register:
            for record in result.get('artifacts',[]):store.add_artifact(rid,None,record)
        builds=[];spec=result['spec']
        for run in store.query('SELECT * FROM runs ORDER BY created_at DESC'):
            if run['input'].get('source_run_id')!=rid:continue
            inputs=run['input'];records=store.query("SELECT result FROM tasks WHERE run_id=? AND stage='scene_build'",(run['id'],))
            actual=(records[0].get('result') or {}) if records else {}
            built_hash=actual.get('task_spec_hash') or inputs.get('spec_hash');built_revision=actual.get('spec_revision') or inputs.get('spec_revision')
            builds.append({'run_id':run['id'],'status':run['status'],'spec_hash':built_hash,'spec_revision':built_revision,'requested_spec_hash':inputs.get('spec_hash'),'stale':built_hash!=spec['content_hash'] or built_revision!=spec['revision']})
        result=dict(result)|{'scene_builds':builds}
        return JSONResponse(result,headers={'ETag':result['etag']})
    @app.get('/api/task-profiles')
    def task_profiles():return {'profiles':spec_call('profiles')}
    @app.get('/api/runs/{rid}/task-spec')
    def task_spec_get(rid:str):require_run(rid);return spec_response(rid,spec_call('get',rid))
    @app.post('/api/runs/{rid}/task-spec')
    async def task_spec_generate(rid:str,request:Request):
        run=require_run(rid);data=await task_request(request)
        dependencies={t['stage']:t['result'] for t in run['tasks'] if t['result']}
        result=spec_call('generate',rid,inputs=run['input'],dependencies=dependencies,profile_id=data.get('profile_id'),expected_revision=data.get('expected_revision'),etag=request.headers.get('if-match'))
        store.event(rid,None,'task_spec.generated',{'revision':result['spec']['revision'],'content_hash':result['spec']['content_hash']})
        return spec_response(rid,result,True)
    @app.put('/api/runs/{rid}/task-spec')
    async def task_spec_save(rid:str,request:Request):
        require_run(rid);data=await task_request(request,512*1024)
        if not isinstance(data.get('spec'),dict):raise HTTPException(400,'需要完整spec对象')
        result=spec_call('save',rid,data['spec'],expected_revision=data.get('expected_revision'),etag=request.headers.get('if-match'))
        store.event(rid,None,'task_spec.edited',{'revision':result['spec']['revision'],'content_hash':result['spec']['content_hash'],'prior_validation_invalidated':True})
        return spec_response(rid,result,True)
    @app.post('/api/runs/{rid}/task-spec/freeze')
    async def task_spec_freeze(rid:str,request:Request):
        require_run(rid);data=await task_request(request)
        result=spec_call('freeze',rid,expected_revision=data.get('expected_revision'),etag=request.headers.get('if-match'),scope=data.get('scope'))
        store.event(rid,None,'task_spec.frozen',{'revision':result['spec']['revision'],'content_hash':result['spec']['content_hash'],'scope':'offline_contract_only','contact_execution_authorized':False})
        return spec_response(rid,result,True)
    @app.post('/api/runs/{rid}/build-scene')
    async def task_scene_build(rid:str,request:Request):
        require_run(rid);data=await task_request(request)
        if data.get('preview',True) is not True:raise HTTPException(400,'当前只支持离线场景构建/预览，不接受接触执行请求')
        pinned=spec_call('pin',rid,expected_revision=data.get('expected_revision'),etag=request.headers.get('if-match'))
        spec=pinned['spec'];payload={'source_run_id':rid,'spec_path':pinned['spec_path'],'spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'profile_id':spec['profile_id'],'preview':True,'contact_execution_authorized':False,'query':spec['profile_id'] or '', 'features':['functional_geometry','scene_build','unknown_frames']}
        stages=[{'id':'experience','role':'构建前经验检索','kind':'tool','deps':[]},{'id':'functional_geometry','role':'功能几何证据审查','kind':'tool','deps':['experience']},{'id':'scene_build','role':'离线场景构建与预览','kind':'tool','deps':['functional_geometry']},{'id':'scene_validation','role':'场景结构与局部物理检查','kind':'tool','deps':['scene_build']}]
        job=store.create_run('场景预览 / '+rid,payload,stages)
        store.event(rid,None,'scene.build_requested',{'scene_run_id':job['id'],'spec_hash':spec['content_hash'],'spec_revision':spec['revision']})
        return job
    @app.post('/api/runs/{rid}/run-expert')
    async def task_expert_run(rid:str,request:Request):
        scene_run=require_run(rid);data=await task_request(request)
        if set(data)-{'expected_revision','spec_hash'}:raise HTTPException(400,'只接受绑定版本；当前仅固定场景离线真值专家，不接受任意命令/模型/硬件参数')
        if data.get('expected_revision') is None or not data.get('spec_hash'):raise HTTPException(428,'需要expected_revision和spec_hash')
        if scene_run['status']!='succeeded':raise HTTPException(409,'先完成当前场景构建与独立验证')
        source=scene_run['input'].get('source_run_id')
        if not source:raise HTTPException(400,'该任务不是绑定源定义的场景运行')
        pinned=spec_call('pin',source,expected_revision=data['expected_revision']);spec=pinned['spec']
        if spec['content_hash']!=data['spec_hash']:raise HTTPException(409,'任务定义已改变，请刷新并重新构建场景')
        if spec.get('profile_id')!='connector_keyed_v1':raise HTTPException(409,'当前离线专家仅支持已核验的Connector插件；其他任务需要对应运动专家')
        build=next((t['result'] for t in scene_run['tasks'] if t['stage']=='scene_build' and t['status']=='succeeded'),None)
        validation=next((t['result'] for t in scene_run['tasks'] if t['stage']=='scene_validation' and t['status']=='succeeded'),None)
        if not build or not validation:raise HTTPException(409,'缺少成功场景构建和独立局部检查')
        if build.get('task_spec_hash')!=spec['content_hash'] or build.get('spec_revision')!=spec['revision']:raise HTTPException(409,'场景绑定旧任务定义，请为当前版本重新构建')
        if validation.get('task_spec_hash')!=spec['content_hash'] or validation.get('spec_revision')!=spec['revision']:raise HTTPException(409,'场景验证与当前定义不匹配')
        payload={'expert_run':True,'source_run_id':source,'scene_run_id':rid,'spec_path':pinned['spec_path'],'spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'profile_id':spec['profile_id'],'scene_manifest_path':build['manifest_path'],'manifest_path':build['manifest_path'],'demo_path':'data/reference/connector/converted/episode_000.npz','expert_perception':'simulator_ground_truth','scope':'offline_contact_expert_only','hardware_commands_sent':0,'query':'connector keyed insertion friction grasp direct release','features':['bimanual','friction_grasp','keyed_insertion','post_release_impact']}
        stages=[{'id':'experience','role':'动作前经验检索','kind':'tool','deps':[]},{'id':'scene_validation','role':'执行前场景与依赖复审','kind':'tool','deps':['experience']},{'id':'expert_plan','role':'绑定示教与运动专家','kind':'tool','deps':['scene_validation']},{'id':'expert_execution','role':'离线真值专家执行','kind':'tool','deps':['expert_plan']},{'id':'expert_validation','role':'独立动作与保持验收','kind':'tool','deps':['expert_execution']}]
        job=store.create_run('离线示教专家 / '+source,payload,stages);store.event(rid,None,'expert.requested',{'expert_run_id':job['id'],'spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'hardware_commands_sent':0});return job
    @app.post('/api/experiments/connector-placement')
    async def placement_suite(request:Request):
        data=await task_request(request)
        if 'suite_id' not in data or set(data)-{'suite_id','case_ids'}:raise HTTPException(400,'需要注册suite_id，可选case_ids子集，不接受任意参数或代码')
        try:return call_service('application.placement_runs','create_suite',root,store,data['suite_id'],case_ids=data.get('case_ids'))
        except (ValueError,KeyError,OSError) as exc:raise HTTPException(400,str(exc))
    @app.post('/api/learning/recovery')
    async def recovery_learning_job(request:Request):
        data=await task_request(request)
        batches=data.get('collection_batches');epochs=data.get('epochs',500)
        if set(data)-{'collection_batches','epochs'} or not isinstance(batches,list) or not batches or len(batches)>6 or not all(isinstance(b,str) and b.startswith('placement_') and b.replace('_','').isalnum() for b in batches):raise HTTPException(400,'需要项目内实际采集批次列表')
        if isinstance(epochs,bool) or not isinstance(epochs,int) or not 1<=epochs<=1000:raise HTTPException(400,'epochs需在1到1000内')
        stages=[{'id':'recovery_dataset','role':'真实恢复数据审计与按回合分割','deps':[]},{'id':'recovery_learning','role':'24维状态恢复MLP训练','deps':['recovery_dataset']},{'id':'recovery_evaluation','role':'独立回合分类测试','deps':['recovery_learning']},{'id':'recovery_deployment','role':'仿真专用学生策略包','deps':['recovery_evaluation']}]
        return store.create_run('状态恢复学生 / 真实PhysX数据',{'collection_batches':batches,'epochs':epochs,'recovery_learning':True,'observation_source':'simulated_pose_estimate'},stages)
    @app.post('/api/runs/{rid}/cancel')
    def cancel(rid:str):require_run(rid);scheduler.cancel(rid);return require_run(rid)
    @app.post('/api/runs/{rid}/retry')
    def retry(rid:str):
        require_run(rid)
        try:scheduler.retry(rid)
        except ValueError as exc:raise HTTPException(409,str(exc))
        return require_run(rid)
    @app.get('/api/runs/{rid}/events')
    def events(rid:str,after:int=0):
        require_run(rid);items=store.query('SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 500',(rid,after))
        return {'events':items,'cursor':items[-1]['id'] if items else after}
    @app.get('/api/fs')
    def browser(path:str=''):
        bases=allowed_roots();target=check_source(path or str(Path.home()/'Downloads'))
        entries=[]
        for child in sorted(target.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower())):
            if child.name.startswith('.') or child.is_symlink():continue
            try:entries.append({'name':child.name,'path':str(child),'is_dir':child.is_dir(),'size':None if child.is_dir() else child.stat().st_size})
            except OSError:continue
        parent=str(target.parent) if any(target.parent.is_relative_to(base) for base in bases) else None
        return {'path':str(target),'parent':parent,'entries':entries[:2000],'allowed_roots':[str(p) for p in bases]}
    @app.get('/api/settings/environments')
    def environments():return {'config':call_service('modules.environment.service','load',root),'validation':call_service('modules.environment.service','validate',root)}
    @app.put('/api/settings/environments')
    async def save_environments(request:Request):
        data=await request.json()
        try:call_service('modules.environment.service','save',root,data.get('config',data))
        except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
        return environments()
    @app.get('/api/settings/provider')
    def get_provider():return provider.public()
    @app.put('/api/settings/provider')
    async def save_provider(request:Request):
        try:return provider.save(await request.json())
        except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
    @app.post('/api/settings/provider/test')
    def test_provider():
        from orchestration.providers.codex_cli import TEST_SCHEMA
        return provider.request([{'role':'user','content':'Return only the JSON object {"ok":true}.'}],output_dir=root/'.runtime/provider-tests',schema=TEST_SCHEMA,emit=lambda kind,payload:store.event(None,None,kind,payload))
    @app.get('/api/settings/provider/codex/status')
    def codex_status():return provider.codex_status()
    @app.get('/api/settings/provider/codex/login')
    def codex_login_status():return codex_login.status()
    @app.post('/api/settings/provider/codex/login/start')
    def codex_login_start():return codex_login.start()
    @app.post('/api/settings/provider/codex/login/cancel')
    def codex_login_cancel():return codex_login.cancel()
    @app.get('/api/experience')
    def experience(query:str='',category:str|None=None,stage:str|None=None):return {'cases':call_service('modules.experience','search',root,query=query,category=category,stage=stage)}
    @app.get('/api/incidents')
    def incidents():
        latest=call_service('modules.experience','list_incidents',root)
        return {'incidents':merge_incidents(store.query('SELECT * FROM incidents ORDER BY created_at DESC'),latest)}
    @app.post('/api/incidents')
    async def incident(request:Request):
        data=await request.json()
        if not data.get('summary'):raise HTTPException(400,'需要summary')
        payload=dict(data);payload.setdefault('evidence_scope','unverified_user_report')
        payload.setdefault('observed_behavior',data['summary'])
        payload.setdefault('primary_category',data.get('category','workflow_integrity'))
        try:result=call_service('modules.experience','record_incident',root,payload)
        except ValueError as exc:raise HTTPException(400,str(exc))
        iid=store.incident(data.get('run_id'),data.get('task_id'),payload.get('primary_category','workflow_integrity'),str(data['summary']),result)
        return {'id':iid,'record':result}
    @app.get('/api/robots/status')
    def robot_status():return call_service('modules.robot_backends.service','status',root)
    @app.post('/api/robots/shadow')
    async def robot_shadow(request:Request):
        try:return call_service('modules.robot_backends.service','shadow',root,await request.json())
        except (ValueError,RuntimeError) as exc:raise HTTPException(400,str(exc))
    @app.post('/api/robots/{action}')
    def robot_action(action:str):raise HTTPException(403,'尚未完成本批次真机条件核验和显式授权；未连接设备或发送硬件命令。')
    @app.get('/api/policies')
    def policies():return {'policies':call_service('modules.policies.catalog','list_policies',root)}
    @app.get('/api/policies/active')
    def active_policies():return call_service('modules.policies.service','get_active',root)
    @app.put('/api/policies/active')
    async def set_active_policies(request:Request):
        data=await task_request(request)
        if set(data)-{'role','plugin_id','checkpoint','dependency_signature'}:raise HTTPException(400,'未知策略选择字段')
        try:return call_service('modules.policies.service','set_active',root,data)
        except (ValueError,TypeError,FileNotFoundError,RuntimeError) as exc:raise HTTPException(400,str(exc))
    @app.get('/api/deployments')
    def deployment_packages():return call_service('modules.policies.service','list_packages',root)
    @app.get('/api/artifacts/{aid}')
    def artifact(aid:str):
        records=store.query('SELECT * FROM artifacts WHERE id=?',(aid,))
        if not records:raise HTTPException(404,'工件不存在')
        path=(root/records[0]['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise HTTPException(404,'工件不可用')
        return FileResponse(path,media_type=records[0]['media_type'],filename=None)
    @app.get('/{path:path}')
    def spa(path:str):
        if path.startswith('api/'):raise HTTPException(404,'API不存在')
        dist=root/'frontend/dist';candidate=(dist/path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():return FileResponse(candidate)
        if (dist/'index.html').is_file():return FileResponse(dist/'index.html')
        return JSONResponse({'status':'frontend_not_built','detail':'请构建frontend；后端API已就绪。'})
    return app
