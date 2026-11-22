"""Run trial joint targets against a private Isaac scene session layer."""
import numpy as np

from .contact_trace import ContactTrace


class MotionRollouts:
    def __init__(self, scene_path, prepared_path, manifest, app, *, fixed_ids=(), record_contacts=False):
        import omni.usd
        from pxr import PhysxSchema, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade
        from isaacsim.core.api import World
        from isaacsim.core.prims import SingleArticulation, SingleRigidPrim
        from isaacsim.core.utils.types import ArticulationAction

        self.action = ArticulationAction
        self.geom = UsdGeom
        self.manifest = manifest
        self.fixed_ids = set(fixed_ids)
        source_stage = Usd.Stage.Open(str(scene_path))
        if source_stage is None:
            raise ValueError('Cannot read bound USD scene')
        prepared = Usd.Stage.Open(source_stage.Flatten())
        for ref in ('/Render','/Orchestrator','/Replicator'):
            if prepared.GetPrimAtPath(ref):
                prepared.RemovePrim(ref)
        visual_paths = [prim.GetPath() for prim in prepared.Traverse()
                        if prim.IsA(UsdGeom.Camera) or prim.HasAPI(UsdLux.LightAPI)
                        or (prim.IsA(UsdShade.Material) and not prim.HasAPI(UsdPhysics.MaterialAPI))]
        for path in visual_paths:
            prim = prepared.GetPrimAtPath(path)
            if prim:
                prim.SetActive(False)
        prepared.GetRootLayer().Export(str(prepared_path))
        if not omni.usd.get_context().open_stage(str(prepared_path)):
            raise RuntimeError('Isaac could not load the registered scene')
        self.stage = omni.usd.get_context().get_stage()
        self.stage.SetEditTarget(self.stage.GetSessionLayer())
        stage = self.stage
        app.update()
        physics = [str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
        if len(physics)!=1:
            raise ValueError('Need exactly one physics scene')
        self.hz = int(manifest['physics_hz'])
        self.world = World(stage_units_in_meters=1.,physics_dt=1/self.hz,rendering_dt=1/60,
                           device='cpu',physics_prim_path=physics[0])
        self.world.get_physics_context().set_solver_type('TGS')
        self.world.get_physics_context().enable_ccd(True)
        self.world.get_physics_context().set_solve_articulation_contact_last(True)
        material = UsdShade.Material.Define(stage,'/World/DemonstrationGraspMaterial')
        friction = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        friction.CreateStaticFrictionAttr(1.1)
        friction.CreateDynamicFrictionAttr(.9)
        self.robots, self.fingers = [], []
        self.contact_colliders = 0
        for side in ('left','right'):
            record = next(row for row in manifest['robots'] if row['id']==side)
            self.robots.append(self.world.scene.add(SingleArticulation(record['articulation_path'],name=side+'_placement')))
            for finger in ('gripper_link1','gripper_link2'):
                path = record['root_path']+'/'+finger
                self.fingers.append(stage.GetPrimAtPath(path))
                group = stage.GetPrimAtPath(path+'/collisions')
                if group and group.IsInstance():
                    group.SetInstanceable(False)
            for prim in stage.Traverse():
                path = str(prim.GetPath())
                if not path.startswith(record['root_path']+'/'):
                    continue
                if prim.HasAPI(UsdPhysics.CollisionAPI):
                    UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr().Set(True)
                    self.contact_colliders += 1
                    if '/gripper_link' in path:
                        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material,materialPurpose='physics')
                        PhysxSchema.PhysxCollisionAPI.Apply(prim).CreateContactOffsetAttr(.001)
                if prim.GetName() in ('gripper_joint1','gripper_joint2') and prim.IsA(UsdPhysics.PrismaticJoint):
                    drive = UsdPhysics.DriveAPI.Apply(prim,'linear')
                    drive.CreateTypeAttr().Set('force')
                    drive.CreateStiffnessAttr().Set(650.)
                    drive.CreateDampingAttr().Set(12.)
                    drive.CreateMaxForceAttr().Set(15.)
        if not self.contact_colliders or not all(self.fingers):
            raise ValueError('The registered scene does not provide Nero contact geometry and finger links')
        self.objects = {}
        bodies = []
        for key in manifest['settled_poses']:
            path = '/World/Objects/'+key
            prim = stage.GetPrimAtPath(path)
            body = UsdPhysics.RigidBodyAPI(prim)
            if not body:
                raise ValueError('Expected rigid object at '+path)
            if key in self.fixed_ids:
                body.CreateKinematicEnabledAttr().Set(True)
            bodies.append(prim)
            self.objects[key] = self.world.scene.add(SingleRigidPrim(path,name=key))
        self.contacts = ContactTrace(bodies) if record_contacts else None

    def execute(self, offsets, model, commands, *, stop=None):
        if self.contacts:
            self.contacts.time = None
            self.contacts.rows.clear()
        self.world.reset()
        for key, body in self.objects.items():
            saved = self.manifest['settled_poses'][key]
            position = np.asarray(saved['position_m']).copy()
            if key in offsets:
                position += offsets[key]
            body.set_world_pose(position,np.asarray(saved['quaternion_wxyz']))
            if key not in self.fixed_ids:
                body.set_linear_velocity(np.zeros(3))
                body.set_angular_velocity(np.zeros(3))
        joint_names = [f'joint{i}' for i in range(1,8)]+['gripper_joint1','gripper_joint2']
        openings = model['gripper_opening_m']
        ticks = np.rint(model['times_s']*self.hz).astype(int)
        for side, robot in enumerate(self.robots):
            indices = np.array([robot.dof_names.index(name) for name in joint_names])
            robot.set_solver_position_iteration_count(32)
            robot.set_solver_velocity_iteration_count(8)
            robot.set_joint_positions(np.r_[commands[0,side],openings[0,side]/2,-openings[0,side]/2],joint_indices=indices)
            robot.set_joint_velocities(np.zeros(robot.num_dof))
        joint_indices = [np.array([robot.dof_names.index(name) for name in joint_names]) for robot in self.robots]
        times, points, orientations, fingers, speeds, actual = [], [], [], [], [], []

        def snapshot():
            return {'times_s':np.asarray(times),'object_ids':np.asarray(list(self.objects),dtype=str),
                    'object_m':np.asarray(points),'object_wxyz':np.asarray(orientations),
                    'finger_m':np.asarray(fingers),'object_speed_m_s':np.asarray(speeds),'actual_joints':np.asarray(actual)}

        consumed = 0
        for i in range(len(ticks)+12):
            k = min(i,len(ticks)-1)
            requested = ticks[k] if i<len(ticks) else ticks[-1]+(i-len(ticks)+1)*self.hz//12
            for side, (robot, indices) in enumerate(zip(self.robots,joint_indices)):
                opening = openings[k,side]/2
                targets = np.r_[commands[k,side],opening,-opening]
                robot.apply_action(self.action(joint_positions=targets,joint_indices=indices))
            for step in range(consumed+1,requested+1):
                if self.contacts:
                    self.contacts.time = step/self.hz
                self.world.step(render=False)
            if self.contacts:
                self.contacts.time = None
            consumed = requested
            poses = [body.get_world_pose() for body in self.objects.values()]
            cache = self.geom.XformCache()
            fingers.append([list(cache.GetLocalToWorldTransform(finger).ExtractTranslation()) for finger in self.fingers])
            times.append(consumed/self.hz)
            points.append([np.asarray(position).copy() for position,_ in poses])
            orientations.append([np.asarray(quaternion).copy() for _,quaternion in poses])
            speeds.append([np.linalg.norm(body.get_linear_velocity()) for body in self.objects.values()])
            actual.append([r.get_joint_positions(joint_indices=ids) for r,ids in zip(self.robots,joint_indices)])
            if stop is not None and stop(times[-1],points[-1],snapshot):
                break
        trace = snapshot()
        if self.contacts:
            trace.update(self.contacts.arrays())
        return trace
