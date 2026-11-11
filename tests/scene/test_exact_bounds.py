import numpy as np
from modules.scene_validation.geometry_bounds import mesh_bounds,primitive_bounds

def test_unused_mesh_points_are_not_visible_geometry():
    b=mesh_bounds([[0,0,0],[1,0,0],[0,1,0],[0,0,-100]],[0,1,2],np.eye(4))
    assert b['min'][2]==0

def test_rotated_cylinder_excludes_empty_bounding_box_corners():
    a=np.pi/4;T=np.eye(4);T[:3,:3]=[[1,0,0],[0,np.cos(a),-np.sin(a)],[0,np.sin(a),np.cos(a)]];T[2,3]=2
    b=primitive_bounds('cylinder',T,radius=1,height=4,axis=0)
    assert abs(b['min'][2]-1)<1e-10
    conservative=2-abs(T[2,1])-abs(T[2,2]);assert conservative<b['min'][2]-.4

def test_cube_affine_support_uses_scale_and_translation():
    T=np.diag([2.,3.,4.,1.]);T[:3,3]=[1,2,3]
    assert primitive_bounds('cube',T,size=2)=={'min':[-1.,-1.,-1.],'max':[3.,5.,7.]}
