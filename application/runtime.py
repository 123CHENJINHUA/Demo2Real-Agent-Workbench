"""Concrete runtime environment binding."""
from modules.environment.service import local_environment

def worker_environment(root):
    return local_environment(root)


def archive_failure(root, run_id, task_id, error, log_path, result=None):
    import json, shutil, uuid, yaml
    from pathlib import Path
    from modules.experience import record_incident
    root=Path(root).resolve();result=result or {}
    taxonomy=yaml.safe_load((root/'knowledge/taxonomy.yaml').read_text()) or {}
    declared_category=result.get('failure_category');declared_stage=result.get('failure_stage')
    category=declared_category if declared_category in taxonomy.get('categories',{}) else 'workflow_integrity'
    stage=declared_stage if declared_stage in taxonomy.get('stages',[]) else 'runtime'
    directory=root/'knowledge/evidence/incidents'/uuid.uuid4().hex;directory.mkdir(parents=True)
    snapshot=directory/'failure_result.json'
    snapshot.write_text(json.dumps({'error':error,'result':result},ensure_ascii=False,indent=2,default=str))
    evidence=[{'path':str(snapshot.relative_to(root))}];seen=set()
    for value in [log_path]+list(result.get('evidence_paths',[])):
        if not isinstance(value,str):continue
        source=(root/value).resolve()
        if not source.is_relative_to(root) or not source.is_file() or str(source) in seen:continue
        seen.add(str(source));destination=directory/(str(len(seen))+'_'+source.name)
        shutil.copy2(source,destination);evidence.append({'path':str(destination.relative_to(root))})
    return record_incident(root, {
        'summary':error, 'observed_behavior':error, 'primary_category':category,
        'stage':stage, 'mode':'simulation' if stage=='simulation_test' else 'offline', 'run_id':run_id, 'task_id':task_id,
        'diagnosis':{'state':'unknown'}, 'evidence':evidence,
        'declared_failure_category':declared_category,'declared_failure_stage':declared_stage,
        'validation_scope':'module_reported_failure_only_no_hardware_inference',
    })


def worker_interpreter(root, stage):
    import sys
    from modules.environment.service import resolve
    if stage in ('demo_import','data_audit'):
        return str(resolve(root,'data_python',False) or sys.executable)
    return sys.executable


def codex_login_manager(root):
    from modules.model_accounts.codex_login import CodexLoginManager
    return CodexLoginManager(root)
