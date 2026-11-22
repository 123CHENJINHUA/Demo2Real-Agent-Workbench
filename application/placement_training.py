"""Orchestrate fitting, simulation-guided selection and independent holdout trials."""
import hashlib
import json

import numpy as np
from scipy.spatial.transform import Rotation

from modules.evaluation.placement_expert import assess
from modules.learning.placement_expert import actions, fit
from modules.simulation.isaac.placement_rollout import PlacementRollouts


def train(root, out, plan, app):
    experiment = plan['experiment']
    manifest = json.loads((root/plan['manifest']).read_text())
    spec = json.loads((root/manifest['spec_snapshot_path']).read_text())
    by_id = {obj['id']:obj for obj in spec['objects']}
    arms = {arm['id']:arm for arm in spec['robot']['arms']}
    panel = by_id[experiment['objects']['panel']]
    panel_pose = panel['pose'].copy()
    q = np.asarray(panel_pose['quaternion_wxyz'])
    axes = Rotation.from_quat(q[[1,2,3,0]])*Rotation.from_euler('z',experiment.get('object_frame_z_deg',{}).get('panel',0.),degrees=True)
    panel_pose['quaternion_wxyz'] = axes.as_quat()[[3,0,1,2]].tolist()
    source,goal = (by_id[experiment['objects'][name]]['pose']['position_m'] for name in ('source','goal'))
    with np.load(root/plan['trajectory'],allow_pickle=False) as tape:
        if tape['arms'].tolist()!=['left','right']:
            raise ValueError('Unexpected recorded arm order')
        model = fit(root,[arms['left'],arms['right']],tape['timestamps_s'],tape['joint_positions_rad'],
                    tape['gripper_feedback'],panel_pose,experiment['sizes_m']['panel'],source,goal)
    print('PLACEMENT_EVENT '+json.dumps({'message':'加载可进行接触试验的 Isaac 物理场景'},ensure_ascii=False),flush=True)
    prepared = out/'rollout_scene.usdc'
    runner = PlacementRollouts(root/manifest['scene_path'],prepared,manifest,experiment,app)
    rows = []
    rng = np.random.default_rng(5521)
    total = 4*len(experiment['train'])+2*len(experiment['test'])

    def batch(kind, replay_model, bias, cases, conditioned):
        scores = []
        for case in cases:
            name = kind+'_'+case['id']
            label = {'baseline':'固定回放留出测试','learned':'学习后策略留出测试'}.get(kind,'训练位置的物理试验')
            payload = {'message':label,'completed':len(rows),'total':total,'unit':'回合'}
            print('PLACEMENT_EVENT '+json.dumps(payload,ensure_ascii=False),flush=True)
            source_xy,goal_xy = (case['source_xy_m'],case['goal_xy_m']) if conditioned else ([0.,0.],[0.,0.])
            command = actions(replay_model,source_xy,goal_xy,bias)
            trace = runner.execute(case,replay_model,command)
            path = out/(name+'.npz')
            np.savez_compressed(path,**trace,commanded_joint_rad=command)
            evidence = assess(trace,experiment)
            scores.append(evidence)
            rows.append({'candidate':kind,'case':case,'input_positions_known':conditioned,'trace_path':path.relative_to(root).as_posix(),
                         'trace_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'assessment':evidence})
            (out/'episodes.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,allow_nan=False))
        return scores

    zero = np.zeros((2,3))
    best_score, best = -np.inf, zero
    history = []
    for step in range(4):
        bias = ([zero,model['grasp_alignment_seed_m']][step] if step<2
                else np.clip(best+rng.normal(0.,.020,(2,3)),-.16,.16))
        training = batch(f'candidate_{step}',model,bias,experiment['train'],True)
        score = float(np.mean([result['reward'] for result in training]))
        history.append({'candidate':step,'bias_m':bias.tolist(),'train_mean_reward':score})
        if score>best_score:
            best_score,best = score,bias
    checkpoint = out/'placement_expert.npz'
    np.savez_compressed(checkpoint,**model,placement_bias_m=best)
    frozen = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    with np.load(checkpoint,allow_pickle=False) as saved:
        frozen_model = {name:saved[name] for name in saved.files}
    baseline = batch('baseline',frozen_model,zero,experiment['test'],False)
    test_scores = batch('learned',frozen_model,frozen_model['placement_bias_m'],experiment['test'],True)
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=frozen:
        raise ValueError('Expert weights changed during held-out evaluation')
    print('PLACEMENT_EVENT '+json.dumps({'message':'完成留出位置评估','completed':len(rows),'total':total,'unit':'回合'},ensure_ascii=False),flush=True)
    passed = sum(row['passed'] for row in test_scores)
    reference = sum(row['passed'] for row in baseline)
    total_test = len(test_scores)
    summary = f'学习与留出测试已完成，{passed}/{total_test} 个未见位置通过接触和松手保持检查'
    failure = {'error':summary,'failure_category':'physics_contact','failure_stage':'simulation_test',
               'evidence_paths':[(out/'episodes.json').relative_to(root).as_posix()]} if passed!=total_test else {}
    return {'status':'succeeded' if passed==total_test else 'failed','reason':summary,**failure,
            'scope':'offline_physx_proxy_panel_placement','generalization_scope':'held_out_xy_positions_of_source_group_and_goal',
            'task_spec_hash':experiment['task_spec_hash'],'completed_trials':len(rows),'training_trials':4*len(experiment['train']),
            'training_history':history,'demonstration_fit_rmse_rad':float(model['fitting_rmse_rad']),
            'heldout_passed':passed,'heldout_total':total_test,'fixed_reference_heldout_passed':reference,
            'heldout_mean_xy_error_m':float(np.mean([row['panel_final_xy_error_m'] for row in test_scores])),
            'fixed_reference_heldout_mean_xy_error_m':float(np.mean([row['panel_final_xy_error_m'] for row in baseline])),
            'position_amplitude_m':experiment['position_amplitude_m'],'source_episode_index':plan['episode_index'],
            'enabled_robot_colliders':runner.contact_colliders,'rollout_scene_path':prepared.relative_to(root).as_posix(),
            'checkpoint_path':checkpoint.relative_to(root).as_posix(),'checkpoint_sha256':frozen,
            'episode_report_path':(out/'episodes.json').relative_to(root).as_posix(),
            'hardware_verified':False,'hardware_commands_sent':0,'contact_execution_authorized':False}
