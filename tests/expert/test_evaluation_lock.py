from pathlib import Path
import json,hashlib
import pytest
from modules.experiments.locks import verify_lock

def test_implementation_lock_detects_changes(tmp_path):
    file=tmp_path/'controller.py';file.write_text('fixed controller v1')
    lock=tmp_path/'lock.json';lock.write_text(json.dumps({'schema_version':'expert-evaluation-lock/v1','files':{'controller.py':hashlib.sha256(file.read_bytes()).hexdigest()}}))
    expected=hashlib.sha256(lock.read_bytes()).hexdigest();assert verify_lock(tmp_path,'lock.json',expected)['sha256']==expected
    file.write_text('retuned during evaluation')
    with pytest.raises(ValueError,match='Frozen implementation'):verify_lock(tmp_path,'lock.json',expected)

def test_lock_file_and_path_must_match(tmp_path):
    lock=tmp_path/'lock.json';lock.write_text('{"schema_version":"expert-evaluation-lock/v1","files":{"a":"b"}}')
    with pytest.raises(ValueError,match='lock changed'):verify_lock(tmp_path,'lock.json','0'*64)
    with pytest.raises(ValueError,match='escapes'):verify_lock(tmp_path,'../outside.json')

def test_locked_holdout_is_disjoint_from_development_cases():
    from modules.experiments.placements import load_suite
    root=Path(__file__).resolve().parents[2]
    suite=json.loads((root/'config/experiments/connector_validation_v1.json').read_text());development=json.loads((root/'config/experiments/connector_small_v1.json').read_text())
    def key(case):return tuple(v for row in case['xy_offsets_m'] for v in row)+tuple(case['yaw_offsets_deg'])
    regression=[c for c in suite['cases'] if c['split']=='regression'];holdout=[c for c in suite['cases'] if c['split']=='holdout']
    assert len(regression)==5 and len(holdout)==10
    assert {key(c) for c in regression}=={key(c) for c in development['cases']}
    assert len({key(c) for c in holdout})==10
    assert not {key(c) for c in holdout}&{key(c) for c in development['cases']}
    assert suite['acceptance']['regression_required_passes']==5 and suite['acceptance']['holdout_required_passes']==10
