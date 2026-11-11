from pathlib import Path
import importlib
import pytest
from contracts.recovery import checked_recovery,FEATURES,LABELS
ROOT=Path(__file__).resolve().parents[2]

def config():return dict(kind='drop',arm=0,after_s=1.5,shift_m=[.025,.015],seed=7,pose_noise_m=0,pose_delay_s=0,rotation_noise_rad=0)

def test_recovery_contract_matches_actual_features(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'tasks/connector/legacy'))
    module=importlib.import_module('sim_bridge.connector_robust')
    assert list(FEATURES)==module.FEATURE_NAMES and len(FEATURES)==24 and len(LABELS)==5
    assert not any('force' in x or 'event' in x for x in FEATURES)

@pytest.mark.parametrize('key,value',[('kind','teleport'),('arm',3),('shift_m',[1,0]),('pose_delay_s',1),('seed',True)])
def test_recovery_bounds(key,value):
    c=config();c[key]=value
    with pytest.raises(ValueError):checked_recovery(c)
