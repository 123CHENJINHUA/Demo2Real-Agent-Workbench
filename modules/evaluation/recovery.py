"""Frozen episode test metrics; not a closed-loop success claim."""
from pathlib import Path
import json,hashlib
from modules.learning.recovery_bc import load_dataset,metrics,guarded_modes
from modules.policies.neural.recovery_supervisor import RecoverySupervisor

def run(ctx):
    root=Path(ctx['root']).resolve();dataset=root/ctx['dependencies']['recovery_dataset']['dataset_path'];checkpoint=root/ctx['dependencies']['recovery_learning']['checkpoint'];model=RecoverySupervisor(checkpoint);x,y,ep,split,meta=load_dataset(dataset);mask=split=='test'
    if model.metadata['dataset_sha256']!=hashlib.sha256(dataset.read_bytes()).hexdigest():raise ValueError('Checkpoint/data mismatch')
    report={'status':'succeeded','scope':'held-out episode supervisor decision classification only','test_metrics':metrics(y[mask],model.probabilities(x[mask]).argmax(1)),'guarded_test_metrics':metrics(y[mask],guarded_modes(x[mask],model.probabilities(x[mask]))),'test_episodes':sorted(set(ep[mask])),'checkpoint':str(checkpoint.relative_to(root)),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'task_signature':model.metadata['task_signature'],'observation_source':model.metadata['observation_source'],'closed_loop_evaluated':False,'hardware_verified':False}
    out=Path(ctx['output_dir']);out.mkdir(parents=True,exist_ok=True);report['report_path']=str((out/'evaluation.json').relative_to(root));(out/'evaluation.json').write_text(json.dumps(report,indent=2));return report
