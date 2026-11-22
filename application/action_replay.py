"""Expose run-scoped offline expert evidence through a read-only playback interface."""
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from contracts.artifacts import run_directory
from modules.evaluation.action_trace import failed_lift, first_fall, review_phase, search_focus
from modules.motion_replay.kinematics import joint_positions_world


def _local(root, reference):
    path=(root/reference).resolve()
    if not path.is_relative_to(root):
        raise ValueError('训练记录引用超出项目目录')
    return path


def _json(path):
    return json.loads(path.read_text())


def _bound(root, bundle, reference):
    path=_local(root,reference)
    expected=bundle['hashes'].get(reference)
    if not expected or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError('回放所需的动作计划或场景快照与运行记录不符')
    return path


def _index(root, run, live_ref):
    task=next((item for item in run['tasks'] if item['stage']=='simulation'),{})
    result=task.get('result') or {}
    path_ref=live_ref or result.get('episode_report_path')
    if not path_ref:
        raise FileNotFoundError('当前运行尚未发布专家回合')
    index=_local(root,path_ref)
    simulation=_local(root,Path(run_directory(run))/'simulation')
    if index.name!='episodes.json' or index.parent.parent!=simulation or not index.parent.name.startswith('action_program_'):
        raise ValueError('专家回合不属于当前运行')
    return index,_json(index) if index.is_file() else []


def overview(root, run, live_ref):
    index,rows=_index(root,run,live_ref)
    scored=index.with_name('learning.json')
    if scored.is_file():
        learning=_json(scored)
    else:
        # Earlier versions saved checks per trial and selected by complete passes, then passed checks.
        training={}
        for row in rows:
            if row['trial'].startswith(('方案 ','仿真迭代 ','接近修正 ')):
                training.setdefault(row['trial'],[]).append(row['assessment'])
        candidates=[{'label':label,'passed':sum(item['passed'] for item in items),'total':len(items),
                     'mean_checks':sum(item['score'] for item in items)/len(items)} for label,items in training.items()]
        frozen=index.with_name('action_expert.npz').is_file() and bool(candidates)
        winner=max(candidates,key=lambda item:(item['passed']/item['total'],item['mean_checks']),default=None)
        learning={'candidates':candidates,'selected':winner['label'] if frozen else None,'frozen':frozen}
        if frozen:
            with np.load(index.with_name('action_expert.npz'),allow_pickle=False) as saved:
                if 'phase_translation_m' in saved:
                    winner['corrections']=np.column_stack((saved['phase_translation_m'],saved['phase_rotation_deg'])).tolist()
    return {'attempt':index.parent.name,'learning':learning,'episodes':[
        {'index':i,'trial':row['trial'],'layout':row['case']['id'],'passed':row['assessment']['passed'],
         'score':row['assessment']['score'],'checks':len(row['assessment']['checks']),
         'termination':row.get('termination'),'reused_from':row.get('reused_from')}
        for i,row in enumerate(rows)]}


def _body(obj):
    size=obj.get('preview_bounds_size_m')
    if not size and obj.get('size_m'):
        size=(np.asarray(obj['size_m'])*obj['scale']).tolist()
    return {'id':obj['id'],'label':obj.get('label') or obj['id'],'size':size,
            'boxes':(obj.get('collision') or {}).get('boxes',[])}


def _trace_bytes(index, row):
    trace_path=(index.parent/row['trace']).resolve()
    if trace_path.parent!=index.parent or trace_path.suffix!='.npz':
        raise ValueError('回合轨迹引用无效')
    raw=trace_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row['trace_sha256']:
        raise ValueError('回合轨迹与记录的哈希不符')
    return raw


def _experiment(root, index):
    bundle=_json(index.with_name('experiment.json'))
    manifest=_json(_bound(root,bundle,bundle['manifest']))
    spec=_json(_bound(root,bundle,manifest['spec_snapshot_path']))
    program=_json(_bound(root,bundle,bundle['program_path']))
    acceptance=_json(_bound(root,bundle,bundle['acceptance_path']))
    return bundle,manifest,spec,program,acceptance


def trial_review(root, output, result):
    index=_local(root,result['episode_report_path'])
    if index.name!='episodes.json' or index.parent.parent!=output.resolve() or not index.parent.name.startswith('action_program_'):
        raise ValueError('回合证据不属于当前阶段')
    bundle,_,spec,program,acceptance=_experiment(root,index)
    rows=_json(index)
    failures=[row for row in rows if not row['assessment']['passed']]
    if not failures:
        return []
    selected=result.get('selected_candidate')
    primary=max(failures,key=lambda row:(row.get('mode')=='independent', row['trial']==selected,
                                        row['case']['id']=='demonstrated_layout'))
    latest=next((row for row in reversed(failures) if row['trace']!=primary['trace']
                 and row['case']['id']==primary['case']['id']),None)
    bodies=[_body(obj) for obj in spec['objects']]
    sizes={body['id']:body['size'] for body in bodies if body['size'] is not None}
    trial_rows=[primary] if latest is None else [primary,latest]
    review=[]
    for row in trial_rows:
        termination=row.get('termination') or {}
        phase=termination.get('phase')
        if phase is None:
            phase=search_focus([row['assessment']],len(program['phases']))
        checks=row['assessment']['checks']
        if len(checks)!=len(acceptance['checks']):
            raise ValueError('回合报告与验收快照不匹配')
        with np.load(io.BytesIO(_trace_bytes(index,row)),allow_pickle=False) as trace:
            duration=float(row['planned_duration_s']) if 'planned_duration_s' in row else float(trace['times_s'][-1])
            data=review_phase(trace,program,acceptance,sizes,spec['scene']['table'],duration,phase,checks)
        review.append({'trial':row['trial'],'layout':row['case']['id'],'scene_manifest':bundle['manifest'],
                       'trace':(index.parent/row['trace']).relative_to(root).as_posix(),
                       'trace_sha256':row['trace_sha256'],'termination':termination,'observations':data})
    return review


def frames(root, run, live_ref, episode, attempt):
    index,rows=_index(root,run,live_ref)
    if attempt!=index.parent.name or not 0<=episode<len(rows):
        raise FileNotFoundError('回合尚未发布或训练已进入新尝试')
    row=rows[episode]
    raw=_trace_bytes(index,row)
    bundle,_,spec,program,acceptance=_experiment(root,index)
    objects={obj['id']:obj for obj in spec['objects']}
    table=spec['scene']['table']
    with np.load(io.BytesIO(raw),allow_pickle=False) as trace:
        time=trace['times_s']
        joints=trace['actual_joints']
        command=trace['commanded_gripper_m']
        duration=float(row.get('planned_duration_s',time[len(command)-1]))
        indices=np.unique(np.linspace(0,len(time)-1,min(len(time),900),dtype=int))
        object_ids=trace['object_ids'].tolist()
        arm_points=[]
        for side,arm_id in enumerate(('left','right')):
            arm=next(arm for arm in spec['robot']['arms'] if arm['id']==arm_id)
            urdf=_bound(root,bundle,arm['urdf_path'])
            arm_points.append(joint_positions_world(urdf,arm['base_pose'],joints[indices,side,:7]))
        contact_logged='contact_time_s' in trace.files
        contacts=[]
        if contact_logged:
            contacts=[{'time_s':round(time_s,4),'actors':names,'impulse_ns':round(impulse,4)} for time_s,names,impulse in zip(
                trace['contact_time_s'].tolist(),trace['contact_actors'].tolist(),trace['contact_impulse_ns'].tolist())]
        assessment=row['assessment']
        fall=assessment.get('early_fall') or first_fall(trace,program,acceptance,table,duration)
        lost=assessment.get('lift_loss')
        if lost is None and not fall and not assessment['passed']:
            phase=search_focus([assessment],len(program['phases']))
            checks=[check | {'object_id':rule['object_id'],'relative_to':rule['relative_to']}
                    for check,rule in zip(assessment['checks'],acceptance['checks'])]
            lost=failed_lift(trace,program,table,duration,{'checks':checks},phase)
        return {'objects':[_body(objects[key]) for key in object_ids],
                'table':table,'phases':[{'name':step['name'],'end_s':step['end_fraction']*duration} for step in program['phases']],
                'time':time[indices].round(4).tolist(),'pose':trace['object_m'][indices].round(5).tolist(),
                'quaternion':trace['object_wxyz'][indices].round(6).tolist(),
                'arms':np.stack(arm_points,axis=1).round(5).tolist(),'fingers':trace['finger_m'][indices].round(5).tolist(),
                'gripper_actual':(1000*np.abs(joints[indices,:,7]-joints[indices,:,8])).round(3).tolist(),
                'gripper_command':(1000*command[np.minimum(indices,len(command)-1)]).round(3).tolist(),
                'early_fall':fall,'lift_loss':lost,'checks':assessment['checks'],'offsets':row['case']['offsets'],
                'contacts_logged':contact_logged,'contacts':contacts}
