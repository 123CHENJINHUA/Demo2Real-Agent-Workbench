"""Portable material adaptation for business USD snapshots, no runtime path borrowing."""
from pathlib import Path

def make_portable_materials(saved):
    from pxr import Sdf,UsdShade,Gf
    # Portable visual material adaptation: retain scalar URDF appearance without
    # baking Isaac's external MDL module path into a business scene artifact.
    material_adaptations=[]
    for prim in list(saved.Traverse()):
        source=prim.GetAttribute('info:mdl:sourceAsset')
        if not source:continue
        value=source.Get()
        if not isinstance(value,Sdf.AssetPath) or not value.path:continue
        if Path(value.path).name!='OmniPBR.mdl':raise ValueError('Unreviewed external material module '+value.path)
        old=UsdShade.Shader(prim)
        def parameter(name,default):
            attr=old.GetInput(name)
            result=attr.Get() if attr else None
            return result if result is not None else default
        color=parameter('diffuse_color_constant',Gf.Vec3f(.7,.7,.7));rough=float(parameter('reflection_roughness_constant',.5));metal=float(parameter('metallic_constant',0.))
        path=str(prim.GetPath());parent=prim.GetParent();material=UsdShade.Material(parent)
        if not material:raise ValueError('MDL shader has no portable material parent')
        saved.RemovePrim(path);shader=UsdShade.Shader.Define(saved,path);shader.CreateIdAttr('UsdPreviewSurface');shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color));shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough);shader.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal);shader.CreateOutput('surface',Sdf.ValueTypeNames.Token)
        parent.RemoveProperty('outputs:mdl:surface');material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
        material_adaptations.append({'shader_path':path,'from_module':'OmniPBR.mdl','to':'UsdPreviewSurface','scope':'scalar appearance approximation; physics unchanged'})
    return material_adaptations
