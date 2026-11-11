"""Retained engineering functional geometry; no action execution or devices."""
from pathlib import Path
import json
import numpy as np
from scipy.spatial.transform import Rotation
ROOT=Path(__file__).resolve().parents[3]

def profile():
    return json.loads((ROOT/'assets/objects/connector/keyed_physics.json').read_text())


def wrap_pi(angle):
    return (angle+np.pi)%(2*np.pi)-np.pi

def retained_face_areas(relative,config):
    """Barb rear-face area behind the lip overlapping its retaining shoulder.

    A tilted loaded face can contact the lip at one edge. Requiring its entire
    plane behind the lip would incorrectly reject that physical contact.
    """
    c=config;areas=[]
    def clip(poly,normal,limit):
        output=[]
        for a,b in zip(poly,poly[1:]+poly[:1]):
            da,db=np.dot(normal,a)-limit,np.dot(normal,b)-limit
            if da<=0:output.append(a)
            if (da<=0)!=(db<=0):output.append(a+(b-a)*da/(da-db))
        return output
    for sign in (1.,-1.):
        face=[(relative@np.array([c['latch_head_back_x_m'],sign*r,z,1.]))[:3] for r,z in
              [(c['latch_inner_radius_m'],-c['latch_half_width_m']),
               (c['latch_outer_radius_m'],-c['latch_half_width_m']),
               (c['latch_outer_radius_m'],c['latch_half_width_m']),
               (c['latch_inner_radius_m'],c['latch_half_width_m'])]]
        side=1. if np.mean(face,axis=0)[2]>0 else -1.
        for normal,limit in [(np.array([1,0,0]),c['retaining_lip_x_m']+.0001),
                (np.array([0,1,0]),c['slot_half_width_m']), (np.array([0,-1,0]),c['slot_half_width_m']),
                (np.array([0,0,side]),c['slot_inner_radius_m']-.00005),
                (np.array([0,0,-side]),-c['bore_radius_m']+.0001)]:
            if len(face)<3:break
            face=clip(face,normal,limit)
        area=0. if len(face)<3 else .5*np.linalg.norm(sum((np.cross(a,b) for a,b in zip(face,face[1:]+face[:1])),np.zeros(3)))
        areas.append(float(area))
    return areas

def keyed_error(objects, target_roll=None, config=None):
    """Full body-relative key datum. +X axes same, male end -X/female end +X.

    The engineered guide datums are on the body axis. The offset visual pin is
    allowed for by the measured larger receiver bore, not silently recentered.
    """
    c=profile() if config is None else config
    male,female=objects
    rel=np.linalg.inv(female)@male
    roll=float(np.arctan2(rel[2,1],rel[1,1]))
    candidates=np.radians(c['roll_candidates_deg'])
    chosen=float(candidates[np.argmin(abs(wrap_pi(roll-candidates)))]) if target_roll is None else float(target_roll)
    depth=float(c['nominal_tip_to_mouth_m']-rel[0,3])
    axis=float(np.arccos(np.clip(rel[0,0],-1,1)))
    roll_error=float(wrap_pi(roll-chosen))
    # The trailing barb face must cross the retaining lip, not merely its tip.
    rear_point=rel@np.array([c['latch_head_back_x_m'],0.,0.,1.])
    lateral=float(np.linalg.norm(rear_point[1:3]))
    engagement=float(c['retaining_lip_x_m']-rear_point[0])
    hooks=[]
    for sign in (1.,-1.):
        face=np.array([[c['latch_head_back_x_m'],sign*r,z,1.] for r in
            (c['latch_inner_radius_m'],c['latch_outer_radius_m']) for z in (-c['latch_half_width_m'],c['latch_half_width_m'])])@rel.T
        side=1. if np.mean(face[:,2])>0 else -1.
        radial=side*face[:,2]
        hooks.append(bool(np.max(face[:,0])<c['retaining_lip_x_m']+.0001
            and np.max(abs(face[:,1]))<c['slot_half_width_m']+.0002
            and radial.min()<c['slot_inner_radius_m']-.00005
            and radial.max()>c['slot_inner_radius_m']+.0003))
    areas=retained_face_areas(rel,c)
    return dict(depth_m=depth,lateral_error_m=lateral,angle_rad=axis,
                roll_rad=roll,roll_error_rad=roll_error,target_roll_rad=chosen,
                latch_engagement_m=engagement,nominal_hooks_caught=bool(all(hooks)),
                retained_face_area_m2=areas,retained_hooks_caught=bool(min(areas)>.02e-6))

def keyed_success(error, config=None):
    c=profile() if config is None else config
    return bool(c['lock_min_depth_m']<=error['depth_m']<=c['lock_max_depth_m']
        and error['lateral_error_m']<.0012 and error['angle_rad']<.06
        and abs(error['roll_error_rad'])<c['evaluation_roll_rad'] and error['latch_engagement_m']>0.
        and error.get('nominal_hooks_caught',False))

def keyed_retained(error, config=None):
    """Both barb faces remain captured after releasing the alignment fixture.

    Passive mating clearance permits angular play after jaw release. Retention
    is determined by finite contact area of EACH barb face behind the lip and
    overlapping its retaining shoulder, not the servo's angular tolerance.
    Spring return and the jaw-release test are checked separately by callers.
    """
    c=profile() if config is None else config
    return bool(c['lock_min_depth_m']<=error['depth_m']<=c['lock_max_depth_m']
        and error['latch_engagement_m']>0. and error.get('retained_hooks_caught',False))

def female_rectangles(region,config=None):
    """Greedy non-overlapping boxes for a concave, through-bored cross section.

    Exact slot boundary coordinates are included; the circular bore is enlarged
    conservatively by at most one grid cell. No convex hull fills the channels.
    """
    c=profile() if config is None else config
    y=c['front_outer_y_m'];z=c['front_outer_z_m'] if region=='front' else c['window_outer_z_m']
    ys=np.unique(np.r_[np.linspace(-y,y,int(np.ceil(2*y/c['collision_grid_m']))+1),[-c['slot_half_width_m'],0.,c['slot_half_width_m']]])
    zs=np.unique(np.r_[np.linspace(-z,z,int(np.ceil(2*z/c['collision_grid_m']))+1),
        [-c['slot_inner_radius_m'],c['slot_inner_radius_m'],-c['slot_outer_radius_m'],c['slot_outer_radius_m'],
         -c['window_inner_radius_m'],c['window_inner_radius_m']]])
    zs=zs[(zs>=-z)&(zs<=z)]
    yc=(ys[:-1]+ys[1:])/2;zc=(zs[:-1]+zs[1:])/2
    Y,Z=np.meshgrid(yc,zc,indexing='ij')
    # Use each cell's point closest to the bore center: retain a cell only when
    # its entire rectangle lies outside the bore, preserving insertion space.
    near_y=np.where(ys[:-1]*ys[1:]<=0,0,np.minimum(abs(ys[:-1]),abs(ys[1:])))
    near_z=np.where(zs[:-1]*zs[1:]<=0,0,np.minimum(abs(zs[:-1]),abs(zs[1:])))
    solid=near_y[:,None]**2+near_z[None,:]**2>=c['bore_radius_m']**2
    if region=='front':slot=(abs(Y)<c['slot_half_width_m'])&(abs(Z)>c['slot_inner_radius_m'])&(abs(Z)<c['slot_outer_radius_m'])
    else:slot=(abs(Y)<c['slot_half_width_m'])&(abs(Z)>c['window_inner_radius_m'])
    remaining=solid&~slot
    boxes=[]
    for i in range(len(yc)):
        for j in range(len(zc)):
            if not remaining[i,j]:continue
            j1=j+1
            while j1<len(zc) and remaining[i,j1]:j1+=1
            i1=i+1
            while i1<len(yc) and remaining[i1,j:j1].all():i1+=1
            remaining[i:i1,j:j1]=False
            boxes.append((float(ys[i]),float(ys[i1]),float(zs[j]),float(zs[j1])))
    return boxes

def latch_profile(config=None):
    c=profile() if config is None else config
    front,back=c['latch_head_front_x_m'],c['latch_head_back_x_m']
    return np.array([[front,.0067],[back,c['latch_inner_radius_m']],
                     [back,c['latch_outer_radius_m']],[front,.0074]])
