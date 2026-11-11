from pathlib import Path
import json,numpy as np
from modules.planning.return_home import plan_return
from modules.planning.regrasp import hand_geometry
ROOT=Path(__file__).resolve().parents[2]
def test_failure_returns_initial_joints_without_teleport(monkeypatch):
 monkeypatch.syspath_prepend(str(ROOT/'tasks/connector/legacy'))
 from sim_bridge.connector_planner import NeroIK,BASES,TABLE
 f=json.loads((ROOT/'tests/expert/fixtures/return_home_failure_v2.json').read_text());c=json.loads((ROOT/'config/phase_recovery_v3.json').read_text());current=np.array(f['current']);home=np.array(f['home']);path,report=plan_return(NeroIK(),BASES,current,home,hand_geometry(ROOT),TABLE,c)
 assert report['passed'] and np.allclose(path[-1],home)
 assert np.max(np.abs(np.diff(np.concatenate([current[None],path]),axis=0)))<=c['home_speed_rad_s']/30+1e-9
 assert report['minimum_hand_table_clearance_m']>=c['home_min_hand_table_clearance_m']
