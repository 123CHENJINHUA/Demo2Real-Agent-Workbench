"""Export a simulated-state supervisor package without hardware permission."""
from pathlib import Path
import json,shutil,hashlib
from modules.policies.neural.recovery_supervisor import RecoverySupervisor

def run(ctx):
    root=Path(ctx['root']).resolve();training=ctx['dependencies']['recovery_learning'];evaluation=ctx['dependencies']['recovery_evaluation'];source=root/training['checkpoint'];model=RecoverySupervisor(source)
    if evaluation['checkpoint_sha256']!=hashlib.sha256(source.read_bytes()).hexdigest():raise ValueError('Evaluation weight mismatch')
    output=root/'deployments'/('recovery-'+ctx['run_id']);output.mkdir();shutil.copy2(source,output/'policy.npz');shutil.copy2(root/evaluation['report_path'],output/'evaluation.json')
    manifest={'schema_version':1,'plugin_id':'recovery_state_mlp_v1','role':'supervisor','mode':'simulation_only','hardware_verified':False,'real_motion_authorized':False,'observation_contract':model.metadata['observation_contract'],'observation_source':model.metadata['observation_source'],'action_contract':model.metadata['action_contract'],'task_signature':model.metadata['task_signature'],'policy_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'scope':'learned recovery skill selection; deterministic demonstration motion remains','evaluation_scope':evaluation['scope'],'closed_loop_evaluated':False,'reason':'RGBD intentionally skipped; actual camera/hardware adaptation not validated'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));return {'status':'succeeded','package':str(output.relative_to(root)),'manifest':manifest,'checkpoint':str((output/'policy.npz').relative_to(root))}
