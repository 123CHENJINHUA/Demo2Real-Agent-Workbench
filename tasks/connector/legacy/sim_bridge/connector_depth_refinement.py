"""Small robust point-to-plane updates using RGBD/CAD correspondences only."""
import numpy as np
from scipy.spatial.transform import Rotation


def increment(points,normals,observed):
    """Return a bounded local SE(3) increment, with rotation scaled to metres."""
    length=.02
    residual=np.sum(normals*(observed-points),axis=1)
    weights=np.minimum(1.,.0003/np.maximum(abs(residual),1e-9))
    jacobian=np.column_stack((normals,np.cross(points,normals)/length))
    root=np.sqrt(weights)
    A=jacobian*root[:,None];b=residual*root
    delta=np.linalg.solve(A.T@A+np.diag([.05]*3+[.5]*3),A.T@b)
    translation=delta[:3];rotation=delta[3:]/length
    translation*=min(1.,.0005/max(np.linalg.norm(translation),1e-12))
    rotation*=min(1.,.015/max(np.linalg.norm(rotation),1e-12))
    update=np.eye(4);update[:3,3]=translation;update[:3,:3]=Rotation.from_rotvec(rotation).as_matrix()
    return update
