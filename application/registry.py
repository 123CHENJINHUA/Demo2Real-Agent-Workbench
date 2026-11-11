"""Only this composition root imports concrete task modules; no hardware side effects."""
import importlib, json, mimetypes, base64
from pathlib import Path
from orchestration.providers.compatible import Provider

REGISTRY={
 'demo_import':('modules.demo_import','run'),
 'data_audit':('modules.demo_import','audit'),
 'experience':('modules.experience','run'),
 'geometry':('modules.geometry','run'),
 'task_analysis':('modules.task_analysis','run'),
 'task_spec':('modules.task_specs','run'),
 'functional_geometry':('modules.geometry.functional','run_functional'),
 'scene_build':('modules.scene_building','run'),
 'scene_validation':('modules.scene_validation','run'),
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

def task_output_schema(evidence_refs):
    strings={'type':'array','items':{'type':'string'}}
    nullable_time={'anyOf':[{'type':'number'},{'type':'null'}]}
    phase_properties={'name':{'type':'string'},'evidence_refs':{'type':'array','items':{'type':'string','enum':list(evidence_refs)}},'start_time_s':nullable_time,'end_time_s':nullable_time}
    object_properties={'name':{'type':'string'},'role':{'type':'string'},'visual_evidence':strings,'uncertainties':strings}
    properties={
        'task_summary':{'type':'string'},
        'objects':{'type':'array','items':{'type':'object','properties':object_properties,'required':list(object_properties),'additionalProperties':False}},
        'phases':{'type':'array','maxItems':100,'items':{'type':'object','properties':phase_properties,'required':list(phase_properties),'additionalProperties':False}},
        'success_conditions':strings,'unknowns':strings,'required_assets':strings,'confidence':{'type':'number','minimum':0,'maximum':1},
    }
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def selected_frame_evidence(dependencies, refs):
    audit=dependencies.get('data_audit') or {}
    frames={item.get('path'):item for item in audit.get('sample_frames',[]) if isinstance(item,dict)}
    videos={item.get('path'):item for item in audit.get('videos',[]) if isinstance(item,dict)}
    selected=[]
    for reference in refs:
        source=frames.get(reference,{})
        video=source.get('source_video');entry={'path':reference,'source_video':video,'requested_timestamp_seconds':source.get('requested_timestamp_seconds'),'timestamp_precision':source.get('timestamp_precision','unverified')}
        if video:
            pieces=video.split('/')
            entry['camera']=pieces[1] if len(pieces)>1 else video
            interval=videos.get(video,{}).get('sampled_interval_seconds')
            timestamp=source.get('requested_timestamp_seconds')
            if isinstance(interval,list) and len(interval)==2:
                entry['sampled_interval_seconds']=interval
                if isinstance(timestamp,(int,float)) and isinstance(interval[0],(int,float)):entry['episode_relative_seconds']=timestamp-interval[0]
        selected.append(entry)
    return selected


def execute_stage(stage,ctx):
    if stage not in REGISTRY:raise ValueError('未注册的工作流节点')
    ctx['check_cancel']()
    if ctx.get('input',{}).get('implementation_lock_path'):
        from modules.experiments.locks import verify_lock
        verify_lock(ctx['root'],ctx['input']['implementation_lock_path'],ctx['input'].get('implementation_lock_sha256'))
    dependencies=ctx.get('dependencies',{})
    if stage=='simulation' and dependencies.get('task_spec'):
        return {'status':'needs_configuration','reason':'当前task spec仅完成离线场景定义/预览；尚无绑定本spec并独立验证的接触专家，不自动执行示教或授予硬件权限。','contact_execution_authorized':False}
    imported=dependencies.get('demo_import',{})
    ctx['input']=dict(ctx['input'])|{'import_result':imported}
    variant=dependencies.get('placement_variant') or {}
    if variant.get('spec_path'):
        ctx['input'].update(spec_path=variant['spec_path'],spec_hash=variant['spec_hash'],spec_revision=variant['spec_revision'])
    if stage=='expert_plan' and dependencies.get('scene_build',{}).get('manifest_path'):
        ctx['input']['scene_manifest_path']=dependencies['scene_build']['manifest_path']
    if stage=='functional_geometry':
        from modules.task_specs import load_snapshot
        pinned=dependencies.get('task_spec') or {}
        spec_path=pinned.get('spec_path') or ctx['input'].get('spec_path')
        if spec_path:
            spec=load_snapshot(ctx['root'],spec_path,pinned.get('spec_hash') or ctx['input'].get('spec_hash'))
            ctx['input']['assets']=spec['objects']
            ctx['input']['spec_hash']=spec['content_hash'];ctx['input']['spec_revision']=spec['revision']
            profiles=sorted(set(obj.get('collision',{}).get('profile_path') for obj in spec['objects'] if obj.get('collision',{}).get('profile_path')))
            if len(profiles)==1:ctx['input']['physics_profile_path']=profiles[0]
    if stage=='task_analysis':
        provider=Provider(ctx['root']);provider_settings=provider.public()
        evidence_dir=Path(ctx['root'])/'runs'/ctx['run_id']
        frames=sorted((evidence_dir/'data_audit').glob('**/frame_*.jpg'))
        streams={}
        for path in frames:streams.setdefault(path.parent,[]).append(path)
        candidates=[]
        for paths in streams.values():
            indices=sorted(set(round(i*(len(paths)-1)/3) for i in range(4)))
            candidates.extend(paths[i] for i in indices)
        candidates=candidates[:12]
        if not candidates:candidates=sorted((evidence_dir/'data_audit').glob('**/contact_sheet.jpg'))[:3]
        candidates=[p for p in candidates if p.is_file() and p.stat().st_size<6_000_000]
        if provider_settings['configured'] and candidates:
            instruction=importlib.import_module('modules.task_analysis').MODEL_INSTRUCTION
            refs=[str(p.relative_to(ctx['root'])) for p in candidates]
            timeline=selected_frame_evidence(dependencies,refs)
            boundaries='Sensor capture times and cross-view synchronization are unverified. Image seek timestamps are requested video times, not calibrated sensor capture times. Lossy 8-bit encoded depth must not be treated as metric depth. Visual alignment or apparent insertion does not prove a hidden latch is locked or retained after release. Use null for unobserved phase times; distinguish observations from hypotheses.'
            content=[{'type':'text','text':instruction+'\nEvidence limitations: '+boundaries+'\nselected_frame_evidence: '+json.dumps(timeline,ensure_ascii=False)+'\nUser goal: '+str(ctx['input'].get('goal',''))+'\nAdditional offline evidence (possibly truncated): '+json.dumps(dependencies,ensure_ascii=False,default=str)[:22000]}]
            for path,ref in zip(candidates,refs):
                content.append({'type':'text','text':'Evidence reference: '+ref})
                content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(path.read_bytes()).decode()}})
            ctx['emit']('model.requested',{'model':provider_settings.get('model') or 'CLI default','image_count':len(candidates),'evidence_refs':refs})
            reply=provider.request([{'role':'user','content':content}],output_dir=ctx['output_dir'],images=candidates,schema=task_output_schema(refs),emit=ctx['emit'],check_cancel=ctx['check_cancel'])
            (ctx['output_dir']/'model_response.json').write_text(json.dumps(reply,ensure_ascii=False,indent=2))
            if reply['status']!='succeeded':return reply | {'artifacts':[{'path':str((ctx['output_dir']/'model_response.json').relative_to(ctx['root']))}]}
            raw=reply['content']
            if isinstance(raw,str):
                raw=raw.strip()
                if raw.startswith('```'):
                    raw='\n'.join(raw.splitlines()[1:-1])
                try:raw=json.loads(raw)
                except ValueError:return {'status':'failed','error':'模型未返回合法JSON任务提案；原始响应已保留。','artifacts':[{'path':str((ctx['output_dir']/'model_response.json').relative_to(ctx['root']))}]}
            ctx['input'].update(semantic_model_result=raw,semantic_evidence_type='rgb_sampled_multimodal_model_proposal',evidence_refs=refs)
        elif provider_settings['configured']:
            ctx['emit']('model.waiting_evidence',{'reason':'尚无真实示教采样图像；不会用metadata冒充视频分析。'})
    if stage in ('recovery_learning','recovery_evaluation'):
        import os
        for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
    module,function=REGISTRY[stage]
    try:entry=getattr(importlib.import_module(module),function)
    except (ModuleNotFoundError,AttributeError) as exc:
        # A capability is absent, rather than silently replaced with pretend success.
        return {'status':'needs_configuration','reason':f'尚无可调用插件 {module}:{function}。','capability':stage}
    result=entry(ctx)
    if not isinstance(result,dict):raise ValueError('模块返回必须为dict')
    result=dict(result)
    status=result.get('status')
    if status in ('completed','ok','passed'):result['status']='succeeded'
    elif status in ('needs_multimodal_analysis','waiting_input'):result['status']='needs_configuration'
    elif status is None:result['status']='succeeded'
    if result['status'] not in ('succeeded','failed','blocked','needs_configuration','cancelled'):result['status']='blocked'
    # A completed action may still fail a separate physical validation. Preserve it.
    issue_records=[]
    for issue in result.get('issues',[])[:10]:
        if not isinstance(issue,dict):continue
        try:
            from application.runtime import archive_failure
            recorded=archive_failure(ctx['root'],ctx['run_id'],ctx.get('task_id',stage),issue.get('error','Partial validation failed'),str((ctx['output_dir']/'worker.log').relative_to(ctx['root'])),issue)
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
