"""Experiment acceptance scope; original task/retention facts remain immutable."""
def checked_scope(value='full_task'):
    if value not in ('full_task','action_only'):
        raise ValueError('evaluation_scope must be full_task or action_only')
    return value

def apply_scope(report,scope='full_task'):
    scope=checked_scope(scope)
    integrity=bool(report.get('checks')) and all(c.get('passed') is True for c in report['checks'])
    passed=integrity and report.get('action_passed') is True and (scope=='action_only' or report.get('full_task_passed') is True)
    report.update(evaluation_scope=scope,experiment_passed=bool(passed),retention_record_only=scope=='action_only',status='succeeded' if passed else 'failed')
    if scope=='action_only' and passed and report.get('full_task_passed') is not True:
        report.setdefault('warnings',[]).append('本轮只验收抓取/对键插入/原位松手；保持仅记录，完整任务仍未通过。')
    return report
