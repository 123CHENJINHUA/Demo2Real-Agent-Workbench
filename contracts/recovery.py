"""Explicit state-surrogate recovery contracts; separate from camera-only actor v1."""
import hashlib,json,math
OBSERVATION='recovery-state-24/v1'
ACTION='recovery-skill-5/v1'
LABELS=('continue','wait','regrasp_left','regrasp_right','needs_human')
FEATURES=tuple(f'{arm}.{field}' for arm in ('left','right') for field in ('visible','pose_age_s','confidence','grasp_drift_m','jaw_gap_m','object_height_m','object_speed_m_s','object_x_m','object_y_m','stable_s'))+('progress','wait_s','left_joint_margin_rad','right_joint_margin_rad')
SOURCE='simulated_pose_estimate'

def task_signature(root):
    from pathlib import Path
    profile=json.loads((Path(root)/'tasks/connector/profile.json').read_text())
    value={'assets':profile['asset_signatures'],'observation':OBSERVATION,'action':ACTION,'features':FEATURES,'labels':LABELS,'source':SOURCE}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def checked_recovery(value):
    if not isinstance(value,dict):raise ValueError('Explicit recovery configuration required')
    allowed={'kind','arm','after_s','shift_m','seed','pose_noise_m','pose_delay_s','rotation_noise_rad','policy_path','skill_version','phase_version','trigger_stage','trigger_depth_m'}
    if set(value)-allowed:raise ValueError('Unknown recovery configuration field')
    if value.get('kind') not in ('none','push','drop','relocate','occlude','remove','grip_slip','grip_release','dual_release') or value.get('arm') not in (0,1):raise ValueError('Unknown disturbance/arm')
    for k,hi in [('after_s',5),('pose_noise_m',.001),('pose_delay_s',.1),('rotation_noise_rad',.01)]:
        x=value.get(k,0)
        if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=hi:raise ValueError('Recovery bound exceeded: '+k)
    shift=value.get('shift_m',[])
    if len(shift)!=2 or any(not isinstance(x,(int,float)) or not math.isfinite(x) or abs(x)>.05 for x in shift):raise ValueError('Invalid external shift')
    if isinstance(value.get('seed'),bool) or not isinstance(value.get('seed'),int) or not 0<=value['seed']<2**31:raise ValueError('Invalid recovery seed')
    if value.get('skill_version','legacy_v1') not in ('legacy_v1','contact_center_v2'):raise ValueError('Unknown recovery skill version')
    if value.get('phase_version') not in (None,'insertion_guard_v3'):raise ValueError('Unknown phase supervisor')
    if value.get('trigger_stage','coordination') not in ('coordination','insertion'):raise ValueError('Unknown disturbance stage')
    if 'trigger_depth_m' in value and (not isinstance(value['trigger_depth_m'],(int,float)) or not math.isfinite(value['trigger_depth_m']) or not -.012<=value['trigger_depth_m']<=.023):raise ValueError('Invalid trigger depth')
    return dict(value)
