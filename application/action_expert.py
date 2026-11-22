"""Plan, validate and launch a task-specific offline action expert from a demonstration."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np

from application.agent_activity import handoff as agent_handoff
from application.analysis_agents import request_with_evidence
from application.motion_preflight import run as preflight
from application.scene_repair import repair as repair_scene
from application.scene_visual import previous_scene
from contracts.action_program import acceptance_schema, program_schema, validate_acceptance, validate_program
from modules.environment.service import local_environment, resolve
from modules.experience import run as retrieve_experience
from modules.learning.action_program import recorded_timing
from modules.motion_replay.kinematics import ToolKinematics
from modules.scene_building.service import _stop
from modules.task_specs import load_snapshot


def _save(path, document):
    path.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False))


def _criteria(spec, proposal):
    result=[]
    checks=spec.get('evaluation') or {}
    action=(checks.get('action') or {}).get('criteria') or proposal.get('success_conditions') or []
    retention=(checks.get('retention') or {}).get('criteria') or []
    for item in [*action,*retention]:
        result.append(item if isinstance(item,str) else json.dumps(item,ensure_ascii=False))
    if (checks.get('retention') or {}).get('required') is True and not retention:
        result.append('示教任务结束后，操作物体在解除夹持后保持就位。')
    observed_steps=[row['name'] for row in proposal.get('phases',[])]
    return list(dict.fromkeys(result or observed_steps or [proposal['task_summary']]))


def _run_isaac(ctx, root, out, bundle, tracking):
    sim=resolve(root,'isaac_sim_root',False)
    if not sim or not (sim/'python.sh').is_file():
        return tracking | {'status':'needs_configuration','reason':'动作计划已生成，需要配置 Isaac Sim 才能执行离线试验'}
    report=out/'expert_learning_report.json'
    path=out/'isaac_learning.log'
    command=[str(sim/'python.sh'),str(root/'workers/learn_placement_expert.py'),'--plan',str(bundle),
             '--output',str(out),'--portable-root',str(root/'.runtime/isaac-expert')]
    process=None
    with (root/'.runtime/gpu.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return tracking | {'status':'blocked','reason':'Isaac 的离线资源正被占用'}
        try:
            env=local_environment(root,{'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2'})
            with path.open('wb') as log,path.open('r',errors='replace') as reader:
                process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                ctx['emit']('expert.rollouts',{'episode_report_path':(out/'episodes.json').relative_to(root).as_posix()})
                deadline=time.monotonic()+1200
                while True:
                    for line in reader:
                        if line.startswith('PLACEMENT_EVENT '):
                            ctx['emit']('task.progress',json.loads(line.removeprefix('PLACEMENT_EVENT ')))
                    if process.poll() is not None:
                        break
                    ctx['check_cancel']()
                    if time.monotonic()>=deadline:
                        return tracking | {'status':'failed','reason':'动作专家试验超过运行时间预算'}
                    time.sleep(.2)
        finally:
            _stop(process)
    if not report.is_file():
        return tracking | {'status':'failed','reason':'Isaac 未生成动作试验报告','evidence_paths':[path.relative_to(root).as_posix()]}
    return tracking | json.loads(report.read_text()) | {'report_path':report.relative_to(root).as_posix()}


def _run_once(ctx, *, previous=None):
    root=Path(ctx['root']).resolve()
    deps=ctx['dependencies']
    offline={'hardware_verified':False,'hardware_commands_sent':0,'contact_execution_authorized':False}
    build=deps.get('scene_build') or {}
    manifest_ref=build.get('manifest_path')
    if not manifest_ref:
        return offline | {'status':'needs_configuration','reason':'动作计划需要已经复核的仿真场景'}
    manifest_path=(root/manifest_ref).resolve()
    manifest_path.relative_to(root)
    raw_manifest=manifest_path.read_bytes()
    manifest=json.loads(raw_manifest)
    spec=load_snapshot(root,manifest['spec_snapshot_path'],manifest['task_spec_hash'])
    analysis=deps.get('task_analysis') or {}
    proposal=analysis.get('proposal') or {}
    if not proposal.get('task_summary') and (ref:=spec['source'].get('proposal_path')):
        proposal=json.loads((root/ref).read_text()).get('proposal') or {}
    if not proposal.get('task_summary'):
        return offline | {'status':'needs_configuration','reason':'缺少总控对本次示教目标和动作流程的理解'}
    out=Path(ctx['output_dir'])/('action_program_'+str(time.time_ns()))
    out.mkdir(parents=True)
    features=' '.join([proposal['task_summary'],*(row['name'] for row in proposal.get('phases',[]))])
    memory=retrieve_experience(ctx | {'output_dir':out,'input':{'query':features}})
    cases={item['case_id']:item for item in memory['cases'][:6]}
    memories=[{key:case.get(key) for key in ('case_id','title','preventive_checks','applicability','status','revision')}
              for case in cases.values()]
    objects={obj['id']:obj for obj in spec['objects']}
    arms=[arm['id'] for arm in spec['robot'].get('arms',[])]
    if not arms:
        return offline | {'status':'needs_configuration','reason':'场景没有绑定录制机器人的可执行关节'}
    views=previous_scene(root,build,run_id=ctx['run_id'],round_number=build.get('refinement_round',0))['views']
    images=[root/view['path'] for view in views]
    context={'task':proposal,'objects':objects,'poses':manifest['settled_poses'],'robot_ids':arms,
             'table':spec['scene']['table'],'roles':(spec.get('scene_approximation') or {}).get('object_roles',{}),
             'demonstrated_scene_contacts':(spec.get('scene_approximation') or {}).get('motion_anchor_review',[]),
             'robot_bases':{arm['id']:arm.get('base_pose') for arm in spec['robot']['arms']}}
    frozen=None
    if previous:
        frozen=json.loads((root/previous['experiment_path']).read_text())
        context['previous_action_program']=previous['action_program']
    audited=preflight(ctx)
    if trajectory_ref:=audited.get('trajectory_path'):
        with np.load(root/trajectory_ref,allow_pickle=False) as tape:
            context['recorded_timing']=recorded_timing(tape)
            order=tape['arms'].tolist()
            first=tape['joint_positions_rad'][:1]
        context['reference_start_tool_m']={
            arm['id']:ToolKinematics(root/arm['urdf_path'],arm['base_pose']).tool(first[:,order.index(arm['id'])])[0][0].tolist()
            for arm in spec['robot']['arms'] if arm['id'] in order
        }
    model_ctx=ctx | {'output_dir':out,'stage':'action_program'}
    ctx['emit']('task.progress',{'message':'动作专家正在结合示教步骤、当前场景和历史经验推断动作计划'})
    instruction=(
        'Design a task-specific offline action program in Chinese. Use the goal and action order evident in the recording. '
        'Derive phase names, count and boundaries from the evidence. Supply end_fraction as an increasing normalized episode time '
        'ending at 1. Give each involved arm its own ordered waypoints for this phase. The simulator generates joint commands by '
        'inverse kinematics from these targets and iterates on measured failures. Recorded joint motion is only the comparison and '
        'initial IK seed. For each waypoint give fraction within this phase, an object-relative position in metres or world position '
        'when relative_to is null, XYZ Euler orientation in that same frame or null to maintain previous orientation, and intended '
        'opening_m or null to hold the last opening. For Nero the tool point is 130 mm along the gripper base local +Z, which points '
        'forward through the fingers. Local Y is the closing axis. Provide explicit orientations and openings where interaction '
        'needs alignment. The first phase may have a fraction 0 start pose. Give separate reachable points for approach, '
        'alignment, contact or release only when the actual task requires them. Choose verified geometry plus plausible '
        'clearances. The object reference is the reset scene pose, including each layout offset. For transport of a moving '
        'object, plan goals relative to destination, a stationary scene object or world. Specify gripper closure separately '
        'after alignment. Keep stationary arms out of the phase arms list; stationary empty phases may have no waypoints. '
        'Set legacy adjustment_m and rotation_adjustment_deg to zero and legacy grippers to an empty list. '
        'Set contact_free=true only for a phase that must run without robot-to-workpiece or robot-to-fixture contact. '
        'For a task with open approach followed by grasp, keep those as separate phases. Deliberate interaction phases must use '
        'contact_free=false, including push, slide, press, or an episode that starts in intentional contact. '
        'When measured gripper transitions are supplied, reconcile them with the same episode visual evidence and use their '
        'time fractions to place phase boundaries. An approach with an open gripper must end before the observed closing '
        'of that same approaching gripper. Opening changes measure actuator state and do not establish contact by themselves. '
        'Use demonstrated_scene_contacts as approximate visual and kinematic references when choosing task-relative targets. '
        'Use current object and fixture geometry to leave room during approach. The optimizer tests alternatives in PhysX. '
        'Do not assume a panel, slot, fixed pose or fixed sequence of actions. '
        'If layout variations are meaningful, group objects that should move together, including their initial supports. '
        'List each proposed group once; empty groups are allowed for a fixed-layout task. Explain use/skip for each retrieved '
        'case. Reuse evidence-backed lessons and never copy actuator commands, thresholds or success outcomes from past cases. '
        'Current task and scene: '+json.dumps(context,ensure_ascii=False)
        +'\nCandidate experiences: '+json.dumps(memories,ensure_ascii=False)
    )
    if previous:
        instruction += ('\nRepair the executable waypoints, arm assignment, orientation or gripper sequence within each phase. '
                        'Keep the ordered phase names, end_fraction, contact_free and variation_groups from the previous program '
                        'exactly so the independent evaluator can reuse its frozen checks.')
    model,raw,_=request_with_evidence(model_ctx,instruction,lambda _:program_schema(objects,arms,cases),
                                      comparison_images=images,frame_selection='task_phases')
    if model['status']!='succeeded':
        return (previous or offline) | model
    program=validate_program(raw,objects,arms,cases,require_waypoints=True)
    if previous:
        structure=lambda value: ([(phase['name'],phase['end_fraction'],phase.get('contact_free',False))
                                  for phase in value['phases']],value['variation_groups'])
        if structure(program)!=structure(previous['action_program']):
            return previous | {'status':'failed','reason':'动作修正改变了冻结的阶段和验收绑定'}
    program_path=out/'action_program.json'
    _save(program_path,program)
    tracking=offline | {'action_program':program,'action_program_path':program_path.relative_to(root).as_posix(),
              'experience_checklist_path':memory['checklist_path']}
    ctx['emit']('expert.plan_ready',{'message':'已生成本次示教的动作计划','program':program})

    if previous:
        tracking['action_acceptance_path']=previous['action_acceptance_path']
        return _bind_and_run(ctx,root,out,manifest_ref,raw_manifest,spec,tracking,audited=audited,
                             frozen=frozen,replanned=True)

    criteria=_criteria(spec,proposal)
    criterion_context=context | {'criteria':criteria,'phases':[
        {key:row[key] for key in ('name','end_fraction')} for row in program['phases']]}
    verifier=(
        'Act as the independent evaluator for this offline demonstration. Express every supplied criterion as phase-indexed '
        'measurements. Cover each phase and the actual demonstrated actions. Do not weaken supplied criteria or add '
        'unobserved requirements for a particular end pose, seating depth or latch. '
        'Use zero-based criterion and phase indices. travel and rise are minimum movement and positive vertical rise in the phase. '
        'region uses lower/upper XYZ bounds on the workpiece center in the live relative_to object frame, or world if null. '
        'stable is maximum object displacement from the end of the phase during the end window. clearance is minimum fingertip '
        'distance from the object bounding box in the end window. gripper_gap is minimum opening of at least one gripper in the '
        'end window. value is the scalar threshold in metres for those checks. orientation uses lower as the desired XYZ Euler '
        'degrees in the relative_to object frame (world if null) and value as the maximum angular error in degrees. '
        'Use zeros for unused fields. '
        'Keep unsupported conditions as explicit unsupported checks, with a short reason, when the trace cannot verify them '
        'such as hidden latch engagement, force, or a precision contact or orientation requirement with no suitable observable. '
        'Use actual model extents and relative geometry. Write check descriptions in Chinese. Task data: '
        +json.dumps(criterion_context,ensure_ascii=False)
    )
    verification_dir=out/'verification'
    verification_dir.mkdir()
    verification_ctx=model_ctx | {'output_dir':verification_dir,'stage':'action_acceptance',
        'dependencies':{key:value for key,value in deps.items() if key!='experience'},'recovery_feedback':None}
    with agent_handoff(ctx,'动作专家 Agent','独立验收 Agent',
                       '为本次动作计划建立独立、可测的验收判据',
                       evidence_path=tracking['action_program_path']) as finish:
        reviewed,raw,_=request_with_evidence(verification_ctx,verifier,
            lambda _:acceptance_schema(objects,len(criteria),len(program['phases'])),
            comparison_images=images,frame_selection='task_phases')
        if reviewed['status']!='succeeded':
            finish(reviewed)
            return tracking | reviewed
        acceptance=validate_acceptance(raw,objects,criteria,len(program['phases']))
        acceptance_path=out/'acceptance.json'
        _save(acceptance_path,acceptance)
        tracking['action_acceptance_path']=acceptance_path.relative_to(root).as_posix()
        finish(reviewed,summary=f'已冻结 {len(acceptance["checks"])} 项验收判据',
               report_path=tracking['action_acceptance_path'])
    return _bind_and_run(ctx,root,out,manifest_ref,raw_manifest,spec,tracking,audited=audited)


def _bind_and_run(ctx,root,out,manifest_ref,raw_manifest,spec,tracking,*,frozen=None,audited=None,replanned=False):
    manifest=json.loads(raw_manifest)
    if audited is None:
        audited=preflight(ctx)
    if not audited.get('replay_limit_check_passed') or set(audited.get('scene_robot_ids',[]))!={'left','right'}:
        return tracking | audited
    references={manifest_ref,manifest['spec_snapshot_path'],manifest['scene_path'],audited['trajectory_path'],
                tracking['experience_checklist_path'],tracking['action_program_path'],tracking['action_acceptance_path']}
    references.update(path for path in manifest.get('usd_layers',[]) if path!='.')
    references.update((spec.get('dependency_hashes') or {}).get('assets',{}))
    references.update(('modules/motion_replay/kinematics.py','modules/motion_replay/timing.py','modules/learning/action_program.py',
                       'modules/learning/waypoint_expert.py',
                       'modules/evaluation/action_program.py','modules/evaluation/action_trace.py',
                       'modules/simulation/isaac/motion_rollout.py','modules/simulation/isaac/contact_trace.py',
                       'application/action_training.py'))
    hashes={}
    for reference in sorted(references):
        path=(root/reference).resolve()
        path.relative_to(root)
        hashes[reference]=hashlib.sha256(path.read_bytes()).hexdigest()
    if (audited['scene_manifest']!=manifest_ref or hashes[manifest_ref]!=hashlib.sha256(raw_manifest).hexdigest()
            or hashes[manifest_ref]!=audited['scene_manifest_sha256']
            or hashes[audited['trajectory_path']]!=audited['trajectory_sha256']):
        raise ValueError('动作计划对应的场景或轨迹在审查期间发生变化')
    immutable=('action_acceptance_path',) if replanned else ('action_program_path','action_acceptance_path','experience_checklist_path')
    if frozen and (hashes[audited['trajectory_path']]!=frozen['hashes'][frozen['trajectory']] or
            any(hashes[tracking[key]]!=frozen['hashes'].get(tracking[key]) for key in immutable)):
        raise ValueError('场景回查期间冻结的轨迹、动作计划、验收条件或经验证据发生变化')
    bundle=out/'experiment.json'
    _save(bundle,{'engine':'demonstration_action_program/v1','program_path':tracking['action_program_path'],
                  'acceptance_path':tracking['action_acceptance_path'],
                  'manifest':manifest_ref,'trajectory':audited['trajectory_path'],'hashes':hashes,
                  'scene_recheck_phase':ctx.get('scene_recheck_phase'),'approach_contact':ctx.get('approach_contact')})
    return _run_isaac(ctx,root,out,bundle,tracking | {'replay_preflight_path':audited['report_path'],
                       'experiment_path':bundle.relative_to(root).as_posix()})


def _run_rebound(ctx,initial):
    root=Path(ctx['root']).resolve()
    manifest_ref=ctx['dependencies']['scene_build']['manifest_path']
    manifest_path=(root/manifest_ref).resolve()
    manifest_path.relative_to(root)
    raw_manifest=manifest_path.read_bytes()
    manifest=json.loads(raw_manifest)
    spec=load_snapshot(root,manifest['spec_snapshot_path'],manifest['task_spec_hash'])
    previous=json.loads((root/initial['experiment_path']).read_text())
    out=Path(ctx['output_dir'])/('action_program_'+str(time.time_ns()))
    out.mkdir()
    keys=('hardware_verified','hardware_commands_sent','contact_execution_authorized',
          'action_program','action_program_path','action_acceptance_path','experience_checklist_path')
    tracking={key:initial[key] for key in keys}
    rebound=ctx | {'output_dir':out}
    return _bind_and_run(rebound,root,out,manifest_ref,raw_manifest,spec,tracking,frozen=previous)


def run(ctx):
    return _run_once(ctx)


def recovery_actions(ctx, failure):
    """Expose only actions with a frozen experiment and the evidence to resume it."""
    if not all(failure.get(key) for key in ('experiment_path','action_program','action_acceptance_path')):
        return {}
    if not failure.get('episode_report_path'):
        return {'retry_stage': retry_frozen_run}
    actions={'replan_action': replan_action}
    if (ctx['dependencies'].get('scene_build') or {}).get('manifest_path'):
        actions['repair_scene']=repair_and_resume
    return actions


def retry_frozen_run(ctx, failure, decision):
    result=_run_rebound(ctx,failure)
    if handoff:=failure.get('scene_recheck_result'):
        result['scene_recheck_result']=handoff
    return _publish_candidate(ctx,result)


def _publish_candidate(ctx,result):
    candidate=ctx.get('pending_scene')
    if not candidate or not (result.get('approach_verified') or result.get('status')=='succeeded'):
        return result,ctx
    ctx['emit']('scene.published',candidate)
    working={key:value for key,value in ctx.items() if key!='pending_scene'}
    handoff=(result.get('scene_recheck_result') or {}) | {'status':'approach_rechecked'}
    return result | {'resolved_scene_build':candidate['build'],'scene_recheck_result':handoff},working


def repair_and_resume(ctx, initial, decision):
    build=ctx['dependencies'].get('scene_build') or {}
    feedback=initial.get('scene_recheck') or {}
    if (feedback.get('scene_manifest_path')!=build.get('manifest_path')
            or feedback.get('source_scene_hash')!=build.get('task_spec_hash')):
        feedback={}
    failure_checks=[check | {'id':check.get('id') or f'action_check_{index}'}
                    for index,check in enumerate(initial.get('checks',[]))
                    if not check.get('passed') and check.get('evaluated',True)]
    if not feedback:
        feedback={'kind':'supervisor_scene_review','phase':min((check.get('phase',0) for check in failure_checks),default=0),
                  'scene_manifest_path':build['manifest_path'],'reason':initial.get('reason') or initial.get('error')}
    root=Path(ctx['root']).resolve()
    folder=Path(ctx['output_dir'])/('approach_scene_recheck_'+str(time.time_ns()))
    folder.mkdir(parents=True)
    ctx['check_cancel']()
    ctx['emit']('task.progress',{'message':'场景 Agent 正在复核总控分派的问题'})
    validation={'status':'failed','report_path':initial.get('report_path'),
                'verified_trial_review':decision.get('verified_trial_review',[]),
                'checks':[{'id':feedback['kind'],'passed':False,'details':feedback},*failure_checks]}
    checked=repair_scene(ctx | {'output_dir':folder},build,validation,motion_feedback=feedback,
                         supervisor_instruction=decision['instruction'])
    handoff={'status':'no_verified_scene_change','source_scene_manifest':build['manifest_path'],
             'source_episode_report_path':initial['episode_report_path'],
             'repair_report_path':checked.get('repair_report_path') or checked.get('report_path')}
    candidate=checked.get('resolved_scene_build') or {}
    if checked['status']!='succeeded' or not candidate.get('manifest_path') or candidate.get('task_spec_hash')==build['task_spec_hash']:
        return initial | {'scene_recheck_result':handoff},ctx
    pending={'build':candidate,'validation_path':checked.get('report_path'),
             'visual_comparison_path':checked.get('visual_comparison_path'),
             'visual_comparison_status':checked.get('visual_comparison_status'),
             'repair_report_path':handoff['repair_report_path']}
    working=ctx | {'dependencies':ctx['dependencies'] | {'scene_build':candidate},
                   'scene_recheck_phase':feedback['phase'],'pending_scene':pending}
    ctx['emit']('task.progress',{'message':'新版场景已通过独立检查，动作专家正在原判据下复测'})
    ctx['check_cancel']()
    with agent_handoff(ctx,'场景 Agent','动作专家 Agent',
                       '交接通过独立检查的候选场景，沿用冻结判据复测',
                       evidence_path=candidate['manifest_path']) as finish:
        result=_run_rebound(working,initial)
        finish(result,report_path=result.get('report_path') or result.get('episode_report_path'))
    handoff.update(status='candidate_not_verified_in_motion',proposed_scene_manifest=candidate['manifest_path'],
                   retest_episode_report_path=result.get('episode_report_path'))
    return _publish_candidate(working,result | {'scene_recheck_result':handoff})


def replan_action(ctx, failure, decision):
    repair={'instruction':decision['instruction'],'diagnosis':decision['diagnosis'],'evidence':decision.get('evidence'),
            'verified_trial_review':decision.get('verified_trial_review',[])[:1],
            'failure':failure.get('reason') or failure.get('error'),'checks':failure.get('checks',[])[:12],
            'contact':failure.get('scene_recheck')}
    result=_run_once(ctx | {'recovery_feedback':repair},previous=failure)
    if handoff:=failure.get('scene_recheck_result'):
        result['scene_recheck_result']=handoff | {'approach_retry_status':result['status'],
            'approach_retry_scene_manifest':ctx['dependencies']['scene_build']['manifest_path'],
            'approach_retry_episode_report_path':result.get('episode_report_path')}
    return _publish_candidate(ctx,result)
