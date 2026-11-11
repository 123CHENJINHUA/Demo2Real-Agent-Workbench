"""Exact affine support bounds; rotated local AABBs can include empty corners."""
import numpy as np

def mesh_bounds(points,indices,T):
    points=np.asarray(points,float);indices=np.asarray(indices,int);T=np.asarray(T,float)
    if not len(indices):raise ValueError('Mesh has no referenced vertices')
    world=points[np.unique(indices)]@T[:3,:3].T+T[:3,3]
    return {'min':world.min(axis=0).tolist(),'max':world.max(axis=0).tolist()}

def primitive_bounds(kind,T,*,size=None,radius=None,height=None,axis=2):
    T=np.asarray(T,float);A=T[:3,:3];center=T[:3,3]
    if kind=='cube':extent=np.abs(A).sum(axis=1)*float(size)/2
    elif kind=='sphere':extent=np.linalg.norm(A,axis=1)*float(radius)
    elif kind=='cylinder':
        others=[i for i in range(3) if i!=axis];extent=np.abs(A[:,axis])*float(height)/2+np.linalg.norm(A[:,others],axis=1)*float(radius)
    else:raise ValueError('Unsupported exact bounds shape: '+kind)
    return {'min':(center-extent).tolist(),'max':(center+extent).tolist()}

def union_bounds(items):
    if not items:return None
    return {'min':np.min([b['min'] for b in items],axis=0).tolist(),'max':np.max([b['max'] for b in items],axis=0).tolist()}
