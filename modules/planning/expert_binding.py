"""Bind the retained Connector expert to one checked task/scene/source snapshot.

No Isaac, vision model or device imports. The only subprocess reads imported
Parquet via the registered data interpreter, with project-local caches.
"""
from pathlib import Path
import copy, hashlib, json, math, os, subprocess
import numpy as np
from contracts.tasks import canonical_hash, validate_task_spec
from contracts.experiments import checked_scope
from modules.environment.service import resolve, local_environment
from modules.task_specs.service import load_snapshot

VERSION = 'connector-expert-plan/v1'
PROFILE_ID = 'connector_keyed_v1'
DEMO = 'data/reference/connector/converted/episode_000.npz'
CONVERSION = 'data/reference/connector/converted/conversion.json'
OVERLAY = dict(id='connector_contact_execution/v1', finger_drive_type='force',
    finger_stiffness_N_m=600., finger_damping_Ns_m=10., finger_max_force_N=10.,
    finger_contact_offset_m=.001, finger_friction=dict(static=1.2, dynamic=1.),
    finger_mesh_approximation='convexDecomposition', body_angular_damping_per_s=5.,
    body_linear_damping_per_s=.1, body_sleep_threshold=.00001,
    gravity_compensation=True, solver_position_iterations=64, solver_velocity_iterations=16)

def _local(root, value, must_exist=True):
    if not isinstance(value, (str, Path)) or not str(value):raise ValueError('Explicit project-local path required')
    root=Path(root).resolve(); raw=Path(value)
    p=(root/raw).absolute() if not raw.is_absolute() else raw.absolute()
    if not p.is_relative_to(root) or not p.resolve().is_relative_to(root):raise ValueError('Path escapes project')
    current=p
    while current!=root:
        if current.is_symlink():raise ValueError('Symlink references are not accepted')
        current=current.parent
    if must_exist and not p.is_file():raise ValueError('Missing input file: '+str(value))
    return p.resolve()

def _sha(path):
    d=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(1024*1024):d.update(block)
    return d.hexdigest()

def _json(path):return json.loads(Path(path).read_text())
def _seal(value):return hashlib.sha256(json.dumps({k:v for k,v in value.items() if k!='content_hash'},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def _supported(spec, profile):
    if spec.get('profile_id')!=PROFILE_ID or profile.get('id')!=PROFILE_ID:raise ValueError('Only the registered Connector expert is supported')
    defaults=profile['defaults']
    for field in ('robot','scene','frames','evaluation'):
        if spec.get(field)!=defaults.get(field):raise ValueError('Retained expert does not support changed '+field)
    objects=spec.get('objects',[]);expected=defaults['objects']
    if len(objects)!=2 or [x.get('id') for x in objects]!=[x['id'] for x in expected]:raise ValueError('Connector object IDs/order are incompatible')
    for obj, baseline in zip(objects, expected):
        if {k:v for k,v in obj.items() if k!='pose'}!={k:v for k,v in baseline.items() if k!='pose'}:raise ValueError('Only Connector initial poses may change; asset, scale, mass and collision must match')
        pose=obj['pose'];base=baseline['pose']
        if np.linalg.norm(np.asarray(pose['position_m'])-base['position_m'])>.050000001:raise ValueError('Object translation exceeds 50 mm')
        qa=np.asarray(pose['quaternion_wxyz'],float);qb=np.asarray(base['quaternion_wxyz'],float)
        angle=2*math.acos(float(np.clip(abs(qa@qb)/(np.linalg.norm(qa)*np.linalg.norm(qb)),-1,1)))
        if angle>math.radians(30)+1e-8:raise ValueError('Object orientation change exceeds 30 degrees')

def _demo_shapes(path):
    shapes={'time':(555,), 'eef_poses':(555,2,4,4), 'object_poses':(555,2,4,4),
            'joints':(555,2,7), 'widths':(555,2), 'initial_objects':(2,4,4), 'grasp_frames':(2,)}
    with np.load(path,allow_pickle=False) as data:
        for key,shape in shapes.items():
            if key not in data or data[key].shape!=shape or not np.isfinite(data[key]).all():raise ValueError('Retained demo shape/nonfinite mismatch: '+key)
        times=data['time'];widths=data['widths']
        if abs(times[0])>1e-6 or not np.allclose(np.diff(times),1/30,atol=2e-6,rtol=0):raise ValueError('Expected 555 monotonic 30 Hz source samples')
        if not np.all((widths>=0)&(widths<=.1)):raise ValueError('Invalid source jaw widths in metres')
        if not np.issubdtype(data['grasp_frames'].dtype,np.integer) or not np.all((data['grasp_frames']>=0)&(data['grasp_frames']<555)):raise ValueError('Invalid source grasp frames')
        for name in ('eef_poses','object_poses','initial_objects'):
            matrices=data[name].reshape(-1,4,4);R=matrices[:,:3,:3]
            if not np.allclose(matrices[:,3,:],[0,0,0,1],atol=1e-7) or not np.allclose(R.transpose(0,2,1)@R,np.eye(3),atol=2e-5) or not np.allclose(np.linalg.det(R),1,atol=2e-5):raise ValueError('Source poses must be rigid SE(3): '+name)
    return {'frames':555,'fps':30,'episode_index':0,'source_pose_kind':'manual RGB landmarks + model/horizontal assumption + finger registration; not measured pose truth'}

# Deliberately separate process: app environment need not have PyArrow.
_LINEAGE_READER = r'''
import json,sys
from pathlib import Path
import numpy as np,pyarrow.parquet as pq,pyarrow.compute as pc
root=Path(sys.argv[1]);dataset=Path(sys.argv[2]);demo=Path(sys.argv[3])
info=json.loads((dataset/'meta/info.json').read_text());names=info['features']['observation.state']['names']
if info['fps']!=30:raise ValueError('Source FPS differs')
parts=[];used=[]
for p in sorted((dataset/'data').rglob('*.parquet')):
 t=pq.read_table(p,columns=['episode_index','frame_index','timestamp','observation.state'])
 t=t.filter(pc.equal(t['episode_index'],0))
 if len(t):parts.extend(t.to_pylist());used.append(str(p.relative_to(root)))
parts.sort(key=lambda r:r['frame_index'])
if len(parts)!=555 or [r['frame_index'] for r in parts]!=list(range(555)):raise ValueError('Episode 0 must contain exactly 555 unique ordered frames')
s=np.asarray([r['observation.state'] for r in parts]);t=np.asarray([r['timestamp'] for r in parts])
q=np.stack([s[:,[names.index(a+'.joint'+str(j)+'.pos') for j in range(1,8)]] for a in ('left','right')],axis=1)
w=np.column_stack([s[:,names.index(a+'.gripper.pos')] for a in ('left','right')])
with np.load(demo,allow_pickle=False) as z:
 if not np.allclose(q,z['joints'],atol=1e-7,rtol=0) or not np.allclose(w,z['widths'],atol=1e-7,rtol=0) or not np.allclose(t,z['time'],atol=1e-6,rtol=0):raise ValueError('Retained NPZ does not match source episode 0 joint/width/time feedback')
print(json.dumps({'verified':True,'method':'source_episode0_feedback_time_comparison','episode_index':0,'frames':555,'source_files':used,'object_pose_truth_verified':False}))
'''

def _verify_lineage(root, dataset, demo):
    interpreter=resolve(root,'data_python');env=local_environment(root)
    env.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    result=subprocess.run([str(interpreter),'-B','-c',_LINEAGE_READER,str(root),str(dataset),str(demo)],cwd=root,env=env,capture_output=True,text=True,timeout=120)
    if result.returncode:raise ValueError('Source lineage comparison failed: '+result.stderr[-1800:])
    try:report=json.loads(result.stdout.strip())
    except ValueError as exc:raise ValueError('Source lineage reader did not return JSON') from exc
    if report.get('verified') is not True:raise ValueError('Source lineage is unverified')
    return report

def _inputs(root, inp, deps, compare_feedback=True):
    required=('spec_path','spec_hash','spec_revision','scene_manifest_path','demo_path')
    for key in required:
        if key not in inp:raise ValueError('Explicit '+key+' is required for expert binding')
    if isinstance(inp['spec_revision'],bool) or not isinstance(inp['spec_revision'],int):raise ValueError('spec_revision must be an integer')
    spec_path=_local(root,inp['spec_path']);spec=load_snapshot(root,str(spec_path),inp['spec_hash'])
    if spec['revision']!=inp['spec_revision']:raise ValueError('Pinned spec revision changed')
    profile_path=_local(root,'tasks/connector/profile.json');profile=_json(profile_path);_supported(spec,profile)
    profile_binding=spec['dependency_hashes'].get('profile') or {}
    if profile_binding.get('path')!='tasks/connector/profile.json' or profile_binding.get('sha256')!=_sha(profile_path):raise ValueError('Spec does not bind the registered expert profile')
    refs={str(spec_path.relative_to(root)):_sha(spec_path),str(profile_path.relative_to(root)):_sha(profile_path)}
    for group in ('assets','evidence'):refs.update(spec['dependency_hashes'].get(group,{}))
    for rel,digest in refs.items():
        if _sha(_local(root,rel))!=digest:raise ValueError('Changed task dependency: '+rel)
    manifest_path=_local(root,inp['scene_manifest_path']);manifest=_json(manifest_path)
    validation_ref=(deps.get('scene_validation') or {}).get('report_path') or inp.get('scene_validation_path')
    validation_path=_local(root,validation_ref);validation=_json(validation_path)
    for name,value in [('scene',manifest),('scene validation',validation)]:
        if value.get('task_spec_hash')!=spec['content_hash'] or value.get('spec_revision')!=spec['revision']:raise ValueError(name+' does not bind this spec hash/revision')
    if validation.get('status')!='succeeded' or validation.get('manifest_path')!=str(manifest_path.relative_to(root)):raise ValueError('Successful independent validation must name this scene manifest')
    required_checks={'task_hash','usd_composition','usd_physics_scene','usd_no_forbidden_object_attachment','no_controller_or_hardware','object_count'}
    checks=validation.get('checks',[])
    if not required_checks.issubset({x.get('id') for x in checks}) or not all(x.get('passed') is True for x in checks):raise ValueError('Independent scene checks are incomplete or failed')
    if manifest.get('profile_id')!=PROFILE_ID or manifest.get('objects')!=spec['objects'] or manifest.get('evaluation')!=spec['evaluation']:raise ValueError('Scene object/evaluation identity mismatch')
    if manifest.get('full_task_executed') is not False or manifest.get('hardware_commands_sent')!=0 or manifest.get('robot_motion_controller_loaded') is not False:raise ValueError('Expected checked preview/local-settling scene, not an executed or hardware scene')
    built_spec=_local(root,manifest.get('spec_snapshot_path'));built=_json(built_spec)
    if canonical_hash(built)!=spec['content_hash'] or built.get('revision')!=spec['revision']:raise ValueError('Scene snapshot differs from task')
    artifact_hashes=validation.get('artifact_sha256')
    if not isinstance(artifact_hashes,dict) or not artifact_hashes:raise ValueError('Fresh independent scene artifact SHA256 closure required; revalidate the scene')
    scene=_local(root,manifest.get('scene_path'))
    needed={str(scene.relative_to(root)),str(manifest_path.relative_to(root))}
    for item in manifest.get('usd_layers',[]):
        if item=='.':continue
        needed.add(str(_local(root,item).relative_to(root)))
    if not needed.issubset(artifact_hashes):raise ValueError('Scene validation did not hash every scene layer and manifest')
    for rel,digest in artifact_hashes.items():
        if _sha(_local(root,rel))!=digest:raise ValueError('Scene changed after independent validation: '+rel)
    refs.update(artifact_hashes);refs[str(validation_path.relative_to(root))]=_sha(validation_path);refs[str(built_spec.relative_to(root))]=_sha(built_spec)
    demo=_local(root,inp['demo_path'])
    if str(demo.relative_to(root))!=DEMO or _sha(demo)!=profile['asset_signatures'].get(DEMO):raise ValueError('Only the explicit registered episode-0 reference is supported; another demo requires a new compatible expert')
    facts=_demo_shapes(demo);conversion=_local(root,CONVERSION);converted=_json(conversion)
    migration_path=_local(root,'docs/migration_manifest.json');migration=_json(migration_path)
    record=next((x for x in migration.get('files',[]) if x.get('path')==CONVERSION),{})
    if _sha(conversion)!=record.get('current_sha256'):raise ValueError('Retained conversion annotation changed from registered migration provenance')
    if converted.get('episode')!=0 or converted.get('frames')!=555:raise ValueError('Unsupported conversion episode/frame count')
    source_manifest=_local(root,spec['source'].get('manifest_path'));source=_json(source_manifest)
    dataset=_local(root,source.get('result',{}).get('dataset_path'),False)
    if not dataset.is_dir():raise ValueError('Imported source dataset is unavailable')
    entries={x['path']:x['sha256'] for x in source.get('files',[]) if 'path' in x and 'sha256' in x}
    source_refs=['meta/info.json']+[str(p.relative_to(dataset)) for p in sorted((dataset/'data').rglob('*.parquet'))]
    if len(source_refs)<2:raise ValueError('Source dataset has no Parquet feedback')
    for rel in source_refs:
        p=_local(root,dataset/rel);digest=_sha(p)
        if entries.get(rel)!=digest:raise ValueError('Imported source changed from manifest: '+rel)
        refs[str(p.relative_to(root))]=digest
    lineage=_verify_lineage(root,dataset,demo) if compare_feedback else None
    for p in (demo,conversion,source_manifest,migration_path):refs[str(p.relative_to(root))]=_sha(p)
    for p in [root/'config/environments.yaml',*sorted((root/'tasks/connector/legacy/sim_bridge').glob('*.py')),*sorted((root/'tasks/connector/components').glob('*.py'))]:
        p=_local(root,p);refs[str(p.relative_to(root))]=_sha(p)
    if inp.get('recovery'):
        from contracts.recovery import checked_recovery,task_signature
        config=checked_recovery(inp['recovery'])
        for rel in ('contracts/recovery.py','modules/motion_expert/recovery_runtime.py','modules/policies/neural/recovery_supervisor/__init__.py'):
            refs[rel]=_sha(_local(root,rel))
        if spec.get('simulation_variant',{}).get('case',{}).get('recovery')!=config:raise ValueError('Recovery does not match declared episode')
        if config.get('skill_version')=='contact_center_v2':
            for rel in ('modules/planning/regrasp.py','modules/skills/connector_regrasp.py','modules/geometry/functional.py','assets/nero/meshes/gripper_base.stl','assets/nero/meshes/gripper_link1.stl','assets/nero/meshes/gripper_link2.stl'):
                refs[rel]=_sha(_local(root,rel))
        if config.get('phase_version'):
            for rel in ('contracts/phase_recovery.py','modules/policies/scripted/phase_guard/__init__.py','modules/skills/insertion_recovery.py','modules/skills/failure_exit.py','modules/planning/return_home.py','config/phase_recovery_v3.json'):
                refs[rel]=_sha(_local(root,rel))
        if config.get('policy_path'):
            from modules.policies.neural.recovery_supervisor import RecoverySupervisor
            model=RecoverySupervisor(_local(root,config['policy_path']))
            if model.metadata['task_signature']!=task_signature(root):raise ValueError('Student task signature mismatch')
            refs[config['policy_path']]=_sha(_local(root,config['policy_path']))
    if inp.get('implementation_lock_path'):
        from modules.experiments.locks import verify_lock
        frozen=verify_lock(root,inp['implementation_lock_path'],inp.get('implementation_lock_sha256'))
        refs.update(frozen['files']);refs[frozen['path']]=frozen['sha256']
    return spec,manifest,refs,facts,lineage,source_manifest,conversion,validation_path

def create_plan(ctx):
    root=Path(ctx['root']).resolve();inp=ctx.get('input',{});deps=ctx.get('dependencies',{})
    try:
        spec,manifest,refs,facts,lineage,source_manifest,conversion,validation_path=_inputs(root,inp,deps)
        plan={'schema_version':VERSION,'evaluation_scope':checked_scope(inp.get('evaluation_scope','full_task')),'binding_scope':'offline_contact_expert_only','task_spec_hash':spec['content_hash'],'spec_revision':spec['revision'],
            'spec_path':str(_local(root,inp['spec_path']).relative_to(root)),'scene_manifest_path':str(_local(root,inp['scene_manifest_path']).relative_to(root)),
            'scene_path':manifest['scene_path'],'scene_validation_path':str(validation_path.relative_to(root)),'demo_path':str(_local(root,inp['demo_path']).relative_to(root)),
            'source_manifest_path':str(source_manifest.relative_to(root)),'conversion_path':str(conversion.relative_to(root)),
            'input_hashes':refs,'expert_id':'connector_retained_episode0/v1','expert_kind':'scripted_demo_relative_contact_expert','learned_policy':False,
            'expert_perception':'simulator_ground_truth','observation_scope':'privileged simulation diagnostic, not RGBD or hardware validation',
            'trajectory_source':facts|{'lineage':lineage,'object_pose_method':'retained manual RGB/model/horizontal and finger-contact assumptions; not reconstructed by this run'},
            'initialization':{'objects':'loaded settled scene; no source-demo pose reset','robot':'spec initial_joint_positions','reset_from_demo':False,'randomization':False},
            'execution_overlay':copy.deepcopy(OVERLAY),'evaluation_rules':{'action':copy.deepcopy(spec['evaluation']['action']),'retention':copy.deepcopy(spec['evaluation']['retention']),
                'action_and_retention_separate':True,'retention_required':spec['evaluation']['retention'].get('required') is True,'known_retention_failure':'CONN-006'},
            'assumptions':copy.deepcopy(spec['assumptions']),'unresolved':copy.deepcopy(spec['unresolved']),
            'hardware_commands_sent':0,'hardware_verified':False,'contact_execution_authorized':False,'full_task_executed':False}
        if inp.get('recovery'):
            plan['recovery']=copy.deepcopy(inp['recovery']);plan['expert_perception']='simulated_pose_estimate';plan['learned_policy']=bool(inp['recovery'].get('policy_path'))
        if inp.get('implementation_lock_path'):
            plan['implementation_lock']={'path':inp['implementation_lock_path'],'sha256':inp['implementation_lock_sha256']}
        plan['content_hash']=_seal(plan)
        output=_local(root,ctx['output_dir'],False);output.mkdir(parents=True,exist_ok=True);path=output/'expert_plan.json'
        with path.open('x') as stream:json.dump(plan,stream,ensure_ascii=False,indent=2,allow_nan=False)
        if ctx.get('emit'):ctx['emit']('expert.plan_bound',{'task_spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'expert_perception':plan['expert_perception'],'hardware_commands_sent':0})
        return {'status':'succeeded','plan_path':str(path.relative_to(root)),'task_spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'expert_perception':plan['expert_perception'],'binding_scope':plan['binding_scope'],'hardware_commands_sent':0,'full_task_executed':False}
    except (ValueError,OSError,RuntimeError,KeyError,TypeError,subprocess.TimeoutExpired) as exc:
        return {'status':'needs_configuration','reason':str(exc),'hardware_commands_sent':0,'hardware_verified':False,'full_task_executed':False}

def load_plan(root,path):
    root=Path(root).resolve();file=_local(root,path);plan=_json(file)
    checked_scope(plan.get('evaluation_scope','full_task'))
    if plan.get('schema_version')!=VERSION or plan.get('content_hash')!=_seal(plan):raise ValueError('Expert plan content hash/version mismatch')
    if plan.get('binding_scope')!='offline_contact_expert_only' or plan.get('hardware_commands_sent')!=0 or plan.get('hardware_verified') is not False or plan.get('expert_perception')!=('simulated_pose_estimate' if plan.get('recovery') else 'simulator_ground_truth'):raise ValueError('Expert plan scope is incompatible')
    for rel,digest in plan.get('input_hashes',{}).items():
        if _sha(_local(root,rel))!=digest:raise ValueError('Expert input changed: '+rel)
    # Recheck semantic binding and require the complete recorded closure.
    inp={key:plan[key] for key in ('spec_path','scene_manifest_path','demo_path')}
    inp.update(spec_hash=plan['task_spec_hash'],spec_revision=plan['spec_revision'],scene_validation_path=plan['scene_validation_path'])
    if plan.get('implementation_lock'):
        inp.update(implementation_lock_path=plan['implementation_lock']['path'],implementation_lock_sha256=plan['implementation_lock']['sha256'])
    if plan.get('recovery'):inp['recovery']=plan['recovery']
    checked=_inputs(root,inp,{},compare_feedback=False)
    if checked[2]!=plan.get('input_hashes'):raise ValueError('Expert input hash closure is incomplete or changed')
    if plan.get('scene_path')!=checked[1]['scene_path'] or plan.get('source_manifest_path')!=str(checked[5].relative_to(root)) or plan.get('conversion_path')!=str(checked[6].relative_to(root)):raise ValueError('Expert semantic paths differ from frozen scene/source')
    if plan.get('evaluation_rules',{}).get('action')!=checked[0]['evaluation']['action'] or plan.get('evaluation_rules',{}).get('retention')!=checked[0]['evaluation']['retention']:raise ValueError('Expert evaluation rules changed')
    spec=load_snapshot(root,plan['spec_path'],plan['task_spec_hash']);profile=_json(_local(root,'tasks/connector/profile.json'));_supported(spec,profile)
    if spec['revision']!=plan['spec_revision'] or plan['execution_overlay']!=OVERLAY:raise ValueError('Expert revision/overlay changed')
    expected_rules={'action':spec['evaluation']['action'],'retention':spec['evaluation']['retention'],
        'action_and_retention_separate':True,'retention_required':spec['evaluation']['retention'].get('required') is True,'known_retention_failure':'CONN-006'}
    if plan.get('evaluation_rules')!=expected_rules:raise ValueError('Expert evaluation rule flags changed')
    if plan.get('expert_id')!='connector_retained_episode0/v1' or plan.get('expert_kind')!='scripted_demo_relative_contact_expert' or plan.get('learned_policy') is not bool(plan.get('recovery',{}).get('policy_path')) or plan.get('full_task_executed') is not False or plan.get('contact_execution_authorized') is not False:raise ValueError('Expert identity/reporting scope changed')
    facts=checked[3];provenance=plan.get('trajectory_source',{})
    if any(provenance.get(k)!=v for k,v in facts.items()):raise ValueError('Trajectory source facts changed')
    lineage=provenance.get('lineage',{})
    if lineage.get('verified') is not True or lineage.get('episode_index')!=0 or lineage.get('frames')!=555 or lineage.get('object_pose_truth_verified') is not False:raise ValueError('Trajectory feedback/pose provenance changed')
    if plan.get('initialization')!={'objects':'loaded settled scene; no source-demo pose reset','robot':'spec initial_joint_positions','reset_from_demo':False,'randomization':False}:raise ValueError('Expert scene initialization changed')
    _demo_shapes(_local(root,plan['demo_path']))
    required={plan[k] for k in ('spec_path','scene_manifest_path','scene_path','scene_validation_path','demo_path','conversion_path','source_manifest_path')}
    if not required.issubset(plan.get('input_hashes',{})):raise ValueError('Plan input closure is incomplete')
    return plan
