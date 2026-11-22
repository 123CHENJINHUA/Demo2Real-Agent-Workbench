import hashlib
import json
import numpy as np
import pytest
from contracts.recovery import FEATURES,LABELS,OBSERVATION,ACTION,SOURCE
from modules.policies.neural.recovery_supervisor import RecoverySupervisor
from modules.learning.recovery_bc import metrics,load_dataset

def checkpoint(tmp_path,source=SOURCE):
    meta={'feature_names':list(FEATURES),'labels':list(LABELS),'observation_contract':OBSERVATION,'action_contract':ACTION,'observation_source':source,'task_signature':'test fixture'}
    p=tmp_path/'unit_policy.npz';np.savez(p,mean=np.zeros(24),scale=np.ones(24),classes=np.arange(5),layers=np.array(1),w0=np.zeros((24,5)),b0=np.arange(5),metadata=np.array(json.dumps(meta)));return p

def test_recovery_policy_is_separate_from_camera_contract(tmp_path):
    model=RecoverySupervisor(checkpoint(tmp_path));assert model.describe().observation_contract==OBSERVATION
    action,confidence=model.act(np.zeros(24));assert action==4 and 0<confidence<1
    with pytest.raises(ValueError):model.act(np.zeros(8))
    with pytest.raises(ValueError):RecoverySupervisor(checkpoint(tmp_path,'camera_estimate'))

def test_recovery_export_is_colocated_and_listed_with_legacy_packages(tmp_path):
    from modules.deployment.recovery import run
    from modules.policies.service import list_packages

    source=checkpoint(tmp_path)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    evaluation={'checkpoint_sha256':digest,'scope':'export fixture','report_path':'evaluation.json'}
    (tmp_path/'evaluation.json').write_text(json.dumps(evaluation))
    directory=tmp_path/'runs'/'demo'/'derived'/'training'/'recovery_deployment'
    result=run({'root':tmp_path,'input':{'demo_run_id':'demo'},'run_id':'training','output_dir':directory,
                'dependencies':{'recovery_learning':{'checkpoint':source.name},'recovery_evaluation':evaluation}})
    assert (tmp_path/result['checkpoint']).read_bytes()==source.read_bytes()
    assert (tmp_path/result['package']).parent==directory
    legacy=tmp_path/'deployments'/'old';legacy.mkdir(parents=True)
    (legacy/'manifest.json').write_text('{}')
    assert {item['path'] for item in list_packages(tmp_path)['items']}=={'deployments/old',result['package']}

def test_macro_f1_does_not_hide_missing_regrasp_predictions():
    y=np.array([0]*100+[1,2,3,4]);m=metrics(y,np.zeros(len(y),int));assert m['accuracy']>.9 and m['macro_f1']<.2

def test_training_refuses_synthetic_camera_or_force_data(tmp_path):
    p=tmp_path/'bad.npz';meta={'source':'synthetic_fixture','observation_source':SOURCE}
    np.savez(p,features=np.zeros((15,24)),labels=np.arange(15)%5,episode_ids=np.arange(15),split=np.array(['train']*5+['validation']*5+['test']*5),metadata=np.array(json.dumps(meta)))
    with pytest.raises(ValueError,match='Genuine'):load_dataset(p)


def test_validation_guard_matches_execution_visibility_and_confidence():
 from modules.learning.recovery_bc import guarded_modes
 x=np.zeros((4,24));x[:,[0,10]]=1.;x[1:3,0]=0.
 p=np.array([[.9,.025,.025,.025,.025],[.025,.025,.9,.025,.025],[.025,.025,.025,.025,.9],[.2,.2,.2,.2,.2]])
 assert guarded_modes(x,p).tolist()==[0,1,4,1]
