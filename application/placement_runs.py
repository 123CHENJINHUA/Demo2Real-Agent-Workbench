"""Compose a fixed experimental suite into real persisted worker jobs."""
from pathlib import Path
import copy,json,time
from modules.experiments.placements import load_suite
from contracts.artifacts import batch_directory

STAGES=[{'id':'experience','role':'变化测试前经验检索','deps':[]},{'id':'placement_variant','role':'生成独立初始位姿案例','deps':['experience']},{'id':'functional_geometry','role':'功能几何复审','deps':['placement_variant']},{'id':'scene_build','role':'变化场景构建','deps':['functional_geometry']},{'id':'scene_validation','role':'独立场景验收','deps':['scene_build']},{'id':'expert_plan','role':'同一示教轨迹适配','deps':['scene_validation']},{'id':'expert_execution','role':'变化场景离线专家执行','deps':['expert_plan']},{'id':'expert_validation','role':'独立动作验收/保持仅记录','deps':['expert_execution']}]

def create_suite(root,store,identifier,case_ids=None):
    root=Path(root).resolve();suite,digest,base=load_suite(root,identifier)
    if case_ids is not None:
        known={c['id'] for c in suite['cases']}
        if not isinstance(case_ids,list) or not case_ids or not all(isinstance(c,str) for c in case_ids) or len(set(case_ids))!=len(case_ids) or not set(case_ids)<=known:raise ValueError('case_ids must be a nonempty unique subset of declared cases')
    binding={}
    if suite.get('implementation_lock_path'):
        from modules.experiments.locks import verify_lock
        locked=verify_lock(root,suite['implementation_lock_path']);binding={'implementation_lock_path':locked['path'],'implementation_lock_sha256':locked['sha256']}
    selected=[c for c in suite['cases'] if case_ids is None or c['id'] in case_ids]
    source_id=base['source']['run_id']
    source=store.get_run(source_id)
    if source is None:raise ValueError('找不到实验套件对应的示教运行')
    demo_id=source['input'].get('demo_run_id') or source_id
    batch='placement_'+str(time.time_ns())
    out=root/'runs'/demo_id/'experiments'/batch
    out.mkdir(parents=True)
    (out/'suite.json').write_text(json.dumps(suite,ensure_ascii=False,indent=2));runs=[]
    for case in selected:
        inp={'suite_id':identifier,'suite_sha256':digest,'batch_id':batch,'case_id':case['id'],'evaluation_scope':'action_only','expert_run':True,'placement_experiment':True,'source_run_id':base['source']['run_id'],'demo_path':'data/reference/connector/converted/episode_000.npz','query':'bimanual connector friction grasp keyed insertion initial XY yaw reference saturation','expert_perception':'simulator_ground_truth','hardware_commands_sent':0}
        if case.get('recovery'):
            inp.update(recovery=copy.deepcopy(case['recovery']),expert_perception='simulated_pose_estimate',recovery_collection=not bool(case['recovery'].get('policy_path')),recovery_student=bool(case['recovery'].get('policy_path')),dataset_split=case.get('split'))
        inp.update(binding)
        stages=copy.deepcopy(STAGES)
        if case.get('recovery'):
            stages[6]['role']='神经恢复学生闭环执行' if case['recovery'].get('policy_path') else '物理扰动与规则专家采集'
        if case.get('recovery',{}).get('phase_version'):
            inp['phase_recovery']=True
            stages.append({'id':'phase_validation','role':'阶段动作与失败松爪归位验收','deps':['expert_execution']})
        job=store.create_run(('学生恢复测试 / ' if inp.get('recovery_student') else '位置与扰动测试 / ')+case['title'],inp,stages,demo_run_id=demo_id);runs.append({'case_id':case['id'],'title':case['title'],'split':case.get('split','development'),'run_id':job['id']})
    result={'batch_id':batch,'output_directory':out.relative_to(root).as_posix(),'suite_id':identifier,'suite_sha256':digest,'runs':runs,'selected_case_ids':[c['id'] for c in selected],'evaluation_scope':'action_only','retention_record_only':True,'hardware_commands_sent':0}
    result.update(binding)
    (out/'submitted.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result


def collection_source(root, store, batches):
    sources = set()
    for batch in batches:
        folder = batch_directory(root, batch)
        submission = json.loads((folder / 'submitted.json').read_text())
        for entry in submission['runs']:
            records = store.query('SELECT input FROM runs WHERE id=?', (entry['run_id'],))
            if not records:
                raise ValueError('采集批次引用的运行不存在')
            inputs = records[0]['input']
            source = inputs.get('demo_run_id') or inputs.get('source_run_id')
            sources.add(source)
    return sources.pop() if len(sources)==1 else None
