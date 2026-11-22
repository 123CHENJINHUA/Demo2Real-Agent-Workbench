"""Preserve task questions and historical diagnostics across workbench versions."""
import time

from contracts.analysis_agents import SPECIALISTS
from contracts.task_dialog import WAITING_INPUT


ROLES = {
    'demo_import':'示教输入 Agent','data_audit':'数据审查 Agent',
    'geometry':'资产 Agent','functional_geometry':'功能几何 Agent',
    'agent_dispatch':'总控 Agent','task_analysis':'任务分析 Agent','task_spec':'任务定义 Agent',
    'scene_layout':'场景估算 Agent','scene_build':'场景构建 Agent','scene_validation':'场景校验 Agent',
    'simulation':'动作专家 Agent','learning':'策略学习 Agent','evaluation':'独立验收 Agent','deployment':'策略归档 Agent',
    **{stage:role[0] for stage,role in SPECIALISTS.items()},
}
ORIENTATION_QUESTIONS = {
    'orientation_unverified': (
        '能否指出一段更清楚显示操作物体、起始摆放或目标位置的示教画面？',
        '需要确认的是操作物体及其起始位置、目标位置或支撑关系中的一项。',
    ),
    'orientation_mismatch': (
        '能否指出哪段示教画面最能说明操作物体应当怎么摆放？',
        '对照报告记录了操作物体或其摆放的可见差异。',
    ),
}
LEGACY_ORIENTATION_QUESTION = (
    '能否指出哪台相机或哪张照片能看清疑似翻转物体的顶面、底面或开口？'
    '如果录制画面本身旋转了，也请告诉我相机和旋转方向。'
)


def _insertion_details(result):
    return next((check.get('details') or {} for check in result.get('checks',[])
                 if check.get('id')=='upright_insertion_clearance' and not check.get('passed')),None)


def insertion_capability_block(result):
    details = _insertion_details(result)
    if not details or (details.get('source_geometry') or {}).get('status')!='source_interference':
        return None
    slot = details.get('slot_label') or '目标槽'
    return result | {'status':'blocked','unsupported_capability':'alternative_insertion_pose',
                     'reason':f'当前动作专家按直立坐底验证。原始模型显示面板和「{slot}」在这个姿态有干涉，'
                              '需要能搜索并验证其他就位姿态的动作专家。'}


def _is_insertion_check(stage,result):
    return stage=='simulation' and (_insertion_details(result) is not None
        or '目标入槽还需核对' in (result.get('reason') or result.get('error') or ''))


def question_for_failure(stage, result, *, has_reply=False):
    if not isinstance(result,dict) or result.get('unsupported_capability'):
        return None
    if result.get('placement_alignment_status')=='mismatch':
        return {'agent':ROLES.get(stage,'场景校验 Agent'),
                'prompt':'哪张示教画面能同时看清相关物体和它们在工作面上的落点？可以直接指出视角或时间点。',
                'context':'场景的相对位置或间距复核仍有偏差，需确认可用的参考点。',
                'report_path':result.get('visual_comparison_path') or result.get('report_path')}
    if orientation := ORIENTATION_QUESTIONS.get(result.get('visual_comparison_status')):
        prompt,context = orientation
        return {'agent':ROLES.get(stage,'阶段 Agent'),'prompt':prompt,'context':context,
                'report_path':result.get('visual_comparison_path') or result.get('report_path')}
    if _is_insertion_check(stage,result):
        return None
    reason = result.get('reason') or result.get('error') or ''
    diagnosis = result.get('diagnosis') or {}
    if stage=='simulation' and result.get('scope')=='offline_physx_task_action_program' and diagnosis.get('decision')!='needs_input':
        return None
    prompt = None
    if diagnosis.get('decision')=='needs_input':
        prompt = diagnosis.get('next_action')
    if not prompt and stage=='scene_validation' and result.get('repair_report_path'):
        prompt = '哪件物体的位置或支撑关系应当调整？请给出对象、预期摆法，以及可参考的示教视角或时间点。'
    if not prompt and diagnosis.get('decision')=='review' and diagnosis.get('next_action'):
        prompt = '你希望按这一步的诊断建议处理，还是有其他方案可以让我复核？'
        reason = '\n'.join(filter(None,(reason,diagnosis['next_action'])))
    elif has_reply and diagnosis.get('diagnosis'):
        reason = '\n'.join(filter(None,(reason,diagnosis['diagnosis'])))
    if not isinstance(prompt,str) or not prompt.strip():
        return None
    return {'agent':result.get('diagnosis_agent') or ROLES.get(stage,'阶段 Agent'),
            'prompt':prompt.strip()[:1500],'context':reason[:1600],
            'report_path':result.get('visual_comparison_path') or result.get('insertion_report_path') or result.get('report_path')}


def restore_pending_questions(store):
    """Expose actionable questions from saved attempts without rerunning their workers."""
    pending_insertions = store.query("""SELECT q.id,t.result FROM task_questions q JOIN tasks t ON t.id=q.task_id
        WHERE t.stage='simulation' AND t.status='waiting_input' AND q.answer IS NULL AND q.attempt=t.attempt
        AND q.run_id NOT IN (SELECT run_id FROM archived_runs)""")
    for row in pending_insertions:
        saved = row['result'] or {}
        if _is_insertion_check('simulation',saved):
            result = insertion_capability_block(saved) or saved | {
                'status':'blocked','reason':'目标位置的配合还需由模型和仿真核对。'}
            store.close_pending_question(row['id'],result)
        elif (saved.get('scope')=='offline_physx_task_action_program'
              and (saved.get('diagnosis') or {}).get('decision')=='review'):
            store.close_pending_question(row['id'],saved | {'status':'blocked'})
    stale = store.query("""SELECT q.id,q.prompt,t.stage,t.result FROM task_questions q JOIN tasks t ON t.id=q.task_id
        WHERE q.prompt=? AND q.answer IS NULL AND t.status='waiting_input' AND q.attempt=t.attempt
        AND q.run_id NOT IN (SELECT run_id FROM archived_runs)""",(LEGACY_ORIENTATION_QUESTION,))
    for row in stale:
        result = row['result'] or {}
        if result.get('visual_comparison_status') in ORIENTATION_QUESTIONS:
            store.revise_pending_question(row['id'],row['prompt'],question_for_failure(row['stage'],result))
    rows = store.query("""SELECT t.* FROM tasks t JOIN runs r ON r.id=t.run_id
        WHERE r.status IN ('failed','blocked') AND t.status IN ('failed','blocked','needs_configuration')
        AND r.id NOT IN (SELECT run_id FROM archived_runs)
        AND t.result IS NOT NULL AND coalesce(json_extract(r.input,'$.source_path'),json_extract(r.input,'$.source_import_manifest_path')) IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM task_questions q WHERE q.task_id=t.id AND q.attempt=t.attempt)""")
    for task in rows:
        if question := question_for_failure(task['stage'],task['result']):
            result=task['result'] | {'status':WAITING_INPUT,'question':question}
            store.transition(task['id'],WAITING_INPUT,result=result,question=question)
            store.execute('UPDATE runs SET status=?,updated_at=? WHERE id=?',(WAITING_INPUT,time.time(),task['run_id']))

