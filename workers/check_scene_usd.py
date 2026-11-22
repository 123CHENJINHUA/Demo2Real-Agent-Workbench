from pathlib import Path
import argparse,json,sys,math,hashlib
from pxr import Usd,UsdPhysics,UsdGeom,Sdf
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--scene',required=True);p.add_argument('--output',required=True);a=p.parse_args();scene=Path(a.scene).resolve();scene.relative_to(ROOT);out=Path(a.output).resolve();out.relative_to(ROOT)
stage=Usd.Stage.Open(str(scene));missing=[];outside=[];dependencies=[];resources=set()
for layer in stage.GetUsedLayers():
    if not layer.realPath:continue
    path=Path(layer.realPath).resolve()
    if not path.is_relative_to(ROOT):outside.append(str(path))
    if not path.is_file():missing.append(str(path))
    dependencies.append(str(path));resources.add(path)
for prim in stage.Traverse():
    for attr in prim.GetAttributes():
        value=attr.Get()
        values=[value] if isinstance(value,Sdf.AssetPath) else list(value) if isinstance(value,(tuple,list)) or type(value).__name__=='AssetPathArray' else []
        for ref in values:
            if not isinstance(ref,Sdf.AssetPath) or not ref.path:continue
            if not ref.resolvedPath:missing.append(ref.path);continue
            path=Path(ref.resolvedPath).resolve()
            if not path.is_relative_to(ROOT):outside.append(str(path))
            if not path.is_file():missing.append(str(path))
            resources.add(path)
poses={}
cache=UsdGeom.XformCache()
for prim in stage.Traverse():
    if str(prim.GetPath()).startswith('/World/Objects/') and prim.HasAPI(UsdPhysics.RigidBodyAPI):
        position=cache.GetLocalToWorldTransform(prim).ExtractTranslation();poses[str(prim.GetPath())]=list(position)
articulation_bases={}
for prim in stage.Traverse():
    if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        parent=prim.GetParent()
        articulation_bases[str(parent.GetPath())]=list(cache.GetLocalToWorldTransform(parent).ExtractTranslation())
camera_properties={}
for prim in stage.Traverse():
    if not prim.IsA(UsdGeom.Camera):continue
    camera=UsdGeom.Camera(prim);optical_flip=[[1,0,0,0],[0,-1,0,0],[0,0,-1,0],[0,0,0,1]]
    # Gf matrices act on row vectors; transpose explicitly for exported convention.
    usd=cache.GetLocalToWorldTransform(prim);parent=prim.GetParent().GetParent();parent_world=cache.GetLocalToWorldTransform(parent)
    relative=usd*parent_world.GetInverse()
    rel=[[float(relative[j][i])*optical_flip[j][j] for j in range(4)] for i in range(4)]
    projection=camera.GetCamera().frustum.ComputeProjectionMatrix()
    samples=[]
    for point in ((0.,0.,1.),(.1,.05,1.),(-.05,-.1,.8)):
        v=[point[0],-point[1],-point[2],1.];clip=[sum(v[k]*projection[k][j] for k in range(4)) for j in range(4)]
        samples.append([clip[0]/clip[3],clip[1]/clip[3]])
    camera_properties[str(prim.GetPath())]={'parent_link_path':str(parent.GetPath()),'T_parent_camera_optical':rel,'projection_ndc_samples':samples,'focal_length_mm':camera.GetFocalLengthAttr().Get(),'clipping_range':list(camera.GetClippingRangeAttr().Get())}
joints=[]
for prim in stage.Traverse():
    if prim.IsA(UsdPhysics.Joint):
        joint=UsdPhysics.Joint(prim);joints.append({'path':str(prim.GetPath()),'type':prim.GetTypeName(),'body0':[str(x) for x in joint.GetBody0Rel().GetTargets()],'body1':[str(x) for x in joint.GetBody1Rel().GetTargets()]})
artifact_sha256={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in resources if path.is_file() and path.is_relative_to(ROOT)}
# Compute support from actual referenced mesh vertices / analytic surfaces.
# Transforming local bounding-box corners would produce false penetrations.
import numpy as np
from modules.scene_validation.geometry_bounds import mesh_bounds,primitive_bounds,union_bounds
exact_object_bounds={}
for body_path in poses:
    roots=[body_path]
    for joint in joints:
        if body_path in joint['body0'] and joint['type']=='PhysicsPrismaticJoint':
            roots.extend(p for p in joint['body1'] if p in ('/World/MaleLatch0','/World/MaleLatch1'))
    visual=[];collision=[]
    for body_root in roots:
        for prim in Usd.PrimRange(stage.GetPrimAtPath(body_root)):
            T=np.asarray(cache.GetLocalToWorldTransform(prim)).T;b=None
            if prim.IsA(UsdGeom.Mesh):
                m=UsdGeom.Mesh(prim);indices=m.GetFaceVertexIndicesAttr().Get()
                if indices:b=mesh_bounds(m.GetPointsAttr().Get(),indices,T)
            elif prim.IsA(UsdGeom.Cube):b=primitive_bounds('cube',T,size=UsdGeom.Cube(prim).GetSizeAttr().Get())
            elif prim.IsA(UsdGeom.Cylinder):
                c=UsdGeom.Cylinder(prim);b=primitive_bounds('cylinder',T,radius=c.GetRadiusAttr().Get(),height=c.GetHeightAttr().Get(),axis={'X':0,'Y':1,'Z':2}[c.GetAxisAttr().Get()])
            elif prim.IsA(UsdGeom.Sphere):b=primitive_bounds('sphere',T,radius=UsdGeom.Sphere(prim).GetRadiusAttr().Get())
            if b:
                if prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False:collision.append(b)
                if UsdGeom.Imageable(prim).ComputeVisibility()!='invisible':visual.append(b)
    exact_object_bounds[body_path]={'visual':union_bounds(visual),'collision':union_bounds(collision)}
def canonical(value):
    if isinstance(value,Sdf.AssetPath):return str(Path(value.resolvedPath or value.path).resolve()) if value.path else ''
    if type(value).__name__=='AssetPathArray':return [canonical(v) for v in value]
    return str(value)
signature=[]
for prim in stage.Traverse():
    path=str(prim.GetPath())
    if any(path==p or path.startswith(p+'/') for p in ('/Render','/Orchestrator','/Replicator')):continue
    signature.append([path,prim.GetTypeName(),list(prim.GetAppliedSchemas()),sorted((a.GetName(),canonical(a.Get()),[str(p) for p in a.GetConnections()]) for a in prim.GetAttributes()),sorted((r.GetName(),[str(p) for p in r.GetTargets()]) for r in prim.GetRelationships())])
scene_signature=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
layer=stage.GetRootLayer();root_specs=[{'name':p.name,'active':p.active,'property_count':len(p.properties),'child_count':len(p.nameChildren)} for p in layer.rootPrims]
report={'support_geometry_version':'used_vertices_and_analytic_support/v1','exact_object_bounds':exact_object_bounds,'scene_signature_without_runtime_graphs':scene_signature,'root_layer_sublayers':list(layer.subLayerPaths),'root_layer_specs':root_specs,'artifact_sha256':artifact_sha256,'joints':joints,'cameras':list(camera_properties),'camera_properties':camera_properties,'object_world_positions':poses,'status':'passed' if not missing and not outside else 'failed','outside':outside,'missing':missing,'layers':dependencies,'rigid_bodies':[str(p.GetPath()) for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)],'physics_scenes':[str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]}
report['articulation_base_positions']=articulation_bases
out.write_text(json.dumps(report,indent=2));print(json.dumps({'status':report['status'],'body_count':len(report['rigid_bodies'])}));sys.exit(0 if report['status']=='passed' else 1)
