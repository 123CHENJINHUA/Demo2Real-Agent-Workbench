"""Connect new recorded demonstrations to a bounded offline expert learning run."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time

from application.motion_preflight import run as preflight
from application.scene_repair import repair as repair_scene
from application.stage_diagnosis import insertion_capability_block
from modules.environment.service import local_environment, resolve
from modules.experiments.placement_expert import (
    SlotClearanceError, UnsupportedPlacementTask, audit_source_clearance, freeze, verify_clearance_repair,
)
from modules.scene_building.service import _stop
from modules.task_specs import load_snapshot


def layout_roles(root, spec, build):
    """Recover task-scoped roles from a linked refinement for older scene snapshots."""
    approximation = spec.get('scene_approximation') or {}
    if approximation.get('object_roles'):
        return approximation['object_roles'],None
    rounds = build.get('refinement_rounds') or []
    reference = rounds[-1].get('corrections') if rounds else None
    if not reference:
        return None,None
    path = root/reference
    if path.resolve()!=path or not path.is_relative_to(root):
        raise ValueError('场景角色证据路径无效')
    report = json.loads(path.read_text())
    expected = {spec['content_hash'],approximation.get('repair_source_spec_hash'),
                spec['robot'].get('preview_registration',{}).get('input_spec_hash')}-{None}
    if report.get('preview_spec_hash') not in expected:
        raise ValueError('角色证据与当前仿真场景的修订不匹配')
    estimates = (report.get('layout_estimates') or {}).get('objects',[])
    return {item['id']:{'shape':item.get('shape'),'support_id':item.get('support_id')} for item in estimates},reference


def _replay_ready(audit):
    return audit.get('replay_limit_check_passed') and set(audit.get('scene_robot_ids',[]))=={'left','right'}


def _bound_scene(root, audited):
    manifest_ref = audited['scene_manifest']
    path = (root/manifest_ref).resolve()
    path.relative_to(root)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=audited['scene_manifest_sha256']:
        raise ValueError('仿真场景清单在回放审查后发生变化')
    manifest = json.loads(raw)
    return manifest_ref,manifest,load_snapshot(root,manifest['spec_snapshot_path'],manifest['task_spec_hash'])


def _repair_insertion(ctx, audited, manifest, spec, roles, failure):
    root = Path(ctx['root']).resolve()
    label = failure.details['slot_label']
    ctx['check_cancel']()
    ctx['emit']('task.progress',{'message':f'正在核对「{label}」原始模型与直立入槽碰撞截面'})
    try:
        source = audit_source_clearance(root,spec,failure.details)
    except (OSError,KeyError,TypeError,ValueError) as exc:
        source = {'status':'source_measurement_failed','error':str(exc),'samples':[]}
    ids = {failure.details['panel_id'],failure.details['slot_id']}
    assets = {obj['id']:obj.get('asset_path') for obj in spec['objects'] if obj['id'] in ids}
    evidence = failure.details | {'source_geometry':source,'source_assets':assets}
    check = {'id':'upright_insertion_clearance','passed':False,'details':evidence}
    out = Path(ctx['output_dir'])/('insertion_check_'+str(time.time_ns()))
    out.mkdir(parents=True)
    report = out/'insertion_clearance.json'
    report_ref = report.relative_to(root).as_posix()
    report.write_text(json.dumps({'scene_manifest':audited['scene_manifest'],'scene_manifest_sha256':audited['scene_manifest_sha256'],
                                  'task_spec_hash':spec['content_hash'],'scope':'upright_floor_seated_geometry',
                                  'replay_preflight_path':audited['report_path'],'checks':[check]},
                                 ensure_ascii=False,indent=2,allow_nan=False))
    fields = {'insertion_report_path':report_ref,'replay_preflight_path':audited['report_path']}
    result = audited | fields | {'status':'needs_configuration','checks':[check],'report_path':report_ref}
    if blocked := insertion_capability_block(result):
        return None,blocked
    if source['status']!='no_interference_sampled':
        return None,result | {'reason':str(failure)+'目前缺少足以修正碰撞体的原始截面证据。'}
    if not spec.get('scene_approximation') or ctx['input'].get('implementation_lock_path'):
        return None,result | {'reason':str(failure)+'当前场景未启用自动修改碰撞体。'}

    def clearance_check(revised):
        for original,candidate in zip(spec['objects'],revised['objects'],strict=True):
            if original['pose']!=candidate['pose'] or (original['id'] not in ids and original!=candidate):
                raise ValueError('入槽纠错仅允许修正相关的面板和槽碰撞体')
        revised_experiment = freeze(revised,roles=roles,root=root)
        verify_clearance_repair(revised_experiment,failure.details,source)
        return {'id':check['id'],'passed':True,'details':revised_experiment['slot_openings'][evidence['slot_role']]}

    build = (ctx['dependencies'].get('scene_build') or {}) | {
        'manifest_path':audited['scene_manifest'],'task_spec_hash':spec['content_hash'],'spec_path':manifest['spec_snapshot_path']}
    corrected = repair_scene(ctx,build,{'checks':[check],'report_path':report_ref},
                             clearance_check=clearance_check,clearance_evidence=evidence)
    repair_fields = fields | {'repair_report_path':corrected['repair_report_path']}
    if corrected['status']!='succeeded':
        return None,result | repair_fields | {'status':corrected['status'],'report_path':corrected['report_path'],
                                               'reason':str(failure)+'场景纠错 Agent 未找到通过复验的碰撞体修正。'}
    candidate = corrected['resolved_scene_build']
    return candidate | {'refinement_rounds':build.get('refinement_rounds',[])},repair_fields | {
        'repaired_scene_manifest':candidate['manifest_path'],'repaired_scene_validation_path':corrected['report_path']}


def run(ctx):
    root = Path(ctx['root']).resolve()
    deps = ctx['dependencies'].copy()
    inputs = ctx['input']
    if inputs.get('placement_expert_run') is True:
        deps.setdefault('demo_import',{'manifest_path':inputs.get('source_import_manifest_path')})
        deps.setdefault('scene_build',{'manifest_path':inputs.get('scene_manifest_path')})
    active = ctx.get('active_scene') or {}
    if active.get('stage')=='simulation':
        build = active['build']
        if build.get('manifest_path') and build.get('task_spec_hash'):
            original = deps.get('scene_build') or {}
            deps['scene_build'] = build | {'refinement_rounds':original.get('refinement_rounds',[])}
    ctx = ctx | {'dependencies':deps}
    audited = preflight(ctx)
    if not _replay_ready(audited):
        return audited
    repair_evidence = {}
    try:
        manifest_ref,manifest,spec = _bound_scene(root,audited)
        roles,role_evidence = layout_roles(root,spec,deps.get('scene_build') or {})
        try:
            experiment = freeze(spec,roles=roles,root=root)
        except SlotClearanceError as exc:
            revised,repair_evidence = _repair_insertion(ctx,audited,manifest,spec,roles,exc)
            if revised is None:
                return repair_evidence
            ctx = ctx | {'dependencies':deps | {'scene_build':revised}}
            audited = preflight(ctx)
            if not _replay_ready(audited):
                return repair_evidence | audited
            manifest_ref,manifest,spec = _bound_scene(root,audited)
            if spec['content_hash']!=revised['task_spec_hash']:
                raise ValueError('重放使用的场景与已复验的修正版不一致')
            experiment = freeze(spec,roles=roles,root=root)
    except UnsupportedPlacementTask as exc:
        return audited | {'status':'needs_configuration','reason':str(exc),'unsupported_capability':'task_scoped_action_expert'}
    except (OSError,KeyError,TypeError,ValueError) as exc:
        return repair_evidence | audited | {'status':'needs_configuration','reason':str(exc)}
    audited = repair_evidence | audited

    sim = resolve(root,'isaac_sim_root',False)
    if not sim or not (sim/'python.sh').is_file():
        return audited | {'status':'needs_configuration','reason':'缺少 Isaac Sim，无法训练和检查任务动作专家'}
    out = Path(ctx['output_dir'])/('expert_learning_'+str(time.time_ns()))
    out.mkdir(parents=True)
    references = {manifest_ref,audited['trajectory_path'],manifest['spec_snapshot_path'],manifest['scene_path']}
    if role_evidence:
        references.add(role_evidence)
    references.update(ref for ref in manifest.get('usd_layers',[]) if ref!='.')
    references.update(spec['dependency_hashes'].get('assets',{}))
    references.update(('modules/learning/placement_expert.py','modules/evaluation/placement_expert.py',
                       'modules/simulation/isaac/placement_rollout.py','application/placement_training.py'))
    hashes = {}
    for ref in sorted(references):
        path = (root/ref).resolve()
        path.relative_to(root)
        hashes[ref] = hashlib.sha256(path.read_bytes()).hexdigest()
    if hashes[audited['trajectory_path']]!=audited['trajectory_sha256'] or hashes[manifest_ref]!=audited['scene_manifest_sha256']:
        raise ValueError('离线轨迹或仿真场景在审查后发生变化')
    plan = {'experiment':experiment,'hashes':hashes,'manifest':manifest_ref,'trajectory':audited['trajectory_path'],
            'episode_index':audited['episode_index']}
    plan_path = out/'experiment.json'
    plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2,allow_nan=False))
    report_path = out/'expert_learning_report.json'
    log_path = out/'isaac_learning.log'
    command = [str(sim/'python.sh'),str(root/'workers/learn_placement_expert.py'),'--plan',str(plan_path),
               '--output',str(out),'--portable-root',str(root/'.runtime/isaac-expert')]
    process = None
    with (root/'.runtime/gpu.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return audited | {'status':'blocked','reason':'Isaac 正运行其他离线任务，待资源空闲后重试'}
        try:
            ctx['emit']('task.progress',{'message':'从示教拟合双臂动作并启动不同组件位置的物理试验'})
            env = local_environment(root,{'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2'})
            with log_path.open('wb') as log, log_path.open('r',errors='replace') as reader:
                process = subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                started = time.monotonic()
                while True:
                    for line in reader:
                        if line.startswith('PLACEMENT_EVENT '):
                            ctx['emit']('task.progress',json.loads(line[len('PLACEMENT_EVENT '):]))
                    if process.poll() is not None:
                        break
                    ctx['check_cancel']()
                    if time.monotonic()-started>1200:
                        raise TimeoutError('动作专家训练超过20分钟上限')
                    time.sleep(.2)
        finally:
            _stop(process)

    if not report_path.is_file():
        return repair_evidence | {'status':'failed','error':'Isaac 训练退出，查看学习日志','evidence_paths':[log_path.relative_to(root).as_posix()]}
    result = json.loads(report_path.read_text())
    return repair_evidence | result | {'report_path':report_path.relative_to(root).as_posix(),
                                       'replay_preflight_path':audited['report_path']}
