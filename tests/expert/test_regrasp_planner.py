from pathlib import Path
import json,sys
import numpy as np
import pytest
from modules.planning.regrasp import plan_regrasp,hand_geometry
ROOT=Path(__file__).resolve().parents[2]

def test_saved_ik_failure_gets_bounded_contact_centre_plan(monkeypatch):
 monkeypatch.syspath_prepend(str(ROOT/'tasks/connector/legacy'))
 from sim_bridge.connector_planner import NeroIK,BASES,TABLE
 f=json.loads((ROOT/'tests/expert/fixtures/regrasp_failure_v1.json').read_text());ik=NeroIK();seed=np.array(f['seed']);nominal=np.array(f['nominal'])
 with pytest.raises(RuntimeError):ik.solve(nominal,0,seed,True)
 paths,report=plan_regrasp(ik,BASES[0],0,seed,np.array(f['start']),nominal,np.array(f['return_pose']),hand_geometry(ROOT),TABLE,np.array(f['other_object']))
 assert report['passed'] and abs(report['selected_tilt_deg'])<=30
 assert report['jaw_closing_axis_preserved'] and report['contact_center_preserved']
 assert report['position_error_max_m']<.0003 and report['angle_error_max_rad']<.008
 assert report['hand_table_clearance_min_m']>=.0005
 assert set(paths)=={'approach','descend','lift','return'}
 for qs in paths.values():
  assert np.all(qs>=ik.limits[:,0]) and np.all(qs<=ik.limits[:,1])
  assert np.abs(np.diff(qs,axis=0)).max()<=.015001
