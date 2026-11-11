"""Nero adapter for the official IsaacLab bimanual Mimic segment generator.

Uses the installed source tree read-only. No packages are installed or patched.
The physical execution adapter is project-specific, not the robosuite environment
released by NVlabs/dexmimicgen.
"""
import hashlib
import json
from pathlib import Path
import sys
import asyncio
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
from modules.environment.service import resolve
LAB = resolve(ROOT, 'isaac_lab_root')


def anchor_grasp_transit(source_pose, warped_pose, arm, source_frame):
    """Keep the far transit in the demonstrated world frame, fade to object frame.

    A large object yaw must not rotate the initial air pose into a folded,
    unreachable configuration. End the transition above the object, before the
    demonstrated final descent. This is a project adaptation of the official
    object-relative targets, not part of the official segment generator.
    """
    from sim_bridge.connector_planner import blend
    start,end = [(120,165),(0,75)][arm]
    fraction = np.clip((source_frame-start)/(end-start),0.,1.)
    return blend(source_pose,warped_pose,fraction),float(fraction*fraction*(3.-2.*fraction))


def official_imports():
    # Read the already installed pure-Python Lab dependencies; Isaac's native
    # NumPy/Torch take precedence. This does not activate or modify the env.
    import numpy
    import torch
    dependency_path = str(next((resolve(ROOT, 'training_python').parent.parent/'lib').glob('python*/site-packages')))
    if dependency_path not in sys.path:
        sys.path.append(dependency_path)
    for extension in sorted((LAB/'source').iterdir()):
        if not extension.is_dir():
            continue
        path = str(extension)
        if path not in sys.path:
            sys.path.insert(0, path)
    from isaaclab.envs import MimicEnvCfg, SubTaskConfig, SubTaskConstraintConfig
    from isaaclab_mimic.datagen.data_generator import DataGenerator
    from isaaclab_mimic.datagen.datagen_info_pool import DataGenInfoPool
    from isaaclab.utils.datasets import EpisodeData
    path = Path(sys.modules[DataGenerator.__module__].__file__)
    return dict(isaaclab_version=(LAB/'VERSION').read_text().strip(),
                generator_file=str(path), generator_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                backend='Official IsaacLab Mimic bimanual DataGenerator; project-specific Nero PhysX adapter')


class NeroMimicAdapter:
    """Small environment interface for official segment generation and stitching.

    Executes with the existing PhysX controller, rather than DataGenerator.generate's
    ManagerBasedRL recording/action queue loop. Segments, selection, transforms,
    coordination sharing and waypoint interpolation are the official implementation.
    """
    names = ('left', 'right')

    def __init__(self, get_tools, get_objects, output,canonical_grasp=False,source_path=None):
        self.provenance = official_imports()
        import torch
        from isaaclab.envs import (MimicEnvCfg, SubTaskConfig, SubTaskConstraintConfig,
                                  SubTaskConstraintType, SubTaskConstraintCoordinationScheme)
        from isaaclab_mimic.datagen.data_generator import DataGenerator
        from isaaclab_mimic.datagen.datagen_info_pool import DataGenInfoPool
        from isaaclab.utils.datasets import EpisodeData, HDF5DatasetFileHandler
        self.torch = torch
        self.device = 'cpu'
        self.get_tools, self.get_objects = get_tools, get_objects
        self.canonical_grasp=canonical_grasp
        self.segment_index=None
        self.source_path=Path(source_path).resolve() if source_path is not None else ROOT/'data/reference/connector/converted/episode_000.npz'
        self.source_path.relative_to(ROOT)
        self.source = np.load(self.source_path,allow_pickle=False)
        self.cfg = MimicEnvCfg()
        self.cfg.datagen_config.generation_select_src_per_arm = False
        self.cfg.datagen_config.generation_select_src_per_subtask = False
        self.cfg.datagen_config.generation_interpolate_from_last_target_pose = False
        self.cfg.subtask_configs = {name: [
            SubTaskConfig(object_ref=f'connector{a+1}', subtask_term_signal='both_lifted',
                          action_noise=0., num_interpolation_steps=30),
            SubTaskConfig(object_ref='connector2', subtask_term_signal='before_release',
                          action_noise=0., num_interpolation_steps=60),
            SubTaskConfig(object_ref=None, action_noise=0., num_interpolation_steps=15),
        ] for a, name in enumerate(self.names)}
        constraint = SubTaskConstraintConfig(
            eef_subtask_constraint_tuple=[('right',1),('left',1)],
            constraint_type=SubTaskConstraintType.COORDINATION,
            # Keep source wrist orientations in the cooperative segment. This
            # official scheme also avoids a missing get_delta_object_pose helper
            # in this locally installed Lab source tree's TRANSFORM branch.
            coordination_scheme=SubTaskConstraintCoordinationScheme.TRANSLATE,
            coordination_synchronize_start=True)
        self.cfg.task_constraint_configs = [constraint]
        self.constraints = constraint.generate_runtime_subtask_constraints()
        self.selected = {n: None for n in self.names}
        self.previous = {n: None for n in self.names}
        tensor = lambda v: torch.tensor(np.array(v), dtype=torch.float32)
        N = len(self.source['time'])
        poses = {n: tensor(self.source['eef_poses'][:,a]) for a,n in enumerate(self.names)}
        signals = {name: tensor(np.arange(N)>=frame).unsqueeze(-1)
                   for name,frame in [('both_lifted',239),('before_release',354)]}
        ep = EpisodeData()
        ep.data = dict(actions=tensor(self.source['widths']), obs=dict(datagen_info=dict(
            eef_pose=poses, target_eef_pose=poses,
            object_pose={f'connector{a+1}':tensor(self.source['object_poses'][:,a]) for a in range(2)},
            subtask_term_signals=signals)))
        self.pool = DataGenInfoPool(self, self.cfg, self.device)
        asyncio.run(self.pool.add_episode(ep))
        self.generator = DataGenerator(self, src_demo_datagen_info_pool=self.pool)
        self.boundaries = self.generator.randomize_subtask_boundaries()
        # A portable official-schema source dataset, including annotation assumptions.
        handler = HDF5DatasetFileHandler()
        handler.create(str(output/'source_demo.hdf5'), env_name='Nero-Connector-PhysX-v0')
        handler.write_episode(ep)
        handler.close()
        self.provenance['subtask_boundaries'] = {k:v.tolist() for k,v in self.boundaries.items()}
        self.provenance['coordination_scheme'] = 'Official TRANSLATE: same translation for both arms; source cooperative wrist orientations retained'

    def get_object_poses(self, env_ids=None):
        poses=self.get_objects()
        if self.canonical_grasp and self.segment_index==0:
            # Tabletop roll changes the body resting face, not the direction
            # from which a parallel jaw should descend onto its rear barrel.
            # This affects only the grasp reference. Actual full orientation is
            # retained by perception and later used for keyed coordination.
            from sim_bridge.connector_robust import canonical
            poses=[canonical(p) for p in poses]
        return {f'connector{a+1}':self.torch.tensor(p[None],dtype=self.torch.float32)
                for a,p in enumerate(poses)}

    def get_robot_eef_pose(self, env_ids=None, eef_name=None):
        p = self.get_tools(self.names.index(eef_name))
        return self.torch.tensor(p[None], dtype=self.torch.float32)

    def actions_to_gripper_actions(self, actions):
        return {n:actions[:,a:a+1] for a,n in enumerate(self.names)}

    def segment(self, index):
        self.segment_index=index
        trajectories = {}
        # The female arm establishes the shared coordination transform first.
        for name in ('right','left'):
            trajectory = self.generator.generate_eef_subtask_trajectory(
                0, name, index, self.boundaries, self.constraints, self.selected)
            waypoints = self.generator.merge_eef_subtask_trajectory(
                0, name, index, self.previous[name], trajectory)
            self.previous[name] = waypoints
            trajectories[name] = waypoints
        lengths = [len(trajectories[n]) for n in self.names]
        if len(set(lengths)) != 1:
            raise RuntimeError(f'Expected synchronized segment lengths: {lengths}')
        poses = np.array([[trajectories[n][i].pose.numpy() for n in self.names]
                          for i in range(lengths[0])])
        widths = np.array([[float(trajectories[n][i].gripper_action.item()) for n in self.names]
                           for i in range(lengths[0])])
        return poses, widths


def attach_calibrated_cameras(stage, arms):
    """Optical pose/intrinsics are calibrated; housing dimensions are approximate."""
    from pxr import Gf, UsdGeom, UsdPhysics
    from sim_bridge.connector_calibration import calibration
    K, T_G_C = calibration()
    optical_to_usd = np.diag([1.,-1.,-1.,1.])
    audit = []
    for state in arms:
        root = state['root']+'/gripper_base/CalibratedCamera'
        mount = UsdGeom.Xform.Define(stage, root)
        mount.AddTransformOp().Set(Gf.Matrix4d((T_G_C@optical_to_usd).T.tolist()))
        camera = UsdGeom.Camera.Define(stage, root+'/Color')
        focal = 20.
        camera.CreateFocalLengthAttr(focal)
        camera.CreateHorizontalApertureAttr(640*focal/K[0,0])
        camera.CreateVerticalApertureAttr(480*focal/K[1,1])
        camera.CreateHorizontalApertureOffsetAttr((320-K[0,2])*focal/K[0,0])
        camera.CreateVerticalApertureOffsetAttr((K[1,2]-240)*focal/K[1,1])
        camera.CreateClippingRangeAttr(Gf.Vec2f(.005,10.))
        body = UsdGeom.Cube.Define(stage, root+'/ApproximateHousing')
        body.AddTranslateOp().Set(Gf.Vec3d(0.,0.,.015))
        body.AddScaleOp().Set(Gf.Vec3f(.09,.025,.025))
        body.CreateSizeAttr(1.)
        body.CreateDisplayColorAttr([Gf.Vec3f(.14,.16,.19)])
        UsdPhysics.CollisionAPI.Apply(body.GetPrim())
        # Independently verify USD projection signs, optical/USD axes and K.
        projection = np.array(camera.GetCamera().frustum.ComputeProjectionMatrix())
        optical = np.array([[0.,0.,1.],[.1,.05,1.],[-.05,-.1,.8]])
        clip = np.c_[optical*np.array([1.,-1.,-1.]),np.ones(3)]@projection
        ndc = clip[:,:2]/clip[:,3:4]
        pixels = (ndc*np.array([1.,-1.])+1)*np.array([320,240])
        expected = optical@K.T
        expected = expected[:,:2]/expected[:,2:3]
        if not np.allclose(pixels,expected,atol=1e-3):
            raise RuntimeError(f'Calibrated USD camera projection mismatch: {pixels-expected}')
        audit.append(dict(camera_path=root+'/Color',K=K.tolist(),
                          maximum_projection_error_px=float(np.max(np.abs(pixels-expected))),
                          optical_to_usd_axes=[1,-1,-1],housing_geometry_measured=False))
    return audit


def execute_trial(sim, report, output, reset=True):
    """Execute official generated targets with independent friction-held bodies."""
    from sim_bridge.connector_planner import BASES, TABLE, quat, insertion_error
    from scipy.spatial.transform import Rotation
    source_path=Path(getattr(sim,'demo_path',ROOT/'data/reference/connector/converted/episode_000.npz')).resolve()
    source_path.relative_to(ROOT)
    source = np.load(source_path,allow_pickle=False)
    report['execution_goal']=sim.args.stop_after
    report['assembly_attempted']=False
    report['placement_mode'] = 'randomized initialization' if reset else 'current simulator poses'
    if reset:
        offsets = np.array([sim.args.left_offset,sim.args.right_offset])+sim.rng.uniform(-sim.args.spread,sim.args.spread,(2,2))
        yaws = np.array([sim.args.left_yaw,sim.args.right_yaw])+sim.rng.uniform(-sim.args.yaw_spread,sim.args.yaw_spread,2)
        report['requested_placement_variation'] = dict(xy_offsets_m=offsets.tolist(),yaw_offsets_deg=yaws.tolist())
        for a, obj in enumerate(sim.objects):
            pose = source['initial_objects'][a].copy()
            pose[:2,3] += offsets[a]
            pose[2,3] = TABLE+.010
            pose[:3,:3] = Rotation.from_euler('z',yaws[a],degrees=True).as_matrix()@pose[:3,:3]
            obj.set_world_pose(pose[:3,3],quat(pose))
            obj.set_linear_velocity(np.zeros(3)); obj.set_angular_velocity(np.zeros(3))
            # Start from the demonstrated world pose; object-frame deformation
            # is introduced smoothly during the airborne approach.
            target = source['eef_poses'][0,a]
            q = sim.ik.solve_near_demo(target,a,source['joints'][0,a],source['joints'][0,a])
            state = sim.arms[a]
            state['command'] = q.copy();state['opening'] = max(float(source['widths'][0,a]),[.055,.04][a])
            state['jaw_command'] = np.array([state['opening']/2,-state['opening']/2])
            full = np.zeros(state['robot'].num_dof)
            full[state['indices']] = q;full[state['fingers']] = state['jaw_command']
            state['robot'].set_joint_positions(full)
            state['robot'].set_joint_velocities(np.zeros_like(full))
        if getattr(sim,'keyed_mechanics',None) is not None:
            sim.keyed_mechanics.reset(sim.object_poses()[0])
    sim.wait(1.)
    initial = sim.object_poses()
    report['initial_object_poses'] = np.array(initial).tolist()
    if sim.rgbd is not None:
        sim.rgbd.save(output/'rgbd_initial')
        # Truth is written separately for offline evaluation, never in an RGBD frame.
        (output/'rgbd_evaluation_truth.json').write_text(json.dumps(dict(
            time=sim.time(), T_world_object=np.asarray(initial).tolist()), indent=2))
    if sim.args.rgbd_capture_only:
        report.update(passed=True, diagnostic_only=True, assembly_attempted=False,
                      rgbd_source='Rendered wrist RGB and optical-Z depth with measured-joint FK')
        return
    if sim.args.robust:
        from sim_bridge.connector_robust import RobustSession
        sim.robust=RobustSession(sim,output=output,
            objects=None if sim.args.perception=='rgbd' else initial,tools=[sim.tool(a) for a in range(2)])
        observed_initial=sim.robust.observe()['poses'].copy()
        report['initial_plan_pose_source']=getattr(sim.robust.sensor,'source','Noisy delayed pose estimate')
        if hasattr(sim.robust,'buttons'):
            for button in sim.robust.buttons:button.enabled=False
            sim.robust.status.text='Initial grasp: disturbances enabled after pickup'
    get_plan_objects=sim.object_poses if not sim.args.robust else lambda:sim.robust.observe()['poses']
    adapter = NeroMimicAdapter(sim.tool,get_plan_objects,output,
                              canonical_grasp=getattr(sim,'keyed_mechanics',None) is not None,source_path=source_path)
    report['generator'] = adapter.provenance
    if adapter.canonical_grasp:
        report['grasp_reference']='Tabletop yaw and position for rear-barrel approach; measured full roll retained for keyed coordination'
    report['gripper_control'] = 'Source closure timing; force closure command zero below 27mm (left),35mm (right); source widths are feedback, not motor targets'
    report['time_scale'] = 2.
    report['transit_adaptation'] = dict(method='World-anchored far transit, smooth blend to official object-relative grasp targets',
                                        source_transition_frames=dict(left=[120,165],right=[0,75]))
    report['camera_housing'] = '90x25x25 mm approximation, optical pose calibrated; actual housing/bracket geometry unavailable'
    sim.capture('initial')
    target_log,actual_log,ref_log = [],[],[]

    def run_segment(index):
        sim.set_phase(['dex_grasp','dex_coordination','dex_release'][index])
        poses,widths = adapter.segment(index)
        interp = [30,60,15][index]
        start = [0,240,355][index]
        if index==0:
            np.savez_compressed(output/'official_segment_0.npz',poses=poses.copy(),widths=widths)
            weights = np.zeros((len(poses),2))
            for k in range(len(poses)):
                frame = int(np.clip(k-interp-1,0,239))
                for a in range(2):
                    poses[k,a],weights[k,a] = anchor_grasp_transit(source['eef_poses'][frame,a],poses[k,a],a,frame)
            np.savez_compressed(output/'transit_weights.npz',weights=weights)
        np.savez_compressed(output/f'generated_segment_{index}.npz',poses=poses,widths=widths)
        if index==1 and sim.args.dex_contact_refine:
            from sim_bridge.connector_coordinator import run_cooperative_feedback
            frames = np.clip(240+np.maximum(0,np.arange(len(poses))-60),0,354).astype(int)
            feedback,targets,actual,qrefs = run_cooperative_feedback(sim,poses,source['joints'][frames],output)
            report['cooperative_feedback'] = feedback
            target_log.extend(targets)
            actual_log.extend(actual)
            ref_log.extend(qrefs)
            return
        grasp_dwelled = [False,False]
        for k, targets in enumerate(poses):
            frame = int(np.clip(start+max(0,k-interp-(1 if index==0 else 0)),0,554))
            for a,target in enumerate(targets):
                state = sim.arms[a]
                q = sim.ik.solve_near_demo(target,a,state['command'],source['joints'][frame,a])
                # Slow trajectory playback provides time for real contact dynamics.
                state['command'] += np.clip(q-state['command'],-1./15,1./15)
                width = widths[k,a]
                state['opening'] = 0. if width < [.027,.035][a] else max([.055,.04][a],float(width))
            for _ in range(16):
                sim.step()
            if index==0:
                for a in range(2):
                    if frame >= [210,106][a] and not grasp_dwelled[a]:
                        sim.wait(.35)
                        grasp_dwelled[a] = True
            target_log.append(targets);actual_log.append([sim.tool(a) for a in range(2)])
            ref_log.append(source['joints'][frame])
            if k%60==0:
                print(f'DEX_SEGMENT {index} waypoint={k}/{len(poses)}',flush=True)
        sim.wait(.4)
    try:
        run_segment(0)
        report['grasps'] = []
        for a in range(2):
            actual = sim.object_poses()[a]
            forces = np.linalg.norm(sim.force_matrix()[a*2:a*2+2,a],axis=-1)
            gap = float(-np.diff(sim.arms[a]['robot'].get_joint_positions(joint_indices=sim.arms[a]['fingers']))[0])
            lift = float(actual[2,3]-initial[a][2,3])
            success = bool(lift>.035 and .008<gap<.035 and np.all(forces>.2))
            if sim.args.robust:
                estimate=sim.robust.observe()
                estimated_lift=float(estimate['poses'][a,2,3]-observed_initial[a,2,3])
                success=bool(estimate['visible'][a] and estimated_lift>.035 and .008<gap<.035)
            report['grasps'].append(dict(arm=a,lift_m=lift,jaw_gap_m=gap,contact_forces_N=forces.tolist(),passed=success))
        sim.capture('both_grasped')
        print(f'DEX_GRASPS {report["grasps"]}',flush=True)
        if not all(g['passed'] for g in report['grasps']):
            raise RuntimeError('Demonstration-derived physical grasp failed; inspect contact diagnostics')
        if sim.args.stop_after=='grasp':
            report['passed'] = True
            report['completed_stage']='grasp'
            return
        report['assembly_attempted']=True
        if sim.args.robust:sim.robust.begin_held_phase()
        run_segment(1)
        if not sim.args.dex_contact_refine:
            sim.capture('preinsert')
        report['after_cooperative_motion'] = insertion_error(sim.object_poses())
        if getattr(sim,'keyed_mechanics',None) is not None:
            from sim_bridge.connector_keying import keyed_error,keyed_success
            report['after_cooperative_motion']=keyed_error(sim.object_poses())
            report['keyed_contact']=sim.keyed_mechanics.evaluate(sim.object_poses())
            # Evaluation only: do not use privileged spring state to choose a
            # motion. The hand-off/placement decisions below use RGBD poses.
        if sim.args.dex_contact_refine:
            if sim.args.stop_after!='insert':
                if getattr(sim,'keyed_mechanics',None) is not None:
                    from sim_bridge.connector_keying import release_keyed_pair
                    release_keyed_pair(sim,report)
                else:place_pair(sim, report)
            elif getattr(sim,'keyed_mechanics',None) is not None:
                report['keyed_assembly_validated']=report['keyed_contact']['engaged']
                if not report['keyed_assembly_validated']:
                    raise RuntimeError(f'Keyed insertion evaluation failed: {report["keyed_contact"]}')
            report['completed_stage']=sim.args.stop_after
            report['passed'] = True
            return
        report['after_source_coordination'] = report['after_cooperative_motion']
        if sim.args.stop_after=='insert':
            e = report['after_source_coordination']
            report['passed'] = bool(e['depth_m']>.008 and e['lateral_error_m']<.002 and e['angle_rad']<.05)
            if not report['passed']:
                raise RuntimeError(f'Official warped source segment did not mate the physical models: {e}')
            report['completed_stage']='insert'
            return
        run_segment(2)
        sim.wait(2.)
        report['after_release'] = insertion_error(sim.object_poses())
        e = report['after_release']
        report['settled_on_table'] = bool(all(TABLE+.006<p[2,3]<TABLE+.014 for p in sim.object_poses()))
        report['released_from_fingers'] = bool(np.max(np.linalg.norm(sim.force_matrix(),axis=-1))<.1)
        report['passed'] = bool(e['depth_m']>.008 and e['lateral_error_m']<.002 and e['angle_rad']<.05 and report['settled_on_table'] and report['released_from_fingers'])
        if report['passed']:report['completed_stage']='place'
        sim.capture('released')
    finally:
        if hasattr(sim,'robust'):
            phase_guard=getattr(sim.robust,'phase_guard',None)
            if phase_guard and not report.get('passed'):
                import sys
                active_error=sys.exc_info()[1]
                phase_guard.abandoned=True;sim.robust.needs_human=False
                phase_guard.reason=phase_guard.reason or str(active_error or 'Task did not complete')
                if active_error is not None and not isinstance(active_error,(InterruptedError,KeyboardInterrupt)):
                    phase_guard.finish_failure()
            report['recovery_policy']=sim.robust.save()
            report['requires_human']=sim.robust.needs_human
            display=getattr(sim.robust.sensor,'display',None)
            if display:
                if report.get('passed'):
                    message={'grasp':'Grasp-only demonstration completed; insertion was not requested',
                             'insert':'Grasp and insertion completed; placement was not requested',
                             'place':'FULL task completed: grasp, insertion, release and retreat',
                             'release':'FULL task completed: keyed insertion and direct jaw release (no lowering)'}
                    if report.get('completed_stage')=='release' and report.get('post_release_retained') is False:
                        message['release']='Insertion and jaw release completed | POST-DROP LATCH RETENTION FAILED'
                    display.set_status(message.get(report.get('completed_stage'),'Run completed'))
                else:display.set_status('Task did not complete; inspect the task result and console')
        if target_log:
            target,actual = np.array(target_log),np.array(actual_log)
            translation = np.linalg.norm(target[:,:,:3,3]-actual[:,:,:3,3],axis=-1)
            rot = Rotation.from_matrix((target[:,:,:3,:3].transpose(0,1,3,2)@actual[:,:,:3,:3]).reshape(-1,3,3)).magnitude()
            report['tracking'] = dict(position_rmse_m=float(np.sqrt(np.mean(translation**2))),
                                       position_max_m=float(translation.max()),rotation_rmse_rad=float(np.sqrt(np.mean(rot**2))))
            np.savez_compressed(output/'tracking.npz',targets=target,actual=actual,joint_reference=np.array(ref_log))


def place_pair(sim, report):
    """Place the already inserted pair; arm motions only, no object constraints."""
    from sim_bridge.connector_planner import align_axis, insertion_error, TABLE
    # The original demo releases above the table. The simplified models have no
    # elastic latch, so lower before release instead of assuming a locked pair.
    sim.set_phase('feedback_lower_pair')
    if hasattr(sim,'robust'):
        sample=sim.robust.observe()
        if not np.all(sample['visible']):
            sim.robust.needs_human=True
            raise RuntimeError('Cannot lower the pair without valid visual observations')
        actual=[p.copy() for p in sample['poses']]
    else:actual=sim.object_poses()
    grips = [np.linalg.inv(sim.tool(a))@actual[a] for a in range(2)]
    # Keep the inserted pair rigidly aligned while making its shaft horizontal.
    # Otherwise one end hits the tabletop before both tool targets can settle.
    horizontal = actual[1][:3,0].copy()
    horizontal[2] = 0.
    horizontal /= np.linalg.norm(horizontal)
    level = align_axis(actual[1][:3,:3],horizontal)@actual[1][:3,:3].T
    anchor = actual[1][:3,3].copy()
    for p in actual:
        p[:3,3] = anchor+level@(p[:3,3]-anchor)
        p[:3,:3] = level@p[:3,:3]
    descent = min(p[2,3] for p in actual)-(TABLE+.011)
    for p in actual:p[2,3] -= descent
    sim.move([actual[a]@np.linalg.inv(grips[a]) for a in range(2)],3.5,tolerance=.003)
    sim.set_phase('release')
    for state in sim.arms:state['opening'] = .065
    sim.wait(1.5)
    sim.set_phase('retreat')
    targets = [sim.tool(a) for a in range(2)]
    for t in targets:t[2,3] += .09
    sim.move(targets,2.5)
    sim.wait(.5)
    report['after_release'] = insertion_error(sim.object_poses())
    report['settled_on_table'] = bool(all(TABLE+.006<p[2,3]<TABLE+.014 for p in sim.object_poses())
                                    and all(np.linalg.norm(o.get_linear_velocity())<.02 for o in sim.objects))
    report['released_from_fingers'] = bool(np.max(np.linalg.norm(sim.force_matrix(),axis=-1))<.1)
    e = report['after_release']
    if not (e['depth_m']>.008 and e['lateral_error_m']<.002 and e['angle_rad']<.05 and report['settled_on_table'] and report['released_from_fingers']):
        raise RuntimeError(f'Physical placement failed: {e}')
    report['release_extension'] = 'Level the assembled pair, lower onto table, then open; models do not simulate an elastic latch'
    sim.capture('released')
