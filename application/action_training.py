"""Bind a frozen task program to reusable trajectory learning and offline physics."""
import hashlib
import json

import numpy as np
from scipy.spatial.transform import Rotation

from modules.evaluation.action_program import assess
from modules.evaluation.action_trace import (
    TrialScreen, eligible_for_validation, failed_lift, first_fall, first_motion_fault, first_support_loss,
    measurable_prefix, partial_progress, search_focus,
)
from modules.learning.action_program import actions, approach_offsets, clearance_window, fit, gripper_openings
from modules.learning.waypoint_expert import WaypointExpert
from modules.simulation.isaac.motion_rollout import MotionRollouts


def _publish(path, document):
    pending=path.with_suffix('.tmp')
    pending.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False))
    pending.replace(path)


def _layout_variants(program, spec, manifest, sizes):
    """Keep requested fixture groups together and avoid new initial overlaps."""
    groups=program['variation_groups']
    if not groups:
        return [],[]
    table=spec['scene']['table']
    center=np.asarray(table['position_m'][:2])
    half=np.asarray(table['size_m'][:2])/2
    bounds={}
    for key,pose in manifest['settled_poses'].items():
        if key not in sizes:
            continue
        rotation=Rotation.from_quat(np.asarray(pose['quaternion_wxyz'])[[1,2,3,0]]).as_matrix()
        extent=np.abs(rotation)@np.asarray(sizes[key])/2
        bounds[key]=(np.asarray(pose['position_m']),extent)
    if any(key not in bounds for group in groups for key in group):
        return [],[]
    memberships={key:index for index,group in enumerate(groups) for key in group}
    roles=(spec.get('scene_approximation') or {}).get('object_roles',{})
    for obj in spec['objects']:
        key=obj['id']
        support=obj.get('preview_support_id') or roles.get(key,{}).get('support_id')
        if support in bounds and memberships.get(key)!=memberships.get(support):
            return [],[]
    train_rng,test_rng=np.random.default_rng(2381),np.random.default_rng(7811)

    def variants(rng,label,low,high):
        found=[]
        for _ in range(54):
            offsets={}
            for group in groups:
                angle=rng.uniform(0,2*np.pi)
                shift=np.r_[rng.uniform(low,high)*np.array([np.cos(angle),np.sin(angle)]),0.]
                offsets.update({key:shift for key in group})
            if any(np.any(np.abs(bounds[key][0][:2]+delta[:2]-center)+bounds[key][1][:2]>half)
                   for key,delta in offsets.items()):
                continue
            if any(np.all(np.abs(a+offsets.get(left,0)-b-offsets.get(right,0))<ae+be)
                   and not np.all(np.abs(a-b)<ae+be)
                   for left,(a,ae) in bounds.items() for right,(b,be) in bounds.items() if left<right):
                continue
            found.append({'id':label+str(len(found)),'offsets':{key:delta.tolist() for key,delta in offsets.items()}})
            if len(found)==3:
                break
        return found

    return variants(train_rng,'train_',.007,.020),variants(test_rng,'holdout_',.023,.040)


def train(root,out,plan,app):
    manifest=json.loads((root/plan['manifest']).read_text())
    spec=json.loads((root/manifest['spec_snapshot_path']).read_text())
    objects={obj['id']:obj for obj in spec['objects']}
    roles=(spec.get('scene_approximation') or {}).get('object_roles',{})
    supports={identifier:obj.get('preview_support_id') or roles.get(identifier,{}).get('support_id')
              for identifier,obj in objects.items()}
    sizes={}
    for key,obj in objects.items():
        if obj.get('preview_bounds_size_m'):
            sizes[key]=obj['preview_bounds_size_m']
        elif obj.get('size_m'):
            sizes[key]=(np.asarray(obj['size_m'])*obj['scale']).tolist()
    program=json.loads((root/plan['program_path']).read_text())
    acceptance=json.loads((root/plan['acceptance_path']).read_text())
    arm_map={arm['id']:arm for arm in spec['robot']['arms']}
    arms=[arm_map[arm] for arm in ('left','right')]
    with np.load(root/plan['trajectory'],allow_pickle=False) as tape:
        if tape['arms'].tolist()!=['left','right']:
            raise ValueError('录制双臂关节顺序与场景不一致')
        model=fit(root,arms,tape)
    expert=WaypointExpert(root,arms,model,program['phases'],manifest['settled_poses'],manifest['robots'],sizes) if any(
        phase.get('waypoints') for phase in program['phases']) else None
    learning_mode='scene_waypoint_search' if expert else 'recorded_trajectory_adjustments'
    runner=MotionRollouts(root/manifest['scene_path'],out/'rollout_scene.usdc',manifest,app,record_contacts=True)
    train_cases,holdout=_layout_variants(program,spec,manifest,sizes)
    nominal={'id':'demonstrated_layout','offsets':{}}
    training=[nominal,*train_cases]
    evaluation=[nominal,*holdout]
    rows=[]
    rng=np.random.default_rng(9951)
    phase_count=len(program['phases'])
    duration=model['times_s'][-1]
    candidates=[]
    selected=None
    reference_by_layout={}
    table=spec['scene']['table']
    contacts=getattr(runner,'contacts',None)
    roots=[item['root_path'] for item in manifest.get('robots',[])]
    review_phase=plan.get('scene_recheck_phase')
    approach=plan.get('approach_contact')
    start_variants=[]
    offset_window=None
    if approach and not expert:
        target=approach['object_id']
        offset_window=clearance_window(model,program['phases'],approach)
        start_variants=approach_offsets(model,approach,manifest['robots'],
                                       manifest['settled_poses'].get(target),sizes.get(target))
        if not start_variants or (offset_window is None and not approach.get('initial_window')):
            return {'status':'blocked','reason':'无法用接触记录生成可复核的接近路径修正'}
    rounds=18 if expert else len(start_variants) or 4
    total=rounds*len(training)+2*len(evaluation)

    def progress(message,expected=total):
        print('PLACEMENT_EVENT '+json.dumps({'message':message,'completed':len(rows),'total':expected,'unit':'回合'},
                                           ensure_ascii=False),flush=True)

    def rollout(label,case,bias,mode,start_offset=None,window=None):
        retarget=mode!='reference'
        residuals=[]
        if expert and retarget:
            command,openings,residuals=expert.command(case['offsets'],bias)
        else:
            openings=(model.get('recorded_gripper_m',model['gripper_opening_m']).copy() if expert
                      else gripper_openings(model,program['phases'],retarget=retarget))
            command=actions(model,program['phases'],case['offsets'],bias,retarget=retarget,
                            start_offset_m=start_offset,offset_window_s=window)
        trial_model=model | {'gripper_opening_m':openings}
        layout=case['id']
        reference=reference_by_layout.get(layout) if mode=='reference' else None
        if (reference and rows[reference[0]]['case']['offsets']==case['offsets']
                and np.array_equal(command,reference[1]) and np.array_equal(openings,reference[2])):
            source=reference[0]
            row=rows[source] | {'trial':label,'mode':mode,'reused_from':source}
            progress(f'{label} · {layout} · 复用第 {source+1} 回合')
        else:
            progress(f'{label} · {layout}')
            screen=TrialScreen(manifest['settled_poses'],program,acceptance,table,sizes,duration,
                               contact_rows=(lambda:contacts.rows) if contacts else None,robot_roots=roots,supports=supports)
            screening=mode=='screen'
            trace=runner.execute(case['offsets'],trial_model,command,stop=screen.observe if screening else None)
            evidence=assess(trace,program,acceptance,sizes,duration,through_phase=screen.through_phase)
            fault=screen.early_fall if screening else first_fall(trace,program,acceptance,table,duration)
            if fault:
                evidence.update(early_fall=fault,passed=False)
            hit=screen.unexpected_contact if screening else (screen.inspect_contacts() if contacts else None)
            if hit:
                evidence.update(unexpected_contact=hit,passed=False)
            loss=screen.initial_support_loss if screening else first_support_loss(trace,program,supports,sizes,duration)
            if loss:
                evidence.update(initial_support_loss=loss,passed=False)
            evidence['simulated_duration_s']=float(trace['times_s'][-1])
            if expert and retarget:
                evidence['waypoint_residuals']=residuals
                if not eligible_for_validation(evidence):
                    phase=search_focus([evidence],phase_count)
                    if lost:=failed_lift(trace,program,table,duration,evidence,phase):
                        evidence['lift_loss']=lost
                    evidence['learning_feedback']=expert.observe(evidence,trace,residuals)
            evidence['approach_verified']=bool(review_phase is not None and contacts and
                not any(finding and finding['phase']<=review_phase for finding in (fault,hit,loss)) and
                not any(check['phase']<=review_phase and check['kind']!='unsupported' and not check['passed']
                        for check in evidence['checks']) and
                trace['times_s'][-1]>=program['phases'][review_phase]['end_fraction']*duration)
            name=f'{len(rows):03d}_{layout}.npz'
            path=out/name
            count=len(trace['times_s'])
            np.savez_compressed(path,**trace,commanded_joint_rad=command[:count],commanded_gripper_m=openings[:count])
            row={'trial':label,'case':case,'trace':name,'assessment':evidence,'mode':mode,'planned_duration_s':float(duration),
                 'termination':screen.termination,
                 'trace_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            if mode=='independent':
                reference_by_layout[layout]=(len(rows),command,openings)
        rows.append(row)
        _publish(out/'episodes.json',rows)
        return row['assessment']

    def execution_counts():
        return {'simulated_runs':sum('reused_from' not in row for row in rows),
                'reused_runs':sum('reused_from' in row for row in rows),
                'stopped_runs':sum(bool(row.get('termination')) for row in rows)}

    missing=[acceptance['criteria'][row['criterion']] for row in acceptance['checks'] if row['kind']=='unsupported']
    common={'scope':'offline_physx_task_action_program','learning_mode':learning_mode,
            'unmeasured_criteria':list(dict.fromkeys(missing)),
            'episode_report_path':(out/'episodes.json').relative_to(root).as_posix(),
            'hardware_verified':False,'hardware_commands_sent':0,'contact_execution_authorized':False}

    def findings(results,cases):
        falls=[row['early_fall']|{'layout':case['id']} for row,case in zip(results,cases) if row.get('early_fall')]
        message=''
        if falls:
            first=falls[0]
            label=objects[first['object_id']].get('label') or first['object_id']
            phase=program['phases'][first['phase']]['name']
            message=f'。{len(falls)} 个回合在抬升前出现跌落，首例在 {first["time_s"]:.2f} 秒（{phase}），{label}已低于工作台面'
        checks=[check for row in results for check in row['checks'] if check['evaluated'] and not check['passed']]
        return message,falls,checks[:30]

    def scene_feedback(nominal_result):
        finding=first_motion_fault(nominal_result)
        if finding:
            return {'scene_recheck':finding | {'kind':finding.get('kind','early_fall'),
                    'scene_manifest_path':plan['manifest'],'episode_report_path':common['episode_report_path'],
                    'source_scene_hash':manifest.get('task_spec_hash')}}
        return {}

    best=np.zeros((len(expert.knots),7) if expert else (phase_count,6))
    score=(-1,-1,-1,-1,-1,-1)
    best_results=[]
    first_trial={}
    focus=None
    best_start=None
    attempted=set()
    proposal_number=0
    for i in range(rounds):
        candidate=best.copy()
        repair=None
        parent=selected
        previous_feedback=None
        start_offset=start_variants[i] if start_variants else None
        if expert and i:
            previous_feedback=next((row.get('learning_feedback') for row in best_results if row.get('learning_feedback')),None)
            if previous_feedback is None:
                break
            for _ in range(24):
                proposal_number+=1
                candidate,repair=expert.propose(best,previous_feedback,proposal_number)
                if candidate.tobytes() not in attempted:
                    break
            else:
                break
        elif i and not start_variants:
            step=np.array([.012,.012,.012,5.,5.,5.])
            bound=np.array([.04,.04,.04,15.,15.,15.])
            candidate[focus]=np.clip(candidate[focus]+rng.normal(0,step),-bound,bound)
        label=f'仿真迭代 {i+1}' if expert else f'接近修正 {i+1}' if start_variants else f'方案 {i+1}'
        if expert:
            attempted.add(candidate.tobytes())
        outcome=[]
        for case in training:
            outcome.append(rollout(label,case,candidate,'screen',start_offset,offset_window))
            if not eligible_for_validation(outcome[-1]):
                break
        if i==0:
            first_trial=outcome[0]
        passed=sum(result['passed'] for result in outcome)
        screened=sum(eligible_for_validation(result) for result in outcome)
        prefix=float(np.mean([measurable_prefix(result,phase_count) for result in outcome]))
        mean_checks=float(np.mean([result['score'] for result in outcome]))
        next_focus=search_focus(outcome,phase_count)
        partial=float(np.mean([partial_progress(result,next_focus) for result in outcome])) if expert else 0.
        candidate_score=(screened,measurable_prefix(outcome[0],phase_count),prefix,mean_checks,
                         partial,outcome[0]['simulated_duration_s'] if expert else 0.)
        entry={'label':label,'focus_phase':approach['phase'] if approach else focus,'passed':passed,'total':len(outcome),
                           'screened':screened,'planned_layouts':len(training),'mean_prefix':prefix,'mean_checks':mean_checks,
                           'start_offset_m':start_offset.tolist() if start_offset is not None else None,
                           'offset_window_s':offset_window}
        if expert:
            entry.update(parent=parent,repair=repair,input_feedback=previous_feedback,
                         outcome_feedback=next((row.get('learning_feedback') for row in outcome if row.get('learning_feedback')),None),
                         focus_phase=repair['phase'] if repair else None,partial_progress=partial)
        else:
            entry['corrections']=candidate.tolist()
        candidates.append(entry)
        if candidate_score>score:
            best,score=candidate,candidate_score
            best_start=start_offset
            best_results=outcome
            selected=label
            focus=next_focus
        _publish(out/'learning.json',{'mode':learning_mode,'candidates':candidates,'selected':selected,'frozen':False})
        if ((i==0 and outcome[0].get('initial_support_loss')) or screened==len(training)
                or all(check['kind']=='unsupported' for check in acceptance['checks'])):
            break
        if not expert and not start_variants and (outcome[0].get('unexpected_contact') or {}).get('initial_window'):
            # Every current candidate starts at the same recorded pose.
            break

    if not eligible_for_validation(best_results[0]):
        message,falls,checks=findings(best_results,training)
        detail=program['phases'][focus]['name']
        hit=best_results[0].get('unexpected_contact')
        loss=best_results[0].get('initial_support_loss')
        measured=hit or falls or any(check['measured'] is not None for check in checks)
        reason=(f'候选方案未通过示教布局筛选（{detail}）' if measured else '示教布局缺少可判定的筛选证据')
        reason+='，未启动位置泛化验收'+message
        if hit:
            reason+=f'。{hit["time_s"]:.2f} 秒检测到非预期的机械臂接触'
        if loss:
            part=objects[loss['object_id']].get('label') or loss['object_id']
            support=objects[loss['support_id']].get('label') or loss['support_id']
            reason=f'{loss["time_s"]:.2f} 秒检测到{part}在接触前相对{support}失稳，已暂停动作搜索'
        elif start_variants:
            reason+=f'。已试验 {len(candidates)} 条接近避让轨迹'
        if expert and not loss:
            reason+=f'。已完成 {len(candidates)} 轮场景目标点试验'
        progress(reason,len(rows))
        return common | execution_counts() | scene_feedback(best_results[0]) | {
            'status':'failed' if measured and not loss else 'blocked','reason':reason,
            'validation_status':'not_started','generalization_evaluated':False,'checks':checks,'early_falls':falls,
            'test_passed':0,'test_total':0,'holdout_total':0,'selected_candidate':selected,
            'approach_verified':(best_results[0] if approach or expert else first_trial)['approach_verified']}

    checkpoint=out/'action_expert.npz'
    parameters={'waypoint_corrections':best} if expert else {
        'phase_translation_m':best[:,:3],'phase_rotation_deg':best[:,3:]}
    np.savez_compressed(checkpoint,**parameters,learning_mode=learning_mode,
                        start_offset_m=best_start if best_start is not None else np.zeros((len(model['arms']),3)),
                        offset_window_s=np.asarray(offset_window or []),
                        reference_rad=model['reference_rad'],times_s=model['times_s'],
                        gripper_opening_m=model['gripper_opening_m'],jacobian_inverse=model['inverse'],
                        joint_limits_rad=model['limits_rad'],arms=np.asarray(model['arms'],dtype=str))
    frozen=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    _publish(out/'learning.json',{'mode':learning_mode,'candidates':candidates,'selected':selected,'frozen':True})
    with np.load(checkpoint,allow_pickle=False) as saved:
        frozen_bias=saved['waypoint_corrections'] if expert else np.column_stack((saved['phase_translation_m'],saved['phase_rotation_deg']))
        frozen_start=saved['start_offset_m']
        frozen_window=saved['offset_window_s'].tolist() or None
    scores=[rollout('独立验证',case,frozen_bias,'independent',frozen_start,frozen_window) for case in evaluation]
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=frozen:
        raise ValueError('独立验证期间动作参数发生变化')
    baseline=[rollout('录制轨迹对照',case,frozen_bias,'reference') for case in evaluation]
    passed=sum(row['passed'] for row in scores)
    scope='示教与留出位置' if holdout else '示教布局'
    summary=f'{scope}仿真验收 {passed}/{len(scores)} 回合通过'
    message,early_falls,failed_checks=findings(scores,evaluation)
    progress(summary,len(rows))
    return common | execution_counts() | scene_feedback(scores[0]) | {'status':'succeeded' if passed==len(scores) else 'blocked' if missing else 'failed',
            'reason':summary+('，部分条件缺乏可测证据' if missing else '')+message,'validation_status':'completed',
            'generalization_evaluated':bool(holdout),
            'approach_verified':(best_results[0] if approach or expert else first_trial)['approach_verified'] and scores[0]['approach_verified'],
            'demonstrated_layout_passed':scores[0]['passed'],'holdout_passed':sum(row['passed'] for row in scores[1:]),
            'holdout_total':len(holdout),'checks':failed_checks[:30],
            'early_falls':early_falls,'selected_candidate':selected,
            'test_passed':passed,'test_total':len(scores),'baseline_passed':sum(row['passed'] for row in baseline),
            'checkpoint_path':checkpoint.relative_to(root).as_posix(),'checkpoint_sha256':frozen}
