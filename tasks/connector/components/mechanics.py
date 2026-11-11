"""Keyed contact geometry and two passive spring latch bodies.

The visual OBJ is unchanged. Its latch faces are split into moving render parts.
The collision reconstruction repairs the scanned channels for a testable fit;
it is an explicit engineering approximation, not measured material compliance.
No connector-to-connector or connector-to-gripper joint is created here.
"""
from pathlib import Path
import numpy as np
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation
from tasks.connector.components.keying_geometry import profile,female_rectangles,latch_profile,keyed_error,keyed_success,keyed_retained

ROOT=Path(__file__).resolve().parents[3]


class KeyedMechanics:
    def __init__(self,stage,world,roots,material,visual=True):
        from pxr import Gf,PhysxSchema,Sdf,UsdGeom,UsdPhysics,UsdShade,Vt
        from isaacsim.core.prims import SingleRigidPrim
        self.stage,self.world,self.roots,self.material=stage,world,roots,material
        self.c=profile();self.clips=[];self.centers=[];self.joints=[];self.max_deflection=np.zeros(2)
        c=self.c
        material=UsdShade.Material.Define(stage,'/World/KeyedContactMaterial')
        mechanical_material=UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        mechanical_material.CreateStaticFrictionAttr(c['static_friction'])
        mechanical_material.CreateDynamicFrictionAttr(c['dynamic_friction'])
        mechanical_material.CreateRestitutionAttr(0.)
        self.material=material
        def collision(prim):
            UsdPhysics.CollisionAPI.Apply(prim)
            api=PhysxSchema.PhysxCollisionAPI.Apply(prim)
            api.CreateContactOffsetAttr(c['contact_offset_m']);api.CreateRestOffsetAttr(0.)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material,materialPurpose='physics')
        def hull(path,points):
            points=np.asarray(points);h=ConvexHull(points)
            m=UsdGeom.Mesh.Define(stage,path);m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)))
            m.CreateFaceVertexCountsAttr([3]*len(h.simplices));m.CreateFaceVertexIndicesAttr(h.simplices.ravel().tolist())
            m.CreateSubdivisionSchemeAttr('none');m.CreateVisibilityAttr('invisible');collision(m.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr('convexHull')
        def cylinder(path,x0,x1,r,y=0.):
            p=UsdGeom.Cylinder.Define(stage,path);p.CreateAxisAttr('X');p.CreateHeightAttr(x1-x0);p.CreateRadiusAttr(r)
            p.AddTranslateOp().Set(Gf.Vec3d((x0+x1)/2,y,0.));p.CreateVisibilityAttr('invisible');collision(p.GetPrim())
        def box(path,x0,x1,y0,y1,z0,z1):
            p=UsdGeom.Cube.Define(stage,path);p.CreateSizeAttr(1.)
            p.AddTranslateOp().Set(Gf.Vec3d((x0+x1)/2,(y0+y1)/2,(z0+z1)/2))
            p.AddScaleOp().Set(Gf.Vec3f(x1-x0,y1-y0,z1-z0));p.CreateVisibilityAttr('invisible');collision(p.GetPrim())
        # Keep the scan's stable rear grasping flats, but do not extend the full
        # barrel forward into the part of the plug which must enter the socket.
        source=next((ROOT/'assets/objects/connector/model/1').glob('*lowpoly.obj'))
        vertices=np.array([[float(v) for v in line.split()[1:4]] for line in source.read_text().splitlines() if line.startswith('v ')])
        section=np.unique(np.round(vertices[vertices[:,0]>=.005,1:],5),axis=0)
        section=section[ConvexHull(section).vertices]
        hull(roots[0]+'/KeyedBarrel',[(x,*yz) for x in (c['barrel_front_x_m'],.028) for yz in section])
        cylinder(roots[0]+'/KeyedPin',-.028,-.012,c['pin_radius_m'],c['pin_y_offset_m'])
        cylinder(roots[0]+'/KeyedCollar',-.012,c['barrel_front_x_m'],c['collar_radius_m'])
        UsdPhysics.MassAPI.Apply(stage.GetPrimAtPath(roots[0])).CreateMassAttr(.020-2*c['latch_mass_kg'])
        # A THROUGH bore replaces the old solid rear cap. Front rectangular
        # channels are decomposed into boxes; their holes are never convexified.
        for i in range(32):
            angles=np.array([i,i+1])*2*np.pi/32
            points=[(x,r*np.cos(t),r*np.sin(t)) for x in (c['female_rear_x_m'],c['window_start_x_m'])
                    for r in (c['bore_radius_m'],.0093) for t in angles]
            hull(roots[1]+f'/RearBoreWall{i:02d}',points)
        for region,x0,x1 in [('window',c['window_start_x_m'],c['retaining_lip_x_m']),('front',c['retaining_lip_x_m'],c['mouth_x_m'])]:
            for i,(y0,y1,z0,z1) in enumerate(female_rectangles(region,c)):
                box(roots[1]+f'/{region}Wall{i:03d}',x0,x1,y0,y1,z0,z1)
        # Both latches translate only radially against a passive spring. Joint
        # collision is disabled only between each clip and its OWN connector.
        for a,sign in enumerate((1.,-1.)):
            center=np.array([-.009,sign*.0072,0.]);self.centers.append(center)
            root=f'/World/MaleLatch{a}'
            p=UsdGeom.Xform.Define(stage,root);p.AddTranslateOp().Set(Gf.Vec3d(*center.tolist()))
            UsdPhysics.RigidBodyAPI.Apply(p.GetPrim())
            UsdPhysics.MassAPI.Apply(p.GetPrim()).CreateMassAttr(c['latch_mass_kg'])
            rb=PhysxSchema.PhysxRigidBodyAPI.Apply(p.GetPrim());rb.CreateEnableCCDAttr(True)
            rb.CreateSolverPositionIterationCountAttr(128);rb.CreateSolverVelocityIterationCountAttr(32)
            head=[np.array([x,sign*r,z])-center for x,r in latch_profile(c) for z in (-c['latch_half_width_m'],c['latch_half_width_m'])]
            hull(root+'/HookContact',head)
            low,high=sorted([sign*.0068,sign*.0076])
            box(root+'/BeamContact',c['latch_head_back_x_m']-center[0],c['latch_root_x_m']-center[0],
                low-center[1],high-center[1],-.0008,.0008)
            joint_path=f'/World/KeyedLatchSpring{a}'
            joint=UsdPhysics.PrismaticJoint.Define(stage,joint_path)
            joint.CreateBody0Rel().SetTargets([Sdf.Path(roots[0])]);joint.CreateBody1Rel().SetTargets([Sdf.Path(root)])
            joint.CreateAxisAttr('Y');joint.CreateLocalPos0Attr(Gf.Vec3f(*center.tolist()));joint.CreateLocalPos1Attr(Gf.Vec3f(0.))
            joint.CreateLocalRot0Attr(Gf.Quatf(1.));joint.CreateLocalRot1Attr(Gf.Quatf(1.))
            joint.CreateLowerLimitAttr(0. if sign>0 else -c['latch_travel_m'])
            joint.CreateUpperLimitAttr(c['latch_travel_m'] if sign>0 else 0.)
            joint.CreateCollisionEnabledAttr(False)
            drive=UsdPhysics.DriveAPI.Apply(joint.GetPrim(),'linear')
            drive.CreateTypeAttr('force');drive.CreateTargetPositionAttr(0.);drive.CreateTargetVelocityAttr(0.)
            drive.CreateStiffnessAttr(c['latch_stiffness_N_m']);drive.CreateDampingAttr(c['latch_damping_Ns_m'])
            drive.CreateMaxForceAttr(c['latch_drive_max_force_N'])
            self.joints.append(joint_path)
            self.clips.append(world.scene.add(SingleRigidPrim(root,name=f'male_latch_{a}')))
        if visual:self.split_visual()

    @classmethod
    def from_existing(cls,stage,world,roots):
        """Wrap the authored mechanism without creating prims or resetting poses."""
        from pxr import UsdPhysics
        from isaacsim.core.prims import SingleRigidPrim
        self=cls.__new__(cls)
        self.stage,self.world,self.roots,self.material=stage,world,list(roots),None
        self.c=profile();self.clips=[];self.centers=[];self.joints=[];self.max_deflection=np.zeros(2)
        for a,sign in enumerate((1.,-1.)):
            clip_path=f'/World/MaleLatch{a}';joint_path=f'/World/KeyedLatchSpring{a}'
            clip=stage.GetPrimAtPath(clip_path);joint=UsdPhysics.PrismaticJoint(stage.GetPrimAtPath(joint_path))
            if not clip or not clip.HasAPI(UsdPhysics.RigidBodyAPI) or not joint:
                raise ValueError('Loaded scene lacks registered latch bodies/joints')
            if list(map(str,joint.GetBody0Rel().GetTargets()))!=[str(roots[0])] or list(map(str,joint.GetBody1Rel().GetTargets()))!=[clip_path]:
                raise ValueError('Loaded latch joint has incompatible body targets')
            if joint.GetAxisAttr().Get()!='Y':raise ValueError('Incompatible latch joint axis')
            expected=(0.,self.c['latch_travel_m']) if sign>0 else (-self.c['latch_travel_m'],0.)
            actual=(joint.GetLowerLimitAttr().Get(),joint.GetUpperLimitAttr().Get())
            if not np.allclose(actual,expected,atol=1e-8,rtol=1e-6):raise ValueError('Incompatible latch joint travel')
            self.centers.append(np.array([-.009,sign*.0072,0.]));self.joints.append(joint_path)
            self.clips.append(world.scene.add(SingleRigidPrim(clip_path,name=f'existing_male_latch_{a}')))
        return self

    def split_visual(self):
        from pxr import Gf,UsdGeom,UsdShade,Vt
        mesh=UsdGeom.Mesh(self.stage.GetPrimAtPath(self.roots[0]+'/VisualAsset/Visual'))
        if not mesh:raise RuntimeError('Cannot split latch visuals: expected connector VisualAsset/Visual')
        points=np.array(mesh.GetPointsAttr().Get());faces=np.array(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1,3)
        uv=UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st');uv_data=uv.Get();uv_indices=np.array(uv.GetIndices()).reshape(-1,3)
        material=UsdShade.MaterialBindingAPI(mesh.GetPrim()).ComputeBoundMaterial()[0]
        center=points[faces].mean(axis=1)
        candidates=(center[:,0]<-.005)&(abs(center[:,1])>.0051)&(abs(center[:,2])<.0035)
        labels=np.where(candidates,np.where(center[:,1]>0,1,2),0)
        original_faces=faces.copy()
        mesh.GetFaceVertexCountsAttr().Set([3]*int(np.sum(labels==0)))
        mesh.GetFaceVertexIndicesAttr().Set(original_faces[labels==0].ravel().tolist())
        uv.SetIndices(uv_indices[labels==0].ravel().tolist())
        for a,body in enumerate(self.clips):
            selected=labels==a+1
            part=UsdGeom.Mesh.Define(self.stage,body.prim_path+'/Visual')
            part.CreatePointsAttr(Vt.Vec3fArray.FromNumpy((points-self.centers[a]).astype(np.float32)))
            part.CreateFaceVertexCountsAttr([3]*int(selected.sum()));part.CreateFaceVertexIndicesAttr(original_faces[selected].ravel().tolist())
            part.CreateSubdivisionSchemeAttr('none');part.CreateDoubleSidedAttr(True)
            pv=UsdGeom.PrimvarsAPI(part).CreatePrimvar('st',uv.GetTypeName(),'faceVarying')
            pv.Set(uv_data);pv.SetIndices(uv_indices[selected].ravel().tolist())
            UsdShade.MaterialBindingAPI.Apply(part.GetPrim()).Bind(material)

    def reset(self,male_pose):
        q=Rotation.from_matrix(male_pose[:3,:3]).as_quat()[[3,0,1,2]]
        for body,center in zip(self.clips,self.centers):
            body.set_world_pose(male_pose[:3,3]+male_pose[:3,:3]@center,q)
            body.set_linear_velocity(np.zeros(3));body.set_angular_velocity(np.zeros(3))
        self.max_deflection[:]=0.

    def record(self,male_pose):
        delta=[]
        for a,(body,center) in enumerate(zip(self.clips,self.centers)):
            position=body.get_world_pose()[0]
            local=male_pose[:3,:3].T@(position-male_pose[:3,3])-center
            delta.append(float(local[1]*(1 if a==0 else -1)))
        values=np.array(delta)
        self.max_deflection=np.maximum(self.max_deflection,values)
        return values

    def evaluate(self,objects):
        current=self.record(objects[0]);error=keyed_error(objects,config=self.c)
        seated=bool(np.all(abs(current)<self.c['latch_seated_deflection_m']))
        flexed=bool(np.all(self.max_deflection>.0001))
        return dict(**error,deflection_m=current.tolist(),maximum_deflection_m=self.max_deflection.tolist(),
                    both_latches_seated=seated,both_latches_flexed=flexed,
                    aligned_for_insertion=keyed_success(error,self.c),
                    engaged=bool(keyed_retained(error,self.c) and seated and flexed),
                    approximation='Two radial prismatic springs and reconstructed convex contact geometry',
                    connector_pair_joint=False)
