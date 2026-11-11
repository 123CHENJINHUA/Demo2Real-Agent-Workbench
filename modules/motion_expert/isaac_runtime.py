"""Run a retained Connector expert on an already built, hash-bound USD scene.

Heavy imports are inside execute. This is a ground-truth diagnostic expert,
not RGBD, a learned policy, hardware execution, or a default-scene launcher.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import sys
import traceback
import numpy as np
from scipy.spatial.transform import Rotation

_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def execute(root,plan,out,app):
    root=Path(root).resolve();out=Path(out).resolve();out.relative_to(root);out.mkdir(parents=True,exist_ok=True)
    def local(value):
        path=(root/str(value)).resolve();path.relative_to(root)
        if not path.is_file():raise ValueError('Missing project input: '+str(value))
        return path
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if plan.get('schema_version')!='connector-expert-plan/v1' or plan.get('binding_scope')!='offline_contact_expert_only' or plan.get('expert_perception')!='simulator_ground_truth':
        raise ValueError('Unsupported expert plan version/scope/perception')
    spec_path=local(plan['spec_path']);manifest_path=local(plan['scene_manifest_path']);demo_path=local(plan['demo_path'])
    spec=json.loads(spec_path.read_text());manifest=json.loads(manifest_path.read_text())
    if manifest['task_spec_hash']!=spec['content_hash'] or manifest['spec_revision']!=spec['revision']:
        raise ValueError('Scene manifest does not match exact task spec revision/hash')
    if plan.get('task_spec_hash',spec['content_hash'])!=spec['content_hash']:raise ValueError('Plan task hash mismatch')
    for path,expected in plan.get('input_hashes',{}).items():
        if digest(local(path))!=expected:raise ValueError('Frozen plan input changed: '+path)
    scene_path=local(manifest['scene_path'])
    legacy=root/'tasks/connector/legacy'
    existing=sys.modules.get('sim_bridge')
    if existing and not Path(existing.__file__).resolve().is_relative_to(legacy):
        raise RuntimeError('Another sim_bridge package is already loaded')
    if str(legacy) not in sys.path:sys.path.insert(0,str(legacy))
    import omni.usd
    from pxr import Usd,UsdGeom,UsdPhysics,PhysxSchema,Sdf
    from isaacsim.core.api import World
    from isaacsim.core.prims import SingleArticulation,SingleRigidPrim,Articulation,RigidPrim
    from isaacsim.core.utils.types import ArticulationAction
    from sim_bridge.connector_planner import NeroIK,BASES,TABLE,blend,from_pose
    from sim_bridge.connector_dexmimic import execute_trial
    from sim_bridge.grasp_scene import configure_dual_grasp_contacts
    from tasks.connector.components.mechanics import KeyedMechanics
    # Strip process-local graph prims from a private flattened copy. Inactive
    # /Render ancestors would also prevent fresh graphs from being created.
    original=Usd.Stage.Open(str(scene_path));prepared=Usd.Stage.Open(original.Flatten());removed=[]
    for transient in ('/Render','/Orchestrator','/Replicator'):
        if prepared.GetPrimAtPath(transient):prepared.RemovePrim(transient);removed.append(transient)
    for prim in prepared.Traverse():
        for attr in prim.GetAttributes():
            value=attr.Get();values=[value] if isinstance(value,Sdf.AssetPath) else list(value) if type(value).__name__=='AssetPathArray' else []
            if not values:continue
            source_attr=original.GetPrimAtPath(prim.GetPath()).GetAttribute(attr.GetName());source_value=source_attr.Get() if source_attr else value
            source_values=[source_value] if isinstance(source_value,Sdf.AssetPath) else list(source_value) if type(source_value).__name__=='AssetPathArray' else values
            refs=[]
            for ref in source_values:
                if not ref.path:refs.append(ref);continue
                asset=Path(ref.resolvedPath or ref.path).resolve();asset.relative_to(root)
                if not asset.is_file():raise ValueError('Unresolved prepared asset '+str(asset))
                refs.append(Sdf.AssetPath(str(asset)))
            attr.Set(refs[0] if isinstance(value,Sdf.AssetPath) else refs)
    prepared_path=out/'runtime_scene.usdc';prepared.GetRootLayer().Export(str(prepared_path))
    preparation={'source_scene_path':str(scene_path.relative_to(root)),'prepared_scene_path':str(prepared_path.relative_to(root)),'removed_runtime_graph_roots':removed,'method':'flatten_remove_only_transient_render_roots','original_scene_sha256':digest(scene_path),'prepared_scene_sha256':digest(prepared_path)}
    save('scene_preparation.json',preparation)
    if omni.usd.get_context().open_stage(str(prepared_path)) is False:raise RuntimeError('Cannot open bound USD scene')
    app.update();stage=omni.usd.get_context().get_stage()
    if not stage or stage.GetRootLayer().realPath!=str(prepared_path):raise RuntimeError('The prepared scene was not loaded')
    loaded_layers=[]
    for layer in stage.GetUsedLayers():
        if layer.realPath:
            p=Path(layer.realPath).resolve();p.relative_to(root);loaded_layers.append(str(p.relative_to(root)))
    robot_by_id={r['id']:r for r in manifest['robots']}
    spec_arms={r['id']:r for r in spec['robot']['arms']}
    if set(robot_by_id)!={'left','right'}:raise ValueError('This expert requires the registered two-arm role mapping')
    for a,name in enumerate(('left','right')):
        pose=spec_arms[name]['base_pose']
        if not np.allclose(pose['position_m'],BASES[a],atol=1e-8) or not np.allclose(abs(np.array(pose['quaternion_wxyz'])),[1,0,0,0],atol=1e-8):
            raise ValueError('Retained IK does not support changed base transforms')
    if abs(spec['scene']['table']['position_m'][2]+spec['scene']['table']['size_m'][2]/2-TABLE)>1e-8:
        raise ValueError('Retained expert requires registered table height')
    roles={o['collision']['role']:o for o in spec['objects']}
    if set(roles)!={'male','female'} or len(spec['objects'])!=2:raise ValueError('Expected exactly the registered connector pair')
    object_specs=[roles['male'],roles['female']]
    object_paths=[f"/World/Objects/{o['id']}" for o in object_specs]
    for path in object_paths:
        prim=stage.GetPrimAtPath(path)
        if not prim or not prim.HasAPI(UsdPhysics.RigidBodyAPI):raise ValueError('Bound scene object missing: '+path)
    physics=[str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
    if len(physics)!=1:raise ValueError('Loaded scene must have exactly one physics scene')
    hz=int(spec['scene']['physics_hz'])
    if hz!=240:raise ValueError('Retained expert is fixed at 240 Hz')
    mesh_before={str(p.GetPath()):{'points':len(UsdGeom.Mesh(p).GetPointsAttr().Get() or []),
        'faces':len(UsdGeom.Mesh(p).GetFaceVertexCountsAttr().Get() or [])} for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Mesh)}
    world=World(stage_units_in_meters=1.,physics_dt=1/hz,rendering_dt=1/60,device='cpu',physics_prim_path=physics[0])
    world.get_physics_context().set_solver_type('TGS');world.get_physics_context().enable_ccd(True)
    arms=[]
    for name in ('left','right'):
        record=robot_by_id[name];robot=world.scene.add(SingleArticulation(record['articulation_path'],name='expert_'+name))
        arms.append(dict(robot=robot,root=record['root_path'],path=record['articulation_path'],role=name))
    objects=[world.scene.add(SingleRigidPrim(path,name='expert_object_'+str(a))) for a,path in enumerate(object_paths)]
    mechanics=KeyedMechanics.from_existing(stage,world,object_paths)
    overlay=dict(id='connector_contact_execution/v1',finger_drive_type='force',finger_stiffness_N_m=600.,
        finger_damping_Ns_m=10.,finger_max_force_N=10.,finger_contact_offset_m=.001,
        finger_friction={'static':1.2,'dynamic':1.},finger_mesh_approximation='convexDecomposition',
        body_angular_damping_per_s=5.,body_linear_damping_per_s=.1,body_sleep_threshold=.00001,
        gravity_compensation=True,solver_position_iterations=64,solver_velocity_iterations=16,
        geometry_rebuilt=False,object_pose_writes_during_execution=False)
    for key,value in plan.get('execution_overlay',{}).items():
        if key not in overlay or overlay[key]!=value:raise ValueError('Unsupported execution overlay value: '+key)
    configure_dual_grasp_contacts(world,[a['root'] for a in arms])
    for path in object_paths:
        api=PhysxSchema.PhysxRigidBodyAPI.Apply(stage.GetPrimAtPath(path))
        api.CreateAngularDampingAttr(5.);api.CreateLinearDampingAttr(.1);api.CreateSleepThresholdAttr(.00001)
    world.reset()
    for arm in arms:
        robot=arm['robot'];values=spec_arms[arm['role']]['initial_joint_positions'];names=list(robot.dof_names)
        full=np.array([float(values[n]) for n in names]);robot.set_joint_positions(full);robot.set_joint_velocities(np.zeros(robot.num_dof))
        arm['indices']=np.array([names.index('joint'+str(j)) for j in range(1,8)])
        arm['fingers']=np.array([names.index('gripper_joint'+str(j)) for j in (1,2)])
        arm['dynamics']=Articulation(prim_paths_expr=arm['path'],name='expert_dynamics_'+arm['role']);arm['dynamics'].initialize()
        gains=np.full(robot.num_dof,6000.);damping=np.full(robot.num_dof,300.);gains[arm['fingers']]=600.;damping[arm['fingers']]=10.
        control=robot.get_articulation_controller();control.set_gains(kps=gains,kds=damping);control.set_max_efforts(np.array([10.,10.]),joint_indices=arm['fingers'])
        robot.set_solver_position_iteration_count(64);robot.set_solver_velocity_iteration_count(16)
        arm['command']=full[arm['indices']].copy();arm['jaw_command']=full[arm['fingers']].copy();arm['opening']=float(-np.diff(arm['jaw_command'])[0])
    def poses():return np.array([from_pose(*o.get_world_pose()) for o in objects])
    initial=poses();pose_audit=[]
    for a,obj in enumerate(object_specs):
        expected=manifest['settled_poses'][obj['id']];p=np.array(expected['position_m']);q=np.array(expected['quaternion_wxyz'])
        T=from_pose(p,q);distance=float(np.linalg.norm(initial[a,:3,3]-p));angle=float(Rotation.from_matrix(T[:3,:3].T@initial[a,:3,:3]).magnitude())
        pose_audit.append(dict(id=obj['id'],path=object_paths[a],position_error_m=distance,rotation_error_rad=angle,
            manifest_pose=expected,loaded_pose=initial[a].tolist()))
        if distance>.0002 or angle>.01:raise ValueError('Loaded rigid pose differs from bound settled scene: '+obj['id'])
    meshes_after={str(p.GetPath()):{'points':len(UsdGeom.Mesh(p).GetPointsAttr().Get() or []),
        'faces':len(UsdGeom.Mesh(p).GetFaceVertexCountsAttr().Get() or [])} for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Mesh)}
    if meshes_after!=mesh_before:raise RuntimeError('Execution initialization unexpectedly changed scene mesh topology')
    audit=dict(loaded_scene_path=str(prepared_path.relative_to(root)),source_scene_path=str(scene_path.relative_to(root)),scene_preparation=preparation,loaded_layers=loaded_layers,
        object_poses=pose_audit,robot_initialization='spec named joint state; no object pose initialization',
        robot_joint_positions={a['role']:a['robot'].get_joint_positions().tolist() for a in arms},
        existing_object_paths=object_paths,existing_latch_paths=[c.prim_path for c in mechanics.clips],
        source_scene_scope=manifest.get('scope'),source_contact_execution_authorized=manifest.get('contact_execution_authorized'),
        plan_binding_scope=plan.get('binding_scope'),mesh_topology_unchanged=True,scene_rebuilt=False)
    save('initial_scene_audit.json',audit);save('execution_overlay.json',dict(requested=plan.get('execution_overlay'),applied=overlay))
    contacts=RigidPrim(prim_paths_expr=[a['root']+'/gripper_link'+str(j) for a in arms for j in (1,2)],
        name='expert_finger_contacts',contact_filter_prim_paths_expr=[object_paths for _ in range(4)],track_contact_forces=True,max_contact_count=1024);contacts.initialize()
    pair=RigidPrim(prim_paths_expr=object_paths,name='expert_pair_contacts',contact_filter_prim_paths_expr=[object_paths for _ in objects],track_contact_forces=True,max_contact_count=1024);pair.initialize()
    ik=NeroIK();tick=0;phase='initialization';records=[];captures=[]
    def tool(a):
        result=ik.fk(arms[a]['robot'].get_joint_positions(joint_indices=arms[a]['indices']));result[:3,3]+=BASES[a];return result
    def forces():return np.asarray(contacts.get_contact_force_matrix(dt=1/hz))
    def step():
        nonlocal tick
        if not app.is_running():raise InterruptedError('Expert application closed')
        for arm in arms:
            robot=arm['robot'];robot.set_joint_efforts(arm['dynamics'].get_generalized_gravity_forces()[0])
            arm['jaw_command']+=np.clip(np.array([arm['opening']/2,-arm['opening']/2])-arm['jaw_command'],-.025/hz,.025/hz)
            robot.apply_action(ArticulationAction(joint_positions=arm['command'],joint_indices=arm['indices']))
            robot.apply_action(ArticulationAction(joint_positions=arm['jaw_command'],joint_indices=arm['fingers']))
        world.step(render=False);tick+=1
        deflection=mechanics.record(poses()[0])
        if tick%4==0:
            records.append(dict(time=tick/hz,phase=phase,objects=poses(),tools=np.array([tool(a) for a in range(2)]),
                joints=np.array([a['robot'].get_joint_positions(joint_indices=a['indices']) for a in arms]),
                jaws=np.array([a['robot'].get_joint_positions(joint_indices=a['fingers']) for a in arms]),
                commanded_joints=np.array([a['command'].copy() for a in arms]),contact_forces_N=forces(),latch_deflections_m=deflection))
    def wait(seconds):
        for _ in range(round(seconds*hz)):step()
    def set_phase(value):
        nonlocal phase
        phase=value;print('EXPERT_EVENT '+json.dumps({'type':'phase','payload':{'phase':phase,'simulation_time_s':tick/hz}}),flush=True)
    def command(targets,warm_multistart=False):
        for a,target in enumerate(targets):
            if target is None:continue
            state=arms[a];q=ik.solve(target,a,state['command'],warm_multistart);state['command']+=np.clip(q-state['command'],-.6/30,.6/30)
    def move(targets,duration=2.,tolerance=.002):
        starts=[tool(a) for a in range(2)]
        for k in range(round(duration*30)+1):
            command([None if t is None else blend(starts[a],t,k/(duration*30)) for a,t in enumerate(targets)])
            for _ in range(8):step()
        for _ in range(180):
            errors=[np.linalg.norm(tool(a)[:3,3]-target[:3,3]) for a,target in enumerate(targets) if target is not None]
            if max(errors,default=0.)<tolerance:return
            command(targets)
            for _ in range(8):step()
        raise RuntimeError('Arm target did not settle: '+str(errors))
    def capture(name):
        # Render orchestration invalidates Isaac tensor handles. Keep execution
        # independent: save the exact phase USD; RGB rendering is a separate job.
        before=float(world.current_time);before_poses=poses()
        checkpoint=export_scene(name+'_checkpoint.usdc')
        if abs(float(world.current_time)-before)>1e-7 or not np.allclose(poses(),before_poses,atol=1e-7):
            raise RuntimeError('Checkpoint export changed physical state')
        captures.append(dict(phase=name,path=None,state_path=checkpoint,simulation_time_s=tick/hz,source='physical_USD_checkpoint_RGB_not_rendered'))
    args=SimpleNamespace(stop_after='release',robust=False,rgbd_capture_only=False,perception='simulator_ground_truth',
        dex_contact_refine=True,connector_model='keyed')
    sim=SimpleNamespace(args=args,arms=arms,objects=objects,ik=ik,tool=tool,object_poses=poses,
        force_matrix=forces,step=step,wait=wait,move=move,command=command,set_phase=set_phase,
        time=lambda:tick/hz,phase=lambda:phase,rgbd=None,keyed_mechanics=mechanics,demo_path=demo_path,object_contacts=pair,capture=capture)
    report=dict(passed=False,expert_perception='simulator_ground_truth',scene_rebuilt=False,initial_object_poses=initial.tolist())
    plan_snapshot=out/'execution_plan.json';plan_snapshot.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    export_adaptations={}
    def export_scene(name):
        path=out/name;stage.Flatten().Export(str(path));saved=Usd.Stage.Open(str(path))
        from modules.scene_building.usd_portability import make_portable_materials
        export_adaptations[name]=make_portable_materials(saved)
        for prim in saved.Traverse():
            for attr in prim.GetAttributes():
                value=attr.Get()
                is_array=type(value).__name__=='AssetPathArray'
                values=list(value) if is_array else [value] if isinstance(value,Sdf.AssetPath) else []
                if not values:continue
                original_prim=stage.GetPrimAtPath(prim.GetPath())
                original=original_prim.GetAttribute(attr.GetName()) if original_prim else None
                old=original.Get() if original else None
                old_values=list(old) if type(old).__name__=='AssetPathArray' else [old]
                rewritten=[]
                for i,item in enumerate(values):
                    if not item.path:rewritten.append(item);continue
                    source=old_values[i] if i<len(old_values) and isinstance(old_values[i],Sdf.AssetPath) else item
                    candidate=Path(getattr(item,'resolvedPath','') or getattr(source,'resolvedPath','') or item.path)
                    if not candidate.is_absolute():candidate=scene_path.parent/candidate
                    candidate=candidate.resolve();candidate.relative_to(root)
                    if not candidate.is_file():raise ValueError('Unresolved exported scene asset '+str(candidate))
                    rewritten.append(Sdf.AssetPath(os.path.relpath(candidate,out)))
                attr.Set(Sdf.AssetPathArray(rewritten) if is_array else rewritten[0])
        saved.GetRootLayer().Save();return str(path.relative_to(root))
    initial_scene=export_scene('execution_initial.usdc')
    try:execute_trial(sim,report,out,reset=False)
    except Exception as exc:
        report['passed']=False;report['error']=str(exc);report['error_type']=type(exc).__name__;report['traceback']=traceback.format_exc();print(report['traceback'],flush=True)
    finally:
        world.pause()
        if records:np.savez_compressed(out/'trajectory.npz',**{k:np.array([r[k] for r in records]) for k in records[0]})
        save('trial_result.json',report)
    final_scene=export_scene('execution_final.usdc')
    hashes={}
    for module in list(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if not filename:continue
        p=Path(filename).resolve()
        if p.suffix=='.py' and p.is_relative_to(root) and (p.is_relative_to(legacy) or p.is_relative_to(root/'tasks/connector/components') or p==Path(__file__).resolve()):hashes[str(p.relative_to(root))]=_SOURCE_SHA256 if p==Path(__file__).resolve() else digest(p)
    result=dict(status='succeeded' if report['passed'] else 'failed',action_passed=bool(report['passed']),
        full_task_passed=bool(report['passed'] and report.get('post_release_validation') is True),
        retention_passed=report.get('post_release_retained'),report=report,trajectory_path=str((out/'trajectory.npz').relative_to(root)) if records else None,
        plan_path=str(plan_snapshot.relative_to(root)),task_spec_hash=spec['content_hash'],spec_revision=spec['revision'],
        scene_manifest_path=str(manifest_path.relative_to(root)),loaded_scene_path=str(prepared_path.relative_to(root)),source_scene_path=str(scene_path.relative_to(root)),scene_preparation=preparation,
        execution_scene_path=final_scene,initial_execution_scene_path=initial_scene,
        execution_overlay=overlay,initial_scene_audit=audit,hardware_commands_sent=0,expert_perception='simulator_ground_truth',
        source_sha256=hashes,loaded_runtime_sha256=_SOURCE_SHA256,export_material_adaptations=export_adaptations,captures=captures,rgbd_executed=False,learned_policy_executed=False,scene_rebuilt=False)
    save('expert_result.json',result);return result
