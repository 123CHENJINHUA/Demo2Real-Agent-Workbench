"""Generic task-spec to real USD/PhysX scene and calibrated rendered previews."""
from pathlib import Path
import json,math,os,hashlib,time
import numpy as np
_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
from scipy.spatial.transform import Rotation

def build(root,spec_path,out,app):
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdPhysics,UsdShade,UsdLux,PhysxSchema,Vt
    import omni.usd,omni.kit.commands
    from isaacsim.core.api import World
    from isaacsim.core.prims import SingleArticulation,SingleRigidPrim
    from isaacsim.core.utils.types import ArticulationAction
    from isaacsim.core.utils.stage import add_reference_to_stage
    from isaacsim.core.utils.extensions import enable_extension
    from contracts.tasks import validate_task_spec,canonical_hash
    root=Path(root).resolve();out=Path(out).resolve();spec=json.loads(Path(spec_path).read_text());validate_task_spec(spec)
    if spec['content_hash']!=canonical_hash(spec):raise ValueError('Task content hash changed')
    def emit(stage,completed,total=8):print('SCENE_EVENT '+json.dumps({'type':'progress','payload':{'unit':'build_phases','completed':completed,'total':total,'stage':stage}},ensure_ascii=False),flush=True)
    def local(value):
        path=(root/str(value)).resolve();path.relative_to(root)
        if not path.is_file():raise ValueError('Missing project asset: '+str(value))
        return path
    def matrix(pose):
        t=np.eye(4);t[:3,:3]=Rotation.from_quat(np.asarray(pose['quaternion_wxyz'])[[1,2,3,0]]).as_matrix();t[:3,3]=pose['position_m'];return t
    def apply(prim,t):
        x=UsdGeom.Xformable(prim);x.ClearXformOpOrder();x.AddTransformOp().Set(Gf.Matrix4d(np.asarray(t).T.tolist()))
    def pose_of(body):
        pos,q=body.get_world_pose();return {'position_m':np.asarray(pos).tolist(),'quaternion_wxyz':np.asarray(q).tolist()}
    def world_matrix(prim):return np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim)).T
    hz=int(spec['scene'].get('physics_hz',240));duration=float(spec['scene'].get('settle_seconds',1.5))
    if not 60<=hz<=480 or not 0<=duration<=3:raise ValueError('Scene settling bounds exceeded')
    if spec['scene'].get('gravity_m_s2',[0,0,-9.81])!=[0,0,-9.81]:raise ValueError('This builder currently supports Z-up gravity only')
    world=World(stage_units_in_meters=1.,physics_dt=1/hz,rendering_dt=1/60,device='cpu');world.get_physics_context().set_solver_type('TGS');world.get_physics_context().enable_ccd(True)
    stage=omni.usd.get_context().get_stage();UsdGeom.Xform.Define(stage,'/World');stage.SetDefaultPrim(stage.GetPrimAtPath('/World'));UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z);UsdGeom.SetStageMetersPerUnit(stage,1.)
    scene_prim=UsdLux.DomeLight.Define(stage,'/World/Light');scene_prim.CreateIntensityAttr(700.)
    material=UsdShade.Material.Define(stage,'/World/ContactMaterial');m=UsdPhysics.MaterialAPI.Apply(material.GetPrim());m.CreateStaticFrictionAttr(.6);m.CreateDynamicFrictionAttr(.4);m.CreateRestitutionAttr(0.)
    def collide(prim):
        UsdPhysics.CollisionAPI.Apply(prim);api=PhysxSchema.PhysxCollisionAPI.Apply(prim);api.CreateContactOffsetAttr(.0001);api.CreateRestOffsetAttr(0.);UsdShade.MaterialBindingAPI.Apply(prim).Bind(material,materialPurpose='physics')
    def cube(path,size,position,color,collision=True):
        x=UsdGeom.Cube.Define(stage,path);x.CreateSizeAttr(1.);x.AddTranslateOp().Set(Gf.Vec3d(*position));x.AddScaleOp().Set(Gf.Vec3f(*size));x.CreateDisplayColorAttr([Gf.Vec3f(*color)])
        if collision:collide(x.GetPrim())
        return x
    table=spec['scene']['table'];cube('/World/Table',table['size_m'],table['position_m'],[.44,.48,.51])
    if spec.get('scene_approximation') and spec['scene'].get('preview_workbench_rail'):
        cx,cy,cz=table['position_m'];sx,sy,sz=table['size_m'];top=cz+sz/2
        cube('/World/WorkbenchFrontRail',[sx,.018,.009],[cx,cy-sy*.32,top+.0045],[.52,.54,.57],False)
        cube('/World/WorkbenchRearFrame',[sx,.026,.035],[cx,cy+sy*.50,top-.0175],[.52,.54,.57],False)
        marks=UsdGeom.Points.Define(stage,'/World/PerforationPreview')
        nx=min(56,max(2,int(sx/.025)));ny=min(28,max(2,int(sy/.025)))
        marks.CreatePointsAttr([Gf.Vec3f(cx+sx*(i/(nx-1)-.5)*.93,cy+sy*(j/(ny-1)-.5)*.62,top+.001)
                                for i in range(nx) for j in range(ny)])
        marks.CreateWidthsAttr([.004]);marks.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        marks.CreateDisplayColorAttr([Gf.Vec3f(.13,.15,.17)])
    floor=spec['scene'].get('floor',{'size_m':[3,3,.05],'position_m':[0,0,-.025]});cube('/World/Floor',floor['size_m'],floor['position_m'],[.24,.27,.29])
    for i,item in enumerate(spec['scene'].get('obstacles',[])):
        if item.get('geometry_type','box')!='box':raise ValueError('Unknown obstacle type')
        cube('/World/Obstacle'+str(i),item['size_m'],item['pose']['position_m'],[.35,.24,.16])
    emit('world_and_support',1)
    robots=[];camera_records=[]
    registration=spec.get('robot',{}).get('preview_registration')
    def calibrated_camera(identifier,mount_path,calibration_path,T_parent_color):
        data=json.loads(local(calibration_path).read_text());K=np.asarray(data['intrinsics']['K'],dtype=float);w,h=int(data['width']),int(data['height'])
        if not (16<=w<=2048 and 16<=h<=2048):raise ValueError('Camera resolution exceeds preview budget')
        optical_to_usd=np.diag([1.,-1.,-1.,1.]);mount=UsdGeom.Xform.Define(stage,mount_path);apply(mount.GetPrim(),T_parent_color@optical_to_usd)
        camera=UsdGeom.Camera.Define(stage,str(mount.GetPath())+'/Color');focal=20.
        camera.CreateFocalLengthAttr(focal);camera.CreateHorizontalApertureAttr(w*focal/K[0,0]);camera.CreateVerticalApertureAttr(h*focal/K[1,1]);camera.CreateHorizontalApertureOffsetAttr((w/2-K[0,2])*focal/K[0,0]);camera.CreateVerticalApertureOffsetAttr((K[1,2]-h/2)*focal/K[1,1]);camera.CreateClippingRangeAttr(Gf.Vec2f(.005,10.))
        projection=np.asarray(camera.GetCamera().frustum.ComputeProjectionMatrix());points=np.array([[0,0,1],[.1,.05,1],[-.05,-.1,.8]],float);clip=np.c_[points*np.array([1,-1,-1]),np.ones(3)]@projection;ndc=clip[:,:2]/clip[:,3:4];pixels=(ndc*np.array([1,-1])+1)*np.array([w/2,h/2]);target=points@K.T;err=float(np.max(np.abs(pixels-target[:,:2]/target[:,2:3])))
        if err>.001:raise RuntimeError('Optical/USD projection convention mismatch')
        return {'id':identifier,'path':str(camera.GetPath()),'width':w,'height':h,'K':K.tolist(),'calibration_path':calibration_path,'maximum_projection_error_px':err,'independently_calibrated_on_recording':data.get('reuse',{}).get('independent_validation_on_recorded_cameras',False)}
    if spec.get('robot',{}).get('arms'):
        enable_extension('isaacsim.asset.importer.urdf');app.update()
        _,config=omni.kit.commands.execute('URDFCreateImportConfig');config.merge_fixed_joints=False;config.fix_base=True;config.import_inertia_tensor=True;config.distance_scale=1.;config.self_collision=False;config.default_drive_strength=6000.;config.default_position_drive_damping=300.
        for a,arm in enumerate(spec['robot']['arms']):
            stage.SetDefaultPrim(stage.GetPrimAtPath('/World'))
            status,path=omni.kit.commands.execute('URDFParseAndImportFile',urdf_path=str(local(arm['urdf_path'])),import_config=config,get_articulation_root=True)
            if not status:raise RuntimeError('URDF import failed')
            base_path=path.rsplit('/',1)[0];apply(stage.GetPrimAtPath(base_path),matrix(arm['base_pose']))
            robot=world.scene.add(SingleArticulation(prim_path=path,name='robot_'+str(a)));robots.append((arm,robot,base_path))
            if registration:
                for prim in Usd.PrimRange(stage.GetPrimAtPath(base_path)):
                    if prim.HasAPI(UsdPhysics.CollisionAPI):
                        UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False).Set(False)
                x,y,z=arm['base_pose']['position_m'];stand_top=z-.005
                if stand_top>0:
                    cube('/World/EstimatedMount'+str(a),[.18,.18,stand_top],[x,y,stand_top/2],[.42,.45,.48],False)
            if spec['scene'].get('base_plates'):
                pos=np.asarray(arm['base_pose']['position_m'])-[0,0,.011];cube('/World/BasePlate'+str(a),[.16,.16,.022],pos.tolist(),[.85,.64,.035])
            cam=arm.get('camera')
            if cam:
                data=json.loads(local(cam['calibration_path']).read_text())
                Tparent=np.asarray(cam['T_parent_sdk_flange'])@np.asarray(data['hand_eye']['matrix'])
                parent=base_path+'/'+cam.get('parent_link','gripper_base')
                if not stage.GetPrimAtPath(parent):raise ValueError('Camera parent link not found')
                mount_path=parent+'/TaskCamera'
                if cam.get('housing_size_m'):
                    UsdGeom.Xform.Define(stage,mount_path)
                    cube(mount_path+'/ApproximateHousing',cam['housing_size_m'],[0,0,.015],[.14,.16,.19])
                camera_records.append(calibrated_camera(arm['id'],mount_path,cam['calibration_path'],Tparent)|{'housing_measured':cam.get('housing_measured',False)})
    if registration:
        center=registration['center_camera']
        camera_records.append(calibrated_camera('center','/World/RegisteredCenterMount',center['calibration_path'],np.asarray(center['T_world_camera_optical']))|{'approximate_reference_view':True})
    stage.SetDefaultPrim(stage.GetPrimAtPath('/World'));app.update();emit('robots_and_calibrated_cameras',2)
    objects=[];requested=[];keyed=[]
    for i,obj in enumerate(spec['objects']):
        name=obj['id']
        if not name.replace('_','').isalnum():raise ValueError('Object ID must be USD-safe')
        path='/World/Objects/'+name;prim=UsdGeom.Xform.Define(stage,path).GetPrim();apply(prim,matrix(obj['pose']))
        collision=obj.get('collision',{});kind=collision.get('type','unknown')
        if obj['geometry_type']=='mesh':
            if obj.get('visual_asset_path'):add_reference_to_stage(str(local(obj['visual_asset_path'])),path+'/VisualAsset')
            else:
                points,faces=read_mesh(local(obj['asset_path']))
                if spec.get('scene_approximation') and obj.get('preview_mesh_center_asset'):
                    points=points-np.asarray(obj['preview_mesh_center_asset'],float)
                color=obj.get('preview_color_rgb',[.10,.12,.14])
                mesh=UsdGeom.Mesh.Define(stage,path+'/VisualAsset');mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)));mesh.CreateFaceVertexCountsAttr([3]*len(faces));mesh.CreateFaceVertexIndicesAttr(faces.ravel().tolist());mesh.CreateSubdivisionSchemeAttr('none');mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
            visual=stage.GetPrimAtPath(path+'/VisualAsset');UsdGeom.Xformable(visual).AddScaleOp().Set(Gf.Vec3f(*obj['scale']))
        elif obj['geometry_type']=='box':
            color=obj.get('preview_color_rgb',[.8,.25,.07]) if spec.get('scene_approximation') else [.8,.25,.07]
            size=(np.array(obj['size_m'])*obj['scale']).tolist()
            shape=obj.get('preview_shape') if spec.get('scene_approximation') else None
            if shape and shape!='box':
                from modules.scene_building.preview_details import box_components
                UsdGeom.Xform.Define(stage,path+'/VisualAsset')
                for part_name,dimensions,offset,part_color in box_components(shape,size,color):
                    cube(path+'/VisualAsset/'+part_name,dimensions,offset,part_color,collision=kind!='preview_compound')
            else:cube(path+'/VisualAsset',size,[0,0,0],color,False)
        elif obj['geometry_type']=='cylinder':
            shape=UsdGeom.Cylinder.Define(stage,path+'/VisualAsset');shape.CreateRadiusAttr(obj['radius_m']);shape.CreateHeightAttr(obj['height_m']);shape.CreateDisplayColorAttr([Gf.Vec3f(.4,.35,.2)]);shape.AddScaleOp().Set(Gf.Vec3f(*obj['scale']))
        if kind=='connector_keyed_engineering':
            if obj['scale']!=[1,1,1]:raise ValueError('Engineering connector collision cannot silently scale functional dimensions')
            if collision.get('profile_path')!='assets/objects/connector/keyed_physics.json':raise ValueError('Unregistered engineering collision profile')
            keyed.append((collision['role'],path))
        elif kind=='box' and obj['geometry_type']=='box':
            visual=stage.GetPrimAtPath(path+'/VisualAsset')
            if visual.IsA(UsdGeom.Cube):collide(visual)
        elif kind=='preview_compound' and obj['geometry_type'] in ('mesh','box') and spec.get('scene_approximation'):
            for index,part in enumerate(collision['boxes']):
                body=cube(path+'/RepairCollision'+str(index),part['size_m'],part['center_m'],[.3,.3,.3])
                body.MakeInvisible()
        elif kind in ('convex_hull','mesh') and obj['geometry_type']=='mesh':
            # Convex approximations require an explicit task choice; never infer for holes.
            for child in Usd.PrimRange(stage.GetPrimAtPath(path+'/VisualAsset')):
                if child.IsA(UsdGeom.Mesh):collide(child);UsdPhysics.MeshCollisionAPI.Apply(child).CreateApproximationAttr('convexHull' if kind=='convex_hull' else 'none')
        elif obj['geometry_type']=='cylinder' and kind=='cylinder':collide(stage.GetPrimAtPath(path+'/VisualAsset'))
        else:raise ValueError('Collision representation must be explicitly configured: '+name)
        UsdPhysics.RigidBodyAPI.Apply(prim);UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(float(obj['mass_kg']));rb=PhysxSchema.PhysxRigidBodyAPI.Apply(prim);rb.CreateEnableCCDAttr(True);rb.CreateSolverPositionIterationCountAttr(64);rb.CreateSolverVelocityIterationCountAttr(16)
        body=world.scene.add(SingleRigidPrim(path,name=name));objects.append((obj,body,path));requested.append(obj['pose'])
    mechanics=None
    if keyed:
        roles=dict(keyed)
        if set(roles)!={'male','female'} or len(keyed)!=2:raise ValueError('Registered contact plugin requires exactly male/female pair')
        from tasks.connector.components.mechanics import KeyedMechanics
        mechanics=KeyedMechanics(stage,world,[roles['male'],roles['female']],material,visual=True)
    if mechanics:
        male_initial=matrix(next(obj['pose'] for obj,body,path in objects if obj['collision'].get('role')=='male'))
        for clip,center in zip(mechanics.clips,mechanics.centers):
            clip_pose=male_initial.copy();clip_pose[:3,3]=(male_initial@np.r_[center,1.])[:3]
            clip.set_world_pose(clip_pose[:3,3],Rotation.from_matrix(clip_pose[:3,:3]).as_quat()[[3,0,1,2]])
    emit('objects_and_explicit_collision',3)
    # Only initial conditions are set. No trajectory/controller/SDK is loaded.
    world.reset()
    robot_records=[]
    for arm,robot,path in robots:
        names=list(robot.dof_names);positions=np.zeros(robot.num_dof)
        for key,value in arm['initial_joint_positions'].items():
            if key not in names:raise ValueError('Unknown robot joint '+key)
            positions[names.index(key)]=float(value)
        robot.set_joint_positions(positions);robot.set_joint_velocities(np.zeros(robot.num_dof));robot.apply_action(ArticulationAction(joint_positions=positions))
        robot_records.append({'id':arm['id'],'root_path':path,'articulation_path':robot.prim_path,'dof_names':names,'initial_positions':positions.tolist(),'urdf_path':arm['urdf_path'],'robot_self_collision_enabled':False})
    for obj,body,path in objects:
        body.set_world_pose(np.array(obj['pose']['position_m']),np.array(obj['pose']['quaternion_wxyz']));body.set_linear_velocity(np.zeros(3));body.set_angular_velocity(np.zeros(3))
    if mechanics:
        male=next(body for obj,body,path in objects if obj['collision'].get('role')=='male');mechanics.reset(matrix(pose_of(male)))
    emit('initial_conditions',4)
    initial={obj['id']:pose_of(body) for obj,body,path in objects}
    records=[]
    for tick in range(round(duration*hz)):
        if not app.is_running():raise InterruptedError('Scene application closed')
        world.step(render=tick%4==0)
        if tick%max(1,hz//20)==0:records.append({'time_s':float(world.current_time),'objects':{obj['id']:pose_of(body) for obj,body,path in objects}})
    emit('local_physx_settling',5)
    settled={obj['id']:pose_of(body) for obj,body,path in objects}
    dynamics={obj['id']:{'linear_velocity_m_s':np.asarray(body.get_linear_velocity()).tolist(),'angular_velocity_rad_s':np.asarray(body.get_angular_velocity()).tolist()} for obj,body,path in objects}
    # Overview is a dedicated simulation camera, never a recorded/demo image.
    def make_preview_camera(path,cfg,identifier,width,height):
        eye=np.array(cfg['eye_m']);target=np.array(cfg['target_m']);forward=target-eye
        forward/=np.linalg.norm(forward)
        right=np.cross(forward,cfg.get('up',[0.,0.,1.]))
        if np.linalg.norm(right)<1e-5:right=np.array([1.,0.,0.])
        right/=np.linalg.norm(right);matrix=np.eye(4)
        matrix[:3,:3]=np.column_stack((right,np.cross(right,forward),-forward));matrix[:3,3]=eye
        camera=UsdGeom.Camera.Define(stage,path);apply(camera.GetPrim(),matrix)
        vertical=36.*height/width
        focal=36.*cfg.get('focal_fraction',2/3)
        camera.CreateFocalLengthAttr(focal);camera.CreateHorizontalApertureAttr(36.)
        camera.CreateVerticalApertureAttr(vertical);camera.CreateClippingRangeAttr(Gf.Vec2f(.01,20.))
        return {'id':identifier,'path':str(camera.GetPath()),'width':width,'height':height,
                'K':[[width*focal/36,0,width/2],[0,height*focal/vertical,height/2],[0,0,1]],
                'maximum_projection_error_px':0.,'calibration_path':None}
    cfg=spec['scene'].get('preview_camera',{'eye_m':[-1.4,-1.4,1.7],'target_m':[-.25,0,.9]})
    main=make_preview_camera('/World/PreviewCamera',cfg,'overview',1280,800)
    comparison_views=[]
    if spec.get('scene_approximation'):
        for config in spec['scene'].get('preview_reference_views',[]):
            identifier=config['id']
            path='/World/ReferenceView_'+identifier
            item=make_preview_camera(path,config,identifier,config.get('width',640),config.get('height',480))
            comparison_views.append(item|{'approximate_reference_view':True}|{key:config[key] for key in
                                    ('recorded_camera','frame_ref','corner_fit_error') if key in config})
    views=[main,*comparison_views,*camera_records]
    import omni.replicator.core as rep
    products=[]
    for view in views:
        product=rep.create.render_product(view['path'],(view['width'],view['height']));rgb=rep.AnnotatorRegistry.get_annotator('rgb');depth=rep.AnnotatorRegistry.get_annotator('distance_to_image_plane');rgb.attach([product]);depth.attach([product]);products.append((product,rgb,depth))
    for _ in range(4):app.update()
    world.pause()
    rep.orchestrator.step(rt_subframes=4,pause_timeline=True)
    # The rendered frame, exported scene and reported body state share this instant.
    settled={obj['id']:pose_of(body) for obj,body,path in objects}
    dynamics={obj['id']:{'linear_velocity_m_s':np.asarray(body.get_linear_velocity()).tolist(),'angular_velocity_rad_s':np.asarray(body.get_angular_velocity()).tolist()} for obj,body,path in objects}
    capture_time_s=float(world.current_time)
    from PIL import Image,ImageDraw
    captures=[]
    for i,(view,(_,rgb,depth)) in enumerate(zip(views,products)):
        color=np.asarray(rgb.get_data());z=np.asarray(depth.get_data())
        if color.ndim!=3 or color.shape[:2]!=(view['height'],view['width']):raise RuntimeError('Rendered camera image unavailable')
        color=color[:,:,:3].astype(np.uint8);valid=np.isfinite(z)&(z>0)&(z<20)
        if float(color.std())<1.:raise RuntimeError('Camera image is empty/constant')
        stem='scene' if i==0 else f'camera_{i-1}_rgb';image=out/(stem+'.png');Image.fromarray(color).save(image)
        T=world_matrix(stage.GetPrimAtPath(view['path']))@np.diag([1.,-1.,-1.,1.]);entry=dict(view,T_world_camera_optical=T.tolist(),rgb_path=str(image.relative_to(root)),valid_depth_fraction=float(valid.mean()),rgb_std=float(color.std()),capture_sim_time_s=float(world.current_time))
        if i:
            np.save(out/f'camera_{i-1}_depth_m.npy',np.where(valid,z,0).astype(np.float32),allow_pickle=False)
            if valid.any():low,high=np.percentile(z[valid],[5,95])
            else:low,high=0.,1.
            preview=np.where(valid,255*(1-np.clip((z-low)/max(.01,high-low),0,1)),0).astype(np.uint8);Image.fromarray(preview).save(out/f'camera_{i-1}_depth.png')
        overlay=Image.fromarray(color);draw=ImageDraw.Draw(overlay);K=np.array(view['K']);inv=np.linalg.inv(T)
        for obj,body,path in objects:
            objT=matrix(pose_of(body));p=objT@np.array([[0,0,0,1],[.03,0,0,1],[0,.03,0,1],[0,0,.03,1]],float).T;cam=inv@p;uv=K@cam[:3];uv=(uv[:2]/uv[2:3]).T
            if cam[2,0]>.005:
                for k,c in enumerate(['red','lime','cyan'],1):
                    if cam[2,k]>.005 and np.isfinite(uv[[0,k]]).all() and np.abs(uv[[0,k]]).max()<10000:draw.line([tuple(uv[0]),tuple(uv[k])],fill=c,width=2)
                if 0<=uv[0,0]<view['width'] and 0<=uv[0,1]<view['height']:draw.text(tuple(uv[0]),obj['id'],fill='yellow')
        draw.text((8,8),'SIMULATION ground-truth axes / engineering reference; preview only',fill='yellow');overlay.save(out/('scene_axes.png' if i==0 else f'camera_{i-1}_axes.png'));captures.append(entry)
    emit('rendered_scene_and_wrist_rgbd',6)
    # Flatten references into a private layer and rebase only local asset paths.
    flat=stage.Flatten()
    scene_data=out/'scene_data.usdc';flat.Export(str(scene_data))
    saved=Usd.Stage.Open(str(scene_data))
    from modules.scene_building.usd_portability import make_portable_materials
    material_adaptations=make_portable_materials(saved)
    for prim in saved.Traverse():
        for attr in prim.GetAttributes():
            value=attr.Get()
            if isinstance(value,Sdf.AssetPath) and value.path:
                resolved=Path(value.resolvedPath or value.path)
                if not resolved.is_absolute():resolved=(root/value.path).resolve()
                resolved=resolved.resolve();resolved.relative_to(root)
                if not resolved.is_file():raise ValueError('Unresolved local USD asset '+value.path)
                attr.Set(Sdf.AssetPath(os.path.relpath(resolved,out)))
    saved.GetRootLayer().Save()
    entry=Usd.Stage.CreateNew(str(out/'scene.usda'));entry.GetRootLayer().subLayerPaths=['./scene_data.usdc'];entry.SetDefaultPrim(entry.GetPrimAtPath('/World'));UsdGeom.SetStageMetersPerUnit(entry,1.);UsdGeom.SetStageUpAxis(entry,UsdGeom.Tokens.z);entry.GetRootLayer().Save()
    audit=[]
    for layer in entry.GetUsedLayers():
        path=Path(layer.realPath).resolve();path.relative_to(root);audit.append(str(path.relative_to(root)))
    for prim in entry.Traverse():
        for attr in prim.GetAttributes():
            value=attr.Get()
            if isinstance(value,Sdf.AssetPath) and value.path:
                if not value.resolvedPath:raise ValueError('USD reference failed resolution: '+value.path)
                Path(value.resolvedPath).resolve().relative_to(root)
    emit('usd_composition_and_reference_audit',7)
    boxes={};cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'],useExtentsHint=False)
    for obj,body,path in objects:
        bounds=cache.ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedRange();boxes[obj['id']]={'min':list(bounds.GetMin()),'max':list(bounds.GetMax())}
    pair_joints=[]
    object_paths={path for _,_,path in objects}
    for prim in stage.Traverse():
        if prim.IsA(UsdPhysics.Joint):
            joint=UsdPhysics.Joint(prim);a=set(str(x) for x in joint.GetBody0Rel().GetTargets());b=set(str(x) for x in joint.GetBody1Rel().GetTargets())
            if a&object_paths and b&object_paths:pair_joints.append(str(prim.GetPath()))
    manifest={'schema_version':'scene-manifest/v1','implementation':{'builder_version':'scene_building/v1','loaded_builder_sha256':_SOURCE_SHA256,'initial_joint_pose_hold_only':True},'task_spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'profile_id':spec.get('profile_id'),'spec_snapshot_path':str(Path(spec_path).relative_to(root)),'scene_path':str((out/'scene.usda').relative_to(root)),'preview_path':str((out/'scene.png').relative_to(root)),'scope':'scene_and_local_settling_only','contact_execution_authorized':False,'hardware_verified':False,'objects':spec['objects'],'initial_poses':initial,'settled_poses':settled,'capture_time_s':capture_time_s,'dynamics':dynamics,'world_visual_bounds':boxes,'robots':robot_records,'cameras':captures,'usd_layers':audit,'material_adaptations':material_adaptations,'connector_pair_joints':pair_joints,'gravity_m_s2':[0,0,-9.81],'physics_hz':hz,'settling_duration_s':duration,'settling_samples':records,'assumptions':spec.get('assumptions',[]),'evaluation':spec['evaluation'],'synthetic_test_fixture':bool(spec.get('source',{}).get('synthetic_test_fixture',False)),'full_task_executed':False,'hardware_commands_sent':0,'robot_motion_controller_loaded':False}
    if spec.get('scene_approximation'):
        manifest['scene_approximation']=spec['scene_approximation']
    if registration:
        manifest['robot_registration']=registration
    (out/'scene_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    result={'status':'succeeded','scene_path':manifest['scene_path'],'preview_path':manifest['preview_path'],'manifest_path':str((out/'scene_manifest.json').relative_to(root)),'task_spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'scope':manifest['scope'],'hardware_verified':False,'contact_execution_authorized':False,'full_task_executed':False,'message':'真实USD/PhysX场景与相机预览已构建；仅局部落桌检查，未执行抓取/插入策略。'}
    if registration:
        result['robot_registration']={key:registration[key] for key in ('status','bundle_id','message','input_spec_hash') if key in registration}
    (out/'build_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));emit('scene_artifacts_complete',8)
    for product,_,_ in products:product.destroy()
    return result

def read_mesh(path):
    """OBJ/STL triangle geometry for USD authoring; no pickle or external tools."""
    import struct
    vertices=[];faces=[]
    if Path(path).suffix.lower()=='.obj':
        for line in Path(path).read_text(errors='replace').splitlines():
            p=line.split()
            if not p:continue
            if p[0]=='v':vertices.append([float(x) for x in p[1:4]])
            elif p[0]=='f':
                ids=[int(v.split('/')[0]) for v in p[1:]];ids=[i-1 if i>0 else len(vertices)+i for i in ids]
                faces.extend((ids[0],ids[k],ids[k+1]) for k in range(1,len(ids)-1))
    elif Path(path).suffix.lower()=='.stl':
        data=Path(path).read_bytes();count=struct.unpack_from('<I',data,80)[0] if len(data)>=84 else 0
        if len(data)!=84+count*50:
            from modules.geometry.functional import load_mesh
            return load_mesh(Path(path))
        for i in range(count):
            values=struct.unpack_from('<12fH',data,84+i*50);start=len(vertices);vertices.extend([values[j:j+3] for j in (3,6,9)]);faces.append((start,start+1,start+2))
    else:raise ValueError('Unsupported scene mesh')
    if not vertices or not faces:raise ValueError('Mesh has no triangles')
    return np.asarray(vertices,float),np.asarray(faces,np.int32)
