"""Adaptive dual Nero connector insertion with independent PhysX rigid bodies.

No object parenting, fixed grasp joints, pose animation or run-time teleporting.
Object poses are read from the simulator; this is not a vision estimator.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[4]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--headless", action="store_true")
parser.add_argument("--interactive", action="store_true")
parser.add_argument("--trials", type=int, default=1)
parser.add_argument("--spread", type=float, default=0., help="Independent XY placement variation in metres")
parser.add_argument("--yaw-spread", type=float, default=0., help="Independent yaw variation in degrees")
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--left-offset", nargs=2, type=float, default=[0., 0.])
parser.add_argument("--right-offset", nargs=2, type=float, default=[0., 0.])
parser.add_argument("--left-yaw", type=float, default=0.)
parser.add_argument("--right-yaw", type=float, default=0.)
parser.add_argument("--output-dir", default="output/connector_physics/run_"+time.strftime("%Y%m%d_%H%M%S"))
parser.add_argument("--capture", action="store_true", help="Save rendered RGB images at phase boundaries")
parser.add_argument("--stop-after", choices=["grasp", "insert", "place", "release"], default="place")
parser.add_argument("--controller", choices=["adaptive", "dexmimic"], default="adaptive")
parser.add_argument("--probe-mimic", action="store_true", help="Check official IsaacLab Mimic imports, then exit")
parser.add_argument("--dex-contact-refine", action="store_true", help="Continuously correct object-relative pose through keyed insertion and direct jaw release")
parser.add_argument('--robust',action='store_true',help='Enable pose/proprioceptive recovery supervision and training records')
parser.add_argument('--disturbance',choices=['none','push','drop','relocate','occlude','remove'],default='none')
parser.add_argument('--disturb-arm',type=int,choices=[0,1],default=0)
parser.add_argument('--disturb-after',type=float,default=1.5,help='Seconds after cooperative motion starts')
parser.add_argument('--disturb-shift',type=float,nargs=2,default=[.04,.025])
parser.add_argument('--pose-noise',type=float,default=.0003)
parser.add_argument('--pose-delay',type=float,default=.066)
parser.add_argument('--supervisor-policy',type=str,help='Learned recovery supervisor NPZ, no force inputs')
parser.add_argument('--perception', choices=['surrogate', 'rgbd'], default='surrogate')
parser.add_argument('--rgbd-capture-only', action='store_true', help='Only acquire calibrated wrist RGBD at the initial demonstration posture')
parser.add_argument('--rgbd-hz', type=float, default=15., help='Vision updates per simulated second; GPU inference may run slower than real time')
parser.add_argument('--rgbd-record', action='store_true', help='Record wrist RGBD during a separately labelled ground-truth-controller diagnostic')
parser.add_argument('--rgbd-depth-noise', type=float, default=0., help='Synthetic optical-Z Gaussian noise std in metres, not a calibrated hardware model')
parser.add_argument('--rgbd-depth-dropout', type=float, default=0., help='Independent synthetic invalid-depth pixel probability')
parser.add_argument('--rgbd-min-depth', type=float, default=.005, help='Valid depth near limit in metres; set from hardware measurements')
parser.add_argument('--rgbd-max-depth', type=float, default=10.)
parser.add_argument('--rgbd-visualize',action='store_true',help='Save SAM3 masks and FoundationPose mesh panels (GUI enabled by default without --headless)')
parser.add_argument('--rgbd-no-visualize',action='store_true',help='Disable the perception panel and mesh visualization')
parser.add_argument('--connector-model',choices=['legacy','keyed'],default='legacy',help='Mechanical collision/controller model; RGBD launcher selects keyed')
parser.add_argument('--keyed-bench',action='store_true',help='Run isolated physical latch mechanism tests without robots')
args, kit_args = parser.parse_known_args()
if args.connector_model=='keyed' and args.stop_after=='place':
    # Backward-compatible old command: keyed tasks now end by direct release.
    args.stop_after='release'
if args.stop_after=='release' and args.connector_model!='keyed':
    parser.error('--stop-after release requires --connector-model keyed')
out = (ROOT/args.output_dir).resolve()
out.relative_to(ROOT)
if args.trials < 1 or not 0 <= args.spread <= .05 or not 0 <= args.yaw_spread <= 30:
    parser.error("trials >= 1, spread in [0, .05] m, yaw-spread in [0, 30] degrees required")
if args.disturbance!='none' and not args.robust:parser.error('--disturbance requires --robust')
if args.connector_model=='keyed' and not args.keyed_bench and (args.controller!='dexmimic' or not args.dex_contact_refine):
    parser.error('The keyed model requires --controller dexmimic --dex-contact-refine')
if (args.perception=='rgbd' or args.rgbd_capture_only or args.rgbd_record) and args.controller!='dexmimic':
    parser.error('Wrist RGBD requires --controller dexmimic')
if args.perception=='rgbd' and not args.robust:
    parser.error('--perception rgbd requires --robust to exclude ground-truth control')
if not 1<=args.rgbd_hz<=30:
    parser.error('--rgbd-hz must be in [1,30]')
if not (0<=args.rgbd_depth_noise<=.005 and 0<=args.rgbd_depth_dropout<=.5 and 0<args.rgbd_min_depth<args.rgbd_max_depth<=10):
    parser.error('Invalid RGBD depth noise, dropout or range')
if not 0<=args.pose_noise<=.005 or not 0<=args.pose_delay<=.5:parser.error('Pose noise/delay outside supported limits')
if out.exists() and any(out.iterdir()):
    parser.error("Use a new output directory")
out.mkdir(parents=True, exist_ok=True)
runtime = ROOT/"output/connector_physics/runtime"
sys.argv = [sys.argv[0], *kit_args]
from isaacsim import SimulationApp
app = SimulationApp(dict(headless=args.headless, multi_gpu=False, width=1280, height=720,
                          enable_crashreporter=False, limit_cpu_threads=8, fast_shutdown=True, hide_ui=False,
                          extra_args=[f"--/log/file={out}/isaac.log", f"--/app/userConfigPath={runtime}/config/user.config.json",
                                      "--/app/settings/persistent=false", "--/app/settings/saveOnExit=false",
                                      "--/app/settings/loadUserConfig=false", "--/app/extensions/registryEnabled=false"]+
                                     (["--/app/hydraEngine/waitIdle=true",
                                       "--/app/updateOrder/checkForHydraRenderComplete=1000",
                                       "--/app/asyncRendering=false"]
                                      if args.perception=='rgbd' or args.rgbd_capture_only or args.rgbd_record else [])))


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))


def run():
    if args.keyed_bench:
        from sim_bridge.connector_keyed_bench import run_bench
        run_bench(app,args,out)
        return
    if args.probe_mimic:
        from sim_bridge.connector_dexmimic import official_imports
        save_json(out/'official_imports.json', official_imports())
        print('OFFICIAL_MIMIC_IMPORT_OK', flush=True)
        return
    import omni.kit.commands
    import omni.usd
    from isaacsim.core.api import World
    from isaacsim.core.api.objects import FixedCuboid
    from isaacsim.core.prims import Articulation, SingleArticulation, SingleRigidPrim, RigidPrim
    from isaacsim.core.utils.extensions import enable_extension
    from isaacsim.core.utils.stage import add_reference_to_stage
    from isaacsim.core.utils.types import ArticulationAction
    from isaacsim.core.utils.viewports import set_camera_view
    from isaacsim.asset.importer.urdf import _urdf
    from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade, Vt
    from sim_bridge.connector_planner import (ASSEMBLY_R, BASES, TABLE, SEEDS, GRIP_X, NeroIK, assembly_targets,
                                               align_axis, blend, from_pose, grasp_target, insertion_error, quat, transform)
    from sim_bridge.grasp_scene import configure_dual_grasp_contacts
    enable_extension("isaacsim.asset.importer.urdf")
    app.update()
    world = World(stage_units_in_meters=1., physics_dt=1/240, rendering_dt=1/60, device="cpu")
    context = world.get_physics_context()
    context.set_solver_type("TGS")
    context.enable_ccd(True)
    stage = omni.usd.get_context().get_stage()
    UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    UsdLux.DomeLight.Define(stage, "/World/Light").CreateIntensityAttr(600.)
    world.scene.add(FixedCuboid("/World/Table", name="table", size=1., position=np.array([-.20, 0., TABLE-.025]),
                                scale=np.array([.90, 1.20, .05]), color=np.array([.43, .47, .50])))
    world.scene.add(FixedCuboid("/World/Floor", name="floor", size=1., position=np.array([0., 0., -.025]),
                                scale=np.array([3., 3., .05]), color=np.array([.20, .23, .26])))
    for arm, base in enumerate(BASES):
        world.scene.add(FixedCuboid(f"/World/BasePlate{arm}", name=f"plate{arm}", size=1.,
                                    position=base-[0., 0., .011], scale=np.array([.16, .16, .022]), color=np.array([.85, .65, .035])))
    status, config = omni.kit.commands.execute("URDFCreateImportConfig")
    if not status:
        raise RuntimeError("URDF import config failed")
    config.merge_fixed_joints = False
    config.fix_base = True
    config.import_inertia_tensor = True
    config.distance_scale = 1.
    config.self_collision = False
    config.default_drive_strength = 6000.
    config.default_position_drive_damping = 300.
    config.default_drive_type = _urdf.UrdfJointTargetType.JOINT_DRIVE_POSITION
    arms = []
    for arm in range(2):
        stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
        urdf = ROOT/("assets/nero/nero.urdf" if arm == 0 else "assets/nero/nero_dual_2.urdf")
        status, path = omni.kit.commands.execute("URDFParseAndImportFile", urdf_path=str(urdf), import_config=config, get_articulation_root=True)
        if not status:
            raise RuntimeError("URDF import failed")
        root = path.rsplit("/", 1)[0]
        prim = stage.GetPrimAtPath(root)
        xform = UsdGeom.Xformable(prim)
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(Gf.Vec3d(*BASES[arm].tolist()))
        robot = world.scene.add(SingleArticulation(prim_path=path, name=f"nero{arm}"))
        arms.append(dict(robot=robot, root=root, path=path))
    # The importer changes defaultPrim. Normalize it before physics views exist;
    # changing it later invalidates the importer's world-fixed root joint.
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    app.update()
    configure_dual_grasp_contacts(world, [a["root"] for a in arms])
    material = UsdShade.Material.Get(stage, "/World/DualGraspMaterial")

    def collision(prim):
        UsdPhysics.CollisionAPI.Apply(prim)
        contact = PhysxSchema.PhysxCollisionAPI.Apply(prim)
        contact.CreateContactOffsetAttr(.0003)
        contact.CreateRestOffsetAttr(0.)
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, materialPurpose="physics")

    def cylinder(path, x0, x1, radius):
        shape = UsdGeom.Cylinder.Define(stage, path)
        shape.CreateAxisAttr("X")
        shape.CreateHeightAttr(x1-x0)
        shape.CreateRadiusAttr(radius)
        shape.AddTranslateOp().Set(Gf.Vec3d((x0+x1)/2, 0., 0.))
        shape.CreateVisibilityAttr("invisible")
        collision(shape.GetPrim())

    def model_barrel(path):
        # Retain the scan's flat sides. A perfect circular proxy loses the
        # actual resting face and can roll into the approaching finger.
        from scipy.spatial import ConvexHull
        source = next((ROOT/'assets/objects/connector/model/1').glob('*lowpoly.obj'))
        vertices = np.array([[float(v) for v in line.split()[1:4]]
                             for line in source.read_text().splitlines() if line.startswith('v ')])
        section = np.unique(np.round(vertices[vertices[:,0]>=-.009,1:],4),axis=0)
        section = section[ConvexHull(section).vertices]
        points = np.array([(x,*yz) for x in (-.009,.028) for yz in section])
        hull = ConvexHull(points)
        shape = UsdGeom.Mesh.Define(stage,path)
        shape.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)))
        shape.CreateFaceVertexCountsAttr([3]*len(hull.simplices))
        shape.CreateFaceVertexIndicesAttr(hull.simplices.ravel().tolist())
        shape.CreateSubdivisionSchemeAttr('none')
        shape.CreateVisibilityAttr('invisible')
        collision(shape.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(shape.GetPrim()).CreateApproximationAttr('convexHull')

    objects = []
    keyed_mechanics = None
    for arm in range(2):
        root = f"/World/Connector{arm+1}"
        body = UsdGeom.Xform.Define(stage, root).GetPrim()
        visual = root+"/VisualAsset"
        asset = ROOT/f"assets/connector_visuals/connector_{arm+1}.usdc"
        if not asset.is_file():
            raise FileNotFoundError("Build the visual assets first: bash scripts/connector_replay.sh build")
        add_reference_to_stage(str(asset), visual)
        UsdPhysics.RigidBodyAPI.Apply(body).CreateRigidBodyEnabledAttr(True)
        UsdPhysics.MassAPI.Apply(body).CreateMassAttr(.020 if arm == 0 else .025)
        rb = PhysxSchema.PhysxRigidBodyAPI.Apply(body)
        rb.CreateSolverPositionIterationCountAttr(64)
        rb.CreateSolverVelocityIterationCountAttr(16)
        rb.CreateEnableCCDAttr(True)
        rb.CreateSleepThresholdAttr(.00001)
        if args.controller == 'dexmimic':
            # Primitive cylinder/table contacts otherwise accumulate slow spin
            # during the long demonstrated waiting phase. Passive drag, not a
            # pose constraint; bodies remain free to translate/rotate/drop.
            rb.CreateAngularDampingAttr(5.)
            rb.CreateLinearDampingAttr(.1)
        if args.connector_model=='keyed':
            pass  # Built together below, including the two internal latch bodies.
        elif arm == 0:
            if args.controller=='dexmimic':
                model_barrel(root+'/BodyCollision')
            else:
                cylinder(root+"/BodyCollision", -.009, .028, .0092)
            cylinder(root+"/CollarCollision", -.012, -.009, .0075)
            cylinder(root+"/PinCollision", -.028, -.012, .0034)
        else:
            cylinder(root+"/RearCollision", -.029, .009, .0093)
            # Individual convex wedges preserve a 4.3 mm bore. A convex hull
            # over the complete connector would seal it and forbid insertion.
            for part in range(24):
                angles = np.array([part, part+1])*2*np.pi/24
                points = [(x, radius*np.cos(angle), radius*np.sin(angle)) for x in (.009, .029)
                          for radius in (.0043, .0093) for angle in angles]
                mesh = UsdGeom.Mesh.Define(stage, root+f"/SocketWall{part:02d}")
                mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.array(points, np.float32)))
                mesh.CreateFaceVertexCountsAttr([4]*6)
                mesh.CreateFaceVertexIndicesAttr([0, 1, 3, 2, 4, 6, 7, 5, 0, 4, 5, 1, 2, 3, 7, 6, 0, 2, 6, 4, 1, 5, 7, 3])
                mesh.CreateSubdivisionSchemeAttr("none")
                mesh.CreateVisibilityAttr("invisible")
                collision(mesh.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
        objects.append(world.scene.add(SingleRigidPrim(root, name=f"connector{arm}")))
    if args.connector_model=='keyed':
        from sim_bridge.connector_keyed_physics import KeyedMechanics
        keyed_mechanics=KeyedMechanics(stage,world,[o.prim_path for o in objects],material)
    ik = NeroIK()
    if args.controller == 'dexmimic':
        from sim_bridge.connector_dexmimic import attach_calibrated_cameras
        save_json(out/'camera_projection_audit.json', attach_calibrated_cameras(stage, arms))
    set_camera_view(eye=np.array([-1.15, -1.1, 1.6]), target=np.array([-.25, 0., .95]))
    camera = UsdGeom.Camera.Define(stage, "/World/AssemblyCamera")
    camera.CreateFocalLengthAttr(45.)
    camera.CreateClippingRangeAttr(Gf.Vec2f(.005, 10.))
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(-.66, -.01, 1.08), Gf.Vec3d(-.35, .02, .90), Gf.Vec3d(0., 0., 1.)).GetInverse())
    world.reset()
    for arm, state in enumerate(arms):
        robot = state["robot"]
        state["indices"] = np.array([robot.dof_names.index(f"joint{j}") for j in range(1, 8)])
        state["fingers"] = np.array([robot.dof_names.index(f"gripper_joint{j}") for j in (1, 2)])
        state["dynamics"] = Articulation(prim_paths_expr=state["path"], name=f"dynamics{arm}")
        state["dynamics"].initialize()
        gains = np.full(robot.num_dof, 6000.)
        damping = np.full(robot.num_dof, 300.)
        gains[state["fingers"]] = 600.
        damping[state["fingers"]] = 10.
        robot.get_articulation_controller().set_gains(kps=gains, kds=damping)
        robot.get_articulation_controller().set_max_efforts(np.array([10., 10.]), joint_indices=state["fingers"])
        robot.set_solver_position_iteration_count(64)
        robot.set_solver_velocity_iteration_count(16)
        state["command"] = SEEDS[arm].copy()
        state["opening"] = .06
        state["jaw_command"] = np.array([.03, -.03])
    contacts = RigidPrim(prim_paths_expr=[a["root"]+f"/gripper_link{j}" for a in arms for j in (1, 2)],
                         name="finger_contacts", contact_filter_prim_paths_expr=[[o.prim_path for o in objects] for _ in range(4)],
                         track_contact_forces=True, max_contact_count=512)
    contacts.initialize()
    object_contacts = RigidPrim(prim_paths_expr=[o.prim_path for o in objects], name="connector_contacts",
                                contact_filter_prim_paths_expr=[[o.prim_path for o in objects] for _ in range(2)], track_contact_forces=True,
                                max_contact_count=512)
    object_contacts.initialize()
    robot_contact_paths = [str(p.GetPath()) for p in stage.Traverse()
                           if p.HasAPI(UsdPhysics.RigidBodyAPI) and any(str(p.GetPath()).startswith(a['root']+'/') for a in arms)]
    robot_contacts = None
    if args.controller=='dexmimic':
        robot_contacts = RigidPrim(prim_paths_expr=[o.prim_path for o in objects],name='object_robot_contacts',
                                  contact_filter_prim_paths_expr=[robot_contact_paths for _ in objects],
                                  track_contact_forces=True,max_contact_count=4096)
        robot_contacts.initialize()
    settings = dict(object_pose_source="simulator ground truth", arm_bases_m=BASES.tolist(), physics_hz=240,
                    mass_kg=[.020, .025], static_friction=1.2, dynamic_friction=1., finger_max_force_N=10.,
                    finger_stiffness_N_m=600., finger_damping_Ns_m=10., contact_offset_m=.0003,
                    male_pin_radius_m=.0034, female_bore_radius_m=.0043, socket_depth_m=.020,
                    nominal_insertion_depth_m=.012, elastic_latch_simulated=False, cable_dynamics_simulated=False,
                    grasp_center_local_x_m=list(GRIP_X),
                    rotation_control="axis alignment; free roll about cylindrical shaft",
                    collision_model="compound cylinder and convex socket walls; source meshes are visual only",
                    fixed_grasp_joints=False, object_teleports_during_execution=False)
    settings['controller'] = args.controller
    settings['connector_model'] = args.connector_model
    if args.controller == 'dexmimic':
        settings['rotation_control'] = 'Official bimanual Mimic source waypoint orientations with demo-biased bounded IK'
        settings['angular_damping_per_s'] = 5.
        settings['linear_damping_per_s'] = .1
        settings['collision_model'] = 'Model-derived convex male barrel with flat resting faces; cylindrical pin and compound socket'
        settings['robot_contact_paths'] = robot_contact_paths
    if keyed_mechanics is not None:
        settings.update(collision_model='Reconstructed through bore, open key channels, retaining lip and two passive spring clips',
            rotation_control='Full keyed relative pose; +/-90 degree key datum, not free axial roll',
            elastic_latch_simulated=True,latch_model='Two limited radial prismatic springs; unmeasured material parameters',
            nominal_insertion_depth_m=keyed_mechanics.c['lock_target_depth_m'],
            male_pin_radius_m=keyed_mechanics.c['pin_radius_m'],female_bore_radius_m=keyed_mechanics.c['bore_radius_m'],
            keyed_physics=keyed_mechanics.c,connector_pair_joint=False)
        settings.update(static_friction=keyed_mechanics.c['static_friction'],
                        dynamic_friction=keyed_mechanics.c['dynamic_friction'],
                        connector_contact_offset_m=keyed_mechanics.c['contact_offset_m'],
                        gripper_material_friction=dict(static=1.2,dynamic=1.),
                        socket_depth_m=keyed_mechanics.c['mouth_x_m']-keyed_mechanics.c['female_rear_x_m'])
    save_json(out/"physics.json", settings)
    constraint_paths = []
    for prim in stage.Traverse():
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            targets = joint.GetBody0Rel().GetTargets()+joint.GetBody1Rel().GetTargets()
            if keyed_mechanics is not None and str(prim.GetPath()) in keyed_mechanics.joints:
                if set(map(str,targets))!={objects[0].prim_path,keyed_mechanics.clips[keyed_mechanics.joints.index(str(prim.GetPath()))].prim_path}:
                    raise RuntimeError('Unexpected body targets on an internal latch joint')
                continue
            if any(str(t).startswith("/World/Connector") for t in targets):
                constraint_paths.append(str(prim.GetPath()))
    audit = dict(object_constraints=constraint_paths, objects=[])
    if keyed_mechanics is not None:
        audit['internal_elastic_latch_joints']=keyed_mechanics.joints
        audit['connector_pair_joint']=False
    for obj in objects:
        prim = stage.GetPrimAtPath(obj.prim_path)
        rb = UsdPhysics.RigidBodyAPI(prim)
        collisions = [str(p.GetPath()) for p in Usd.PrimRange(prim) if p.HasAPI(UsdPhysics.CollisionAPI)]
        animation_samples = sum(a.GetNumTimeSamples() for a in prim.GetAttributes() if a.GetName().startswith("xformOp:"))
        audit["objects"].append(dict(path=obj.prim_path, dynamic=not bool(rb.GetKinematicEnabledAttr().Get()),
                                     pose_animation_samples=animation_samples, collision_shapes=collisions))
    audit["passed"] = not constraint_paths and all(o["dynamic"] and o["pose_animation_samples"] == 0 and o["collision_shapes"] for o in audit["objects"])
    save_json(out/"physics_audit.json", audit)
    if not audit["passed"]:
        raise RuntimeError("Physical body audit failed")
    rng = np.random.default_rng(args.seed)
    tick = 0
    phase = "initialize"
    log = []
    recording = False
    current_dir = out
    disturbance_holder=[None]

    def tool(arm):
        pose = ik.fk(arms[arm]["robot"].get_joint_positions(joint_indices=arms[arm]["indices"]))
        pose[:3, 3] += BASES[arm]
        return pose

    def object_poses():
        return [from_pose(*obj.get_world_pose()) for obj in objects]

    def force_matrix():
        return np.asarray(contacts.get_contact_force_matrix(dt=1/240))

    rgbd = None
    if args.perception=='rgbd' or args.rgbd_capture_only or args.rgbd_record:
        from sim_bridge.connector_rgbd import WristRGBD
        rgbd = WristRGBD(arms, tool, out/'rgbd', record_interval=.25 if args.rgbd_record else 0., clock=lambda:world.current_time,
                        depth_noise=args.rgbd_depth_noise,depth_dropout=args.rgbd_depth_dropout,
                        min_depth=args.rgbd_min_depth,max_depth=args.rgbd_max_depth,seed=args.seed)
        settings['object_pose_source'] = 'RGBD vision' if args.perception=='rgbd' else 'Ground truth controller; RGBD acquisition diagnostic only'
        save_json(out/'physics.json', settings)

    def step():
        nonlocal tick
        if not app.is_running():
            raise InterruptedError("Simulation closed")
        overrides={} if disturbance_holder[0] is None else disturbance_holder[0].before_step(tick/240,phase)
        for arm,state in enumerate(arms):
            robot = state["robot"]
            robot.set_joint_efforts(state["dynamics"].get_generalized_gravity_forces()[0])
            opening=overrides.get(arm,state['opening'])
            jaw_target = np.array([opening/2, -opening/2])
            state["jaw_command"] += np.clip(jaw_target-state["jaw_command"], -.025/240, .025/240)
            robot.apply_action(ArticulationAction(joint_positions=state["command"], joint_indices=state["indices"]))
            robot.apply_action(ArticulationAction(joint_positions=state["jaw_command"], joint_indices=state["fingers"]))
        render = (not args.headless and tick % 4 == 0) or (rgbd is not None and tick % 8 == 0)
        if rgbd is not None or keyed_mechanics is not None:
            if rgbd is not None:rgbd.record_robot_state(tick/240)
            # step(render=True) advances rendering_dt (four physics ticks here),
            # whereas our controller counts one tick. Separate the two clocks.
            world.step(render=False)
            tick += 1
            if rgbd is not None:rgbd.record_robot_state(tick/240)
            if render:world.render()
        else:
            world.step(render=render)
            tick += 1
        if rgbd is not None and render:
            rgbd.update(tick/240)
        deflection=None
        if keyed_mechanics is not None:deflection=keyed_mechanics.record(object_poses()[0])
        if recording and (tick % 4 == 0 or (rgbd is not None and render)):
            poses = object_poses()
            forces = force_matrix()
            log.append(dict(t=tick/240, phase=phase, objects=np.array(poses).tolist(), tools=[tool(a).tolist() for a in range(2)],
                            joints=[s["robot"].get_joint_positions(joint_indices=s["indices"]).tolist() for s in arms],
                            jaws=[s["robot"].get_joint_positions(joint_indices=s["fingers"]).tolist() for s in arms],
                            contact_forces_N=forces.tolist(),
                            robot_contact_forces_N=None if robot_contacts is None else np.asarray(robot_contacts.get_contact_force_matrix(dt=1/240)).tolist(),
                            latch_deflections_m=None if deflection is None else deflection.tolist()))

    def wait(seconds):
        for _ in range(round(seconds*240)):
            step()

    def set_phase(name):
        nonlocal phase
        phase = name
        print(f"PHASE {name} sim_t={tick/240:.3f}", flush=True)

    def command(targets, warm_multistart=False):
        for arm, target in enumerate(targets):
            if target is None:
                continue
            state = arms[arm]
            wanted = ik.solve(target, arm, state["command"], warm_multistart)
            # Limit target slew; no measured-joint teleportation during execution.
            state["command"] += np.clip(wanted-state["command"], -.6/30, .6/30)

    def move(targets, duration=2., tolerance=.002):
        starts = [tool(a) for a in range(2)]
        for k in range(round(duration*30)+1):
            command([None if t is None else blend(starts[a], t, k/(duration*30)) for a, t in enumerate(targets)])
            for _ in range(8):
                step()
        for _ in range(180):
            errors = [np.linalg.norm(tool(a)[:3, 3]-t[:3, 3]) for a, t in enumerate(targets) if t is not None]
            if max(errors, default=0.) < tolerance:
                return
            command(targets)
            for _ in range(8):
                step()
        raise RuntimeError(f"Arm target did not settle: {errors}")

    def capture(name):
        if not args.capture:
            return
        from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport
        viewport = get_active_viewport()
        viewport.updates_enabled = True
        viewport.resolution = (1280, 720)
        previous_camera = viewport.camera_path
        was_playing = world.is_playing()
        if was_playing:world.pause()
        if name in {"preinsert", "inserted"}:
            viewport.set_active_camera("/World/AssemblyCamera")
        async def capture_async():
            for _ in range(8):
                await omni.kit.app.get_app().next_update_async()
            task = capture_viewport_to_file(viewport, str(current_dir/(name+".png")))
            await task.wait_for_result()
        future = app.run_coroutine(capture_async(), run_until_complete=False)
        deadline = time.monotonic()+60
        while not future.done():
            app.update()
            if time.monotonic() > deadline:
                future.cancel()
                raise TimeoutError("Viewport capture timed out")
        future.result()
        viewport.set_active_camera(previous_camera)
        if was_playing:world.play()

    def fresh_placement():
        poses = []
        offsets = np.array([args.left_offset, args.right_offset])+rng.uniform(-args.spread, args.spread, (2, 2))
        yaws = np.array([args.left_yaw, args.right_yaw])+rng.uniform(-args.yaw_spread, args.yaw_spread, 2)
        for a in range(2):
            p = np.array([-.35, [-.11, .11][a], TABLE+.012])
            p[:2] += offsets[a]
            if not (-.48 < p[0] < -.23 and abs(p[1]) < .22):
                raise ValueError("Keep initial connectors inside x [-.48,-.23], |y| < .22 m")
            poses.append(transform(Rotation.from_euler("z", yaws[a], degrees=True).as_matrix()@ASSEMBLY_R, p))
        return poses

    def initialize(poses):
        # Explicit episode initialization only. These calls never appear in the task controller.
        for a, pose in enumerate(poses):
            objects[a].set_world_pose(pose[:3, 3], quat(pose))
            objects[a].set_linear_velocity(np.zeros(3))
            objects[a].set_angular_velocity(np.zeros(3))
            q = ik.solve(grasp_target(pose, a, .07), a, SEEDS[a], True)
            state = arms[a]
            state["command"] = q.copy()
            state["opening"] = .06
            state["jaw_command"] = np.array([.03, -.03])
            full = np.zeros(state["robot"].num_dof)
            full[state["indices"]] = q
            full[state["fingers"]] = state["jaw_command"]
            state["robot"].set_joint_positions(full)
            state["robot"].set_joint_velocities(np.zeros_like(full))
        wait(1.)

    def episode(index, reset=True):
        nonlocal current_dir, log, recording
        current_dir = out/f"trial_{index:03d}"
        current_dir.mkdir(exist_ok=True)
        log = []
        recording = True
        report = dict(passed=False, index=index, fixed_grasp_joints=False, randomized_spread_m=args.spread)
        disturbance_holder[0]=None
        grips = [None, None]
        try:
            set_phase("initialize")
            if not world.is_playing():
                world.play()
            if args.controller == 'dexmimic':
                from types import SimpleNamespace
                from sim_bridge.connector_dexmimic import execute_trial
                sim = SimpleNamespace(args=args, rng=rng, arms=arms, objects=objects, ik=ik,
                                      tool=tool, object_poses=object_poses, force_matrix=force_matrix,
                                      step=step, wait=wait, set_phase=set_phase, capture=capture,
                                      move=move, command=command, object_contacts=object_contacts,
                                      time=lambda:tick/240,phase=lambda:phase,
                                      set_disturbance=lambda value:disturbance_holder.__setitem__(0,value), rgbd=rgbd,
                                      pump=lambda:world.render(),keyed_mechanics=keyed_mechanics)
                execute_trial(sim, report, current_dir, reset=reset)
                return report
            if reset:
                initialize(fresh_placement())
            else:
                for s in arms:
                    s["opening"] = .06
                wait(.5)
            initial = object_poses()
            report["initial_object_poses"] = np.array(initial).tolist()
            save_json(current_dir/"initial_poses.json", report["initial_object_poses"])
            capture("initial")
            set_phase("approach")
            move([grasp_target(p, a, .06) for a, p in enumerate(initial)])
            for arm in (1, 0):
                set_phase(f"descend_{arm}")
                target = grasp_target(object_poses()[arm], arm)
                move([target if a == arm else None for a in range(2)], 2.)
                set_phase(f"close_{arm}")
                arms[arm]["opening"] = 0.
                wait(1.2)
                before = object_poses()[arm]
                grips[arm] = np.linalg.inv(tool(arm))@before
                set_phase(f"lift_{arm}")
                target = tool(arm)
                target[2, 3] += .09
                move([target if a == arm else None for a in range(2)], 2.5)
                wait(.5)
                after = object_poses()[arm]
                lift = float(after[2, 3]-initial[arm][2, 3])
                gap = float(np.diff(arms[arm]["robot"].get_joint_positions(joint_indices=arms[arm]["fingers"]))[0]*-1)
                contact = force_matrix()[arm*2:arm*2+2, arm]
                grip_after = np.linalg.inv(tool(arm))@after
                slip = float(np.linalg.norm(grip_after[:3, 3]-grips[arm][:3, 3]))
                normal_force = np.linalg.norm(contact, axis=-1)
                report[f"grasp_{arm}"] = dict(lift_m=lift, jaw_gap_m=gap, relative_slip_m=slip, contact_force_N=normal_force.tolist())
                print(f"GRASP_CHECK arm={arm} {report[f'grasp_{arm}']}", flush=True)
                if lift < .06 or not .008 < gap < .030 or slip > .008 or np.any(normal_force < .2):
                    raise RuntimeError(f"Physical grasp failed for arm {arm}: lift={lift:.4f}, gap={gap:.4f}")
                grips[arm] = np.linalg.inv(tool(arm))@after
            capture("both_grasped")
            if args.stop_after == "grasp":
                report["passed"] = True
                return report
            set_phase("transport")
            desired_objects = assembly_targets(.085, center=(-.35, 0., .90))
            actual = object_poses()
            for a in range(2):
                desired_objects[a][:3, :3] = align_axis(actual[a][:3, :3], ASSEMBLY_R[:, 0])
            move([desired_objects[a]@np.linalg.inv(grips[a]) for a in range(2)], 3.5)
            capture("preinsert")
            set_phase("align_and_insert")
            female_goal = desired_objects[1]
            depth_command = -.028
            translation_integral = np.zeros((2, 3))
            rotation_integral = np.zeros((2, 3))
            achieved = False
            for k in range(30*16):
                actual = object_poses()
                error = insertion_error(actual)
                for a in range(2):
                    # Estimate the current in-hand transform from measured body
                    # motion. This changes the arm target, never the object pose.
                    measured_grip = np.linalg.inv(tool(a))@actual[a]
                    grips[a] = blend(grips[a], measured_grip, .2)
                pair_forces = np.asarray(object_contacts.get_contact_force_matrix(dt=1/240))
                pair_force = float(np.linalg.norm(pair_forces[0, 1]))
                if pair_force > 8.:
                    raise RuntimeError(f"Insertion force limit exceeded: {pair_force:.2f} N")
                if error["lateral_error_m"] < .0008 and error["angle_rad"] < .035:
                    depth_command = min(.012, depth_command+.004/30)
                female = actual[1]
                female_goal[:3, :3] = align_axis(female[:3, :3], ASSEMBLY_R[:, 0])
                male_goal = transform(align_axis(actual[0][:3, :3], female[:3, 0]), female[:3, 3]+female[:3, :3]@[.057-depth_command, 0., 0.])
                hand_goals = []
                for a, desired in enumerate((male_goal, female_goal)):
                    translation_integral[a] = np.clip(translation_integral[a]+.15*(desired[:3, 3]-actual[a][:3, 3]), -.008, .008)
                    angular_error = Rotation.from_matrix(desired[:3, :3]@actual[a][:3, :3].T).as_rotvec()
                    rotation_integral[a] = np.clip(rotation_integral[a]+.10*angular_error, -.06, .06)
                    hand = desired@np.linalg.inv(grips[a])
                    hand[:3, 3] += translation_integral[a]
                    hand[:3, :3] = Rotation.from_rotvec(rotation_integral[a]).as_matrix()@hand[:3, :3]
                    hand_goals.append(hand)
                command(hand_goals)
                for _ in range(8):
                    step()
                if k % 30 == 0:
                    print(f"INSERT progress={depth_command:.4f} actual={error} force={pair_force:.3f}N", flush=True)
                if error["depth_m"] > .010 and error["lateral_error_m"] < .001 and error["angle_rad"] < .05:
                    achieved = True
                    break
                if min(p[2, 3] for p in actual) < TABLE+.05:
                    raise RuntimeError("A connector slipped/dropped during insertion")
            report["insertion"] = insertion_error(object_poses())
            if not achieved:
                raise RuntimeError(f"Insertion did not converge: {report['insertion']}")
            wait(.5)
            capture("inserted")
            if args.stop_after == "insert":
                report["passed"] = True
                return report
            set_phase("lower_pair")
            desired_objects = object_poses()
            for p in desired_objects:
                p[2, 3] = TABLE+.0105
            move([desired_objects[a]@np.linalg.inv(grips[a]) for a in range(2)], 3.5)
            set_phase("release")
            for s in arms:
                s["opening"] = .065
            wait(1.5)
            set_phase("retreat")
            targets = [tool(a) for a in range(2)]
            for t in targets:
                t[2, 3] += .09
            move(targets, 2.5)
            wait(.5)
            report["after_release"] = insertion_error(object_poses())
            report["final_object_poses"] = np.array(object_poses()).tolist()
            report["settled_on_table"] = bool(all(TABLE+.006 < p[2, 3] < TABLE+.014 for p in object_poses())
                                               and all(np.linalg.norm(obj.get_linear_velocity()) < .02 for obj in objects))
            report["released_from_fingers"] = bool(np.max(np.linalg.norm(force_matrix(), axis=-1)) < .1)
            report["passed"] = bool(report["after_release"]["depth_m"] > .008 and report["after_release"]["lateral_error_m"] < .002
                                    and report["settled_on_table"] and report["released_from_fingers"])
            capture("released")
        except Exception as exc:
            report["error"] = str(exc)
            print(f"TRIAL_FAILED {index}: {exc}", flush=True)
            capture("failed")
        finally:
            if log:
                np.savez_compressed(current_dir/"trajectory.npz", time=np.array([r["t"] for r in log]),
                                    objects=np.array([r["objects"] for r in log]), tools=np.array([r["tools"] for r in log]),
                                    joints=np.array([r["joints"] for r in log]), jaws=np.array([r["jaws"] for r in log]),
                                    contact_forces_N=np.array([r["contact_forces_N"] for r in log]), phase=np.array([r["phase"] for r in log]),
                                    robot_contact_forces_N=np.array([r['robot_contact_forces_N'] for r in log]) if robot_contacts is not None else np.empty(0),
                                    robot_contact_paths=np.array(robot_contact_paths))
                if keyed_mechanics is not None:
                    np.savez_compressed(current_dir/'latch_deflection.npz',time=np.array([r['t'] for r in log]),
                                        deflection_m=np.array([r['latch_deflections_m'] for r in log]))
            save_json(current_dir/"result.json", report)
            recording = False
        return report

    results = []
    for index in range(args.trials):
        results.append(episode(index))
        save_json(out/"summary.json", dict(passed=all(r["passed"] for r in results), successes=sum(r["passed"] for r in results), trials=len(results), results=results, physics=settings))
    stage.GetRootLayer().Export(str(out/"physical_scene.usdc"))
    print(f"PHYSICS_FINISHED output={out} successes={sum(r['passed'] for r in results)}/{len(results)} execution_goal={args.stop_after}", flush=True)
    if args.interactive and not args.headless:
        import omni.ui as ui
        pending = []
        panel = ui.Window("Demo connector physics" if args.controller=='dexmimic' else "Adaptive connector physics", width=460, height=240)
        with panel.frame:
            with ui.VStack(spacing=8):
                ui.Label("RGBD vision; physical friction grasp" if args.perception=='rgbd' else "Independent PhysX bodies; friction grasp; pose feedback")
                ui.Label('Execution: '+dict(grasp='GRASP ONLY (no insertion)',insert='Grasp + insertion',
                    place='FULL: grasp + insertion + placement',release='FULL: grasp + keyed insert + direct jaw release')[args.stop_after])
                ui.Label("Move connectors on table, then run from current poses.")
                with ui.HStack():
                    ui.Label("XY +/- (mm)", width=140)
                    xy_range = ui.FloatDrag(min=0., max=50., step=1., format='%.1f')
                    xy_range.model.set_value(args.spread*1000)
                with ui.HStack():
                    ui.Label("Yaw +/- (deg)", width=140)
                    yaw_range = ui.FloatDrag(min=0., max=30., step=1., format='%.1f')
                    yaw_range.model.set_value(args.yaw_spread)
                def queue_randomized():
                    args.spread = float(np.clip(xy_range.model.get_value_as_float()/1000,0.,.05))
                    args.yaw_spread = float(np.clip(yaw_range.model.get_value_as_float(),0.,30.))
                    xy_range.model.set_value(args.spread*1000)
                    yaw_range.model.set_value(args.yaw_spread)
                    pending.append(True)
                ui.Button("Run from current object poses", clicked_fn=lambda: pending.append(False))
                ui.Button("Randomize and run", clicked_fn=queue_randomized)
                with ui.HStack():
                    from omni.kit.viewport.utility import get_active_viewport
                    ui.Button("Overview", clicked_fn=lambda: get_active_viewport().set_active_camera("/OmniverseKit_Persp"))
                    ui.Button("Assembly camera", clicked_fn=lambda: get_active_viewport().set_active_camera("/World/AssemblyCamera"))
                if rgbd is not None:
                    with ui.HStack():
                        for a,name in enumerate(('Left wrist RGBD','Right wrist RGBD')):
                            ui.Button(name,clicked_fn=lambda a=a:get_active_viewport().set_active_camera(rgbd.paths[a]))
        while app.is_running():
            if pending:
                results.append(episode(len(results), reset=pending.pop(0)))
                save_json(out/"summary.json", dict(passed=all(r["passed"] for r in results), successes=sum(r["passed"] for r in results), trials=len(results), results=results, physics=settings))
            else:
                step()
    if not all(r["passed"] for r in results):
        raise RuntimeError(f"Physical trial failures: inspect {out}/summary.json")


try:
    run()
except BaseException:
    import os
    import traceback
    traceback.print_exc()
    # Kit's fast shutdown terminates with status 0. Preserve failures for batch
    # validation, and let process teardown release this simulator's GPU context.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)
else:
    app.close()
