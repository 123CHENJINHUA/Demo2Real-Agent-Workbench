"""Create bounded placement variants; never mutate a source task or dataset."""
from pathlib import Path
import copy,hashlib,json,math
import numpy as np
from scipy.spatial.transform import Rotation
from modules.scene_building.service import checked_spec
from modules.task_specs.service import generate,save

SUITES={'connector_phase_recovery_regression_v3':'config/experiments/connector_phase_recovery_regression_v3.json','connector_recovery_v2':'config/experiments/connector_recovery_v2.json','connector_recovery_student_v2':'config/experiments/connector_recovery_student_v2.json','connector_recovery_regression_v2':'config/experiments/connector_recovery_regression_v2.json','connector_small_v1':'config/experiments/connector_small_v1.json','connector_validation_v1':'config/experiments/connector_validation_v1.json','connector_recovery_v1':'config/experiments/connector_recovery_v1.json','connector_recovery_student_v1':'config/experiments/connector_recovery_student_v1.json'}
CAPACITIES={'connector_phase_recovery_regression_v3':3,'connector_recovery_v2':20,'connector_recovery_student_v2':6,'connector_recovery_regression_v2':2,'connector_small_v1':5,'connector_validation_v1':15,'connector_recovery_v1':18,'connector_recovery_student_v1':6}

def load_suite(root,identifier):
    root=Path(root).resolve()
    if identifier not in SUITES:raise ValueError('Unknown placement suite')
    path=root/SUITES[identifier];suite=json.loads(path.read_text())
    if suite.get('id')!=identifier or suite.get('evaluation_scope')!='action_only':raise ValueError('Invalid suite identity/scope')
    if suite.get('perception') not in ('simulator_ground_truth','simulated_pose_estimate') or suite.get('rotation_axis')!='world_Z':raise ValueError('Unsupported experiment modality')
    limit=suite['max_axis_translation_m'];angle=suite['max_yaw_deg']
    if not 0<=limit<=.01 or not 0<=angle<=5:raise ValueError('Small suite bounds exceeded')
    cases=suite.get('cases',[]);ids=set()
    if not 1<=len(cases)<=CAPACITIES[identifier]:raise ValueError('Suite exceeds registered case capacity')
    for case in cases:
        if case['id'] in ids:raise ValueError('Duplicate case ID')
        if case.get('recovery'):
            from contracts.recovery import checked_recovery
            checked_recovery(case['recovery'])
        ids.add(case['id']);xy=np.asarray(case['xy_offsets_m'],float);yaw=np.asarray(case['yaw_offsets_deg'],float)
        if xy.shape!=(2,2) or yaw.shape!=(2,) or not np.isfinite(xy).all() or not np.isfinite(yaw).all() or np.max(np.abs(xy))>limit+1e-10 or np.max(np.abs(yaw))>angle+1e-10:raise ValueError('Case placement exceeds declared bounds')
    if suite.get('implementation_lock_path'):
        from modules.experiments.locks import verify_lock
        verify_lock(root,suite['implementation_lock_path'])
    _,spec,missing=checked_spec(root,suite['baseline_spec_path'])
    if missing or spec['content_hash']!=suite['baseline_spec_hash'] or spec.get('profile_id')!='connector_keyed_v1':raise ValueError('Baseline task does not match frozen suite')
    return suite,hashlib.sha256(path.read_bytes()).hexdigest(),spec

def varied_objects(baseline,case):
    objects=copy.deepcopy(baseline['objects'])
    for i,obj in enumerate(objects):
        pose=obj['pose'];pose['position_m'][:2]=(np.asarray(pose['position_m'][:2])+case['xy_offsets_m'][i]).tolist()
        q=np.asarray(pose['quaternion_wxyz']);r=Rotation.from_quat(q[[1,2,3,0]])
        pose['quaternion_wxyz']=(Rotation.from_euler('z',case['yaw_offsets_deg'][i],degrees=True)*r).as_quat()[[3,0,1,2]].tolist()
    return objects

def create_variant(ctx):
    root=Path(ctx['root']).resolve();inp=ctx['input'];suite,digest,base=load_suite(root,inp['suite_id'])
    if digest!=inp['suite_sha256']:raise ValueError('Suite changed after submission')
    case=next((c for c in suite['cases'] if c['id']==inp['case_id']),None)
    if case is None:raise ValueError('Unknown declared case')
    source=base['source'];deps={'demo_import':{'manifest_path':source['manifest_path']},'geometry':{'report_path':source['geometry_report_path']},'task_analysis':{'report_path':source['proposal_path']},'experience':{'checklist_path':source['experience_checklist_path']}}
    run_dir=Path(ctx['output_dir']).parent
    first=generate(root,ctx['run_id'],dependencies=deps,profile_id=base['profile_id'],run_dir=run_dir);spec=first['spec']
    spec['objects']=varied_objects(base,case)
    spec['simulation_variant']={'suite_id':suite['id'],'suite_sha256':digest,'case':case,'baseline_spec_path':suite['baseline_spec_path'],'baseline_spec_hash':base['content_hash'],'pose_source':'declared_simulation_initial_conditions_not_camera_estimates'}
    spec['assumptions'].append('Declared small initial XY/yaw perturbation; no trajectory retraining or task parameter retuning.')
    result=save(root,ctx['run_id'],spec,expected_revision=first['spec']['revision'],run_dir=run_dir);spec=result['spec']
    ctx['emit']('placement.variant_created',{'case_id':case['id'],'spec_hash':spec['content_hash'],'spec_revision':spec['revision']})
    return {'status':'succeeded','spec_path':result['spec_path'],'spec_hash':spec['content_hash'],'revision':spec['revision'],'spec_revision':spec['revision'],'case_id':case['id'],'suite_sha256':digest}
