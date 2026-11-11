from pathlib import Path
from types import SimpleNamespace
import importlib,numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('mode,expected', [('first_fails',-np.pi/2),('all_valid',np.pi/2),('all_fail',None)])
def test_independent_key_candidate_feasibility(monkeypatch,mode,expected):
    monkeypatch.syspath_prepend(str(ROOT/'tasks/connector/legacy'))
    module=importlib.import_module('sim_bridge.connector_coordinator');BASES=importlib.import_module('sim_bridge.connector_planner').BASES
    class IK:
        def __init__(self):self.calls=0;self.solutions={}
        def solve_near_demo(self,target,arm,seed,reference):
            self.calls+=1
            if mode=='all_fail' or mode=='first_fails' and self.calls==1:raise RuntimeError('unreachable mock candidate')
            q=np.zeros(7);q[0]=self.calls;pose=target.copy();pose[:3,3]-=BASES[arm];self.solutions[self.calls]=pose;return q
        def fk(self,q):return self.solutions[int(q[0])].copy()
    ik=IK();sim=SimpleNamespace(ik=ik,arms=[{'command':np.zeros(7)},{'command':np.zeros(7)}]);controller=SimpleNamespace(key_config={'roll_candidates_deg':[90,-90]},initial_grips=np.tile(np.eye(4),(2,1,1)))
    poses=np.tile(np.eye(4),(2,1,1));candidates,chosen=module.select_key_candidate(sim,controller,np.array([poses]),np.zeros((1,2,7)),poses)
    assert len(candidates)==2
    if expected is None:assert chosen is None and not any(c['feasible'] for c in candidates)
    else:assert chosen['feasible'] and abs(chosen['roll_rad']-expected)<1e-9
    if mode=='first_fails':assert not candidates[0]['feasible'] and candidates[0]['score'] is None and candidates[1]['feasible']
