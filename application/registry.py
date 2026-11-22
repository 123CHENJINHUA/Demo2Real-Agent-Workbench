"""Only this composition root imports concrete task modules; no hardware side effects."""
import importlib
from pathlib import Path
import time
from contracts.analysis_agents import DISPATCH_STAGE, SPECIALISTS, SYNTHESIS_STAGE

REGISTRY={
 'demo_import':('modules.demo_import','run'),
 'data_audit':('modules.demo_import','audit'),
 'experience':('modules.experience','run'),
 'geometry':('application.asset_review','run'),
 SYNTHESIS_STAGE:('application.analysis_agents','synthesize'),
 DISPATCH_STAGE:('application.analysis_agents','dispatch'),
 **{role:('application.analysis_agents','analyze_specialty') for role in SPECIALISTS},
 'task_spec':('modules.task_specs','run'),
 'scene_layout':('application.scene_layout','prepare'),
 'functional_geometry':('modules.geometry.functional','run_functional'),
 'scene_build':('application.scene_cycle','run'),
 'scene_validation':('application.scene_review','run'),
 'simulation':('modules.simulation','run'),
 'placement_variant':('modules.experiments.placements','create_variant'),
 'expert_plan':('modules.planning.expert_binding','create_plan'),
 'expert_execution':('modules.motion_expert','run'),
 'phase_validation':('modules.evaluation.phase_recovery','run'),
 'expert_validation':('modules.expert_validation','run'),
 'learning':('modules.learning','run'),
 'recovery_dataset':('modules.learning.recovery_dataset','run'),
 'recovery_learning':('modules.learning.recovery_bc','run'),
 'recovery_evaluation':('modules.evaluation.recovery','run'),
 'recovery_deployment':('modules.deployment.recovery','run'),
 'evaluation':('modules.evaluation','run'),
 'deployment':('modules.deployment','run'),
}


def _invoke(entry,ctx):
    try:
        result=entry(ctx)
        if not isinstance(result,dict):
            raise ValueError('模块返回必须为dict')
    except InterruptedError:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {'status':'failed','error':str(exc),'exception':type(exc).__name__}
    status=result.get('status')
    if status in ('completed','ok','passed',None):
        status='succeeded'
    if status in ('needs_multimodal_analysis','waiting_input'):
        status='needs_configuration'
    if status not in ('succeeded','failed','blocked','needs_configuration','cancelled'):
        status='blocked'
    return result | {'status':status}


def execute_stage(stage,ctx):
    if stage not in REGISTRY:raise ValueError('未注册的工作流节点')
    ctx['check_cancel']()
    if ctx.get('input',{}).get('implementation_lock_path'):
        from modules.experiments.locks import verify_lock
        verify_lock(ctx['root'],ctx['input']['implementation_lock_path'],ctx['input'].get('implementation_lock_sha256'))
    dependencies=ctx.get('dependencies',{})
    resolved=(dependencies.get('scene_validation') or {}).get('resolved_scene_build')
    if resolved:
        previous=dependencies.get('scene_build') or {}
        dependencies=dict(dependencies)|{'scene_build':resolved | {'refinement_rounds':previous.get('refinement_rounds',[])}}
        ctx['dependencies']=dependencies
    active=ctx.get('active_scene') or {}
    current=dependencies.get('scene_build') or {}
    published=active.get('build') or {}
    source_hash=current.get('source_task_spec_hash')
    if (stage=='simulation' and active.get('stage')=='simulation' and source_hash
            and published.get('source_task_spec_hash')==source_hash and published.get('manifest_path')):
        dependencies=dependencies | {'scene_build':published}
        ctx['dependencies']=dependencies
    imported=dependencies.get('demo_import',{})
    ctx['input']=dict(ctx['input'])|{'import_result':imported}
    variant=dependencies.get('placement_variant') or {}
    if variant.get('spec_path'):
        ctx['input'].update(spec_path=variant['spec_path'],spec_hash=variant['spec_hash'],spec_revision=variant['spec_revision'])
    if stage=='expert_plan' and dependencies.get('scene_build',{}).get('manifest_path'):
        ctx['input']['scene_manifest_path']=dependencies['scene_build']['manifest_path']
    if stage=='functional_geometry':
        from modules.task_specs import load_snapshot
        layout=dependencies.get('scene_layout')
        pinned=layout or dependencies.get('task_spec') or {}
        spec_path=pinned.get('spec_path') or ctx['input'].get('spec_path')
        if spec_path:
            spec=load_snapshot(ctx['root'],spec_path,pinned.get('spec_hash') or ctx['input'].get('spec_hash'))
            ctx['input']['assets']=spec['objects']
            ctx['input']['spec_hash']=spec['content_hash'];ctx['input']['spec_revision']=spec['revision']
            ctx['input']['preview_proxy_ids']=spec.get('scene_approximation',{}).get('proxy_objects',[])
            if layout:ctx['dependencies']=dependencies|{'task_spec':layout}
            profiles=sorted(set(obj.get('collision',{}).get('profile_path') for obj in spec['objects'] if obj.get('collision',{}).get('profile_path')))
            if len(profiles)==1:ctx['input']['physics_profile_path']=profiles[0]
    if stage in ('recovery_learning','recovery_evaluation'):
        import os
        for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
    module,function=REGISTRY[stage]
    if stage=='simulation' and ctx['input'].get('placement_expert_run') is True:
        module,function='application.placement_expert','run'
    elif stage=='simulation' and dependencies.get('task_spec'):
        module,function='application.action_expert','run'
    if stage=='functional_geometry' and ctx['input'].get('preview_proxy_ids'):
        module,function='modules.geometry.preview','run'
    ctx=ctx | {'stage':stage}
    entry=None
    plugin=None
    try:
        plugin=importlib.import_module(module)
        entry=getattr(plugin,function)
    except (ModuleNotFoundError,AttributeError):
        # A capability is absent, rather than silently replaced with pretend success.
        result={'status':'needs_configuration','reason':f'尚无可调用插件 {module}:{function}。','capability':stage}
    else:
        result=_invoke(entry,ctx)
    if result['status'] in ('failed','blocked','needs_configuration'):
        from application.supervision import supervise

        def retry_stage(working,failure,decision):
            out=Path(working['output_dir'])
            if module!='application.action_expert':
                out=out/('supervisor_retry_'+str(time.time_ns()))
                out.mkdir(parents=True)
            retried=_invoke(entry,working | {'output_dir':out,'recovery_feedback':{
                'instruction':decision['instruction'],'diagnosis':decision['diagnosis'],
                'error':failure.get('error') or failure.get('reason')}})
            return retried,working

        result=supervise(stage,ctx,result,retry_stage if entry is not None else None,
                         getattr(plugin,'recovery_actions',None))
    # A completed action may still fail a separate physical validation. Preserve it.
    issue_records=[]
    for issue in result.get('issues',[])[:10]:
        if not isinstance(issue,dict):continue
        try:
            from application.runtime import archive_failure
            summary=issue.get('error') or issue.get('message') or 'Partial validation failed'
            recorded=archive_failure(ctx['root'],ctx['run_id'],ctx.get('task_id',stage),summary,str((ctx['output_dir']/'worker.log').relative_to(ctx['root'])),issue)
            issue_records.append({'incident_id':recorded['incident_id'],'record_path':recorded['record_path'],'category':recorded['primary_category']})
            ctx['emit']('experience.problem_archived',issue_records[-1])
        except Exception as exc:ctx['emit']('experience.archive_failed',{'error':str(exc)})
    if issue_records:result['issue_records']=issue_records
    ctx['check_cancel']()
    artifacts=[]
    for path in ctx['output_dir'].rglob('*'):
        if path.is_file() and path.name not in ('worker.log','result.json','result.json.tmp') and not path.is_symlink():
            artifacts.append({'path':str(path.relative_to(ctx['root'])),'name':path.name})
    result['artifacts']=artifacts
    return result
