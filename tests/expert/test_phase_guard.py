from pathlib import Path
import json
from dataclasses import replace
from contracts.phase_recovery import PhaseState
from modules.policies.scripted.phase_guard import PhaseGuardPolicy
ROOT=Path(__file__).resolve().parents[2]
def state(**kw):
 s=PhaseState(0.,True,True,False,(True,False),(False,True),(False,False),True,.0224)
 return replace(s,**kw)
def config():return json.loads((ROOT/'config/phase_recovery_v3.json').read_text())
def test_airborne_loss_routes_to_bounded_recapture_not_tabletop():
 p=PhaseGuardPolicy(config());assert p.decide(state(),3).mode=='wait'
 d=p.decide(state(time_s=.21),3);assert d.mode=='recapture' and d.arm==1

def test_unrecoverable_goes_to_abandon_without_human_request():
 p=PhaseGuardPolicy(config());assert p.decide(state(),4).mode=='abandon'
 p=PhaseGuardPolicy(config());p.decide(state(held=(False,False)),3)
 assert p.decide(state(time_s=.21,held=(False,False)),3).mode=='abandon'

def test_completed_geometry_holds_for_original_stability_check():
 p=PhaseGuardPolicy(config());assert p.decide(state(complete=True),3).mode=='hold_complete'
 assert p.decide(state(complete=False),3).mode!='hold_complete'

def test_observation_loss_never_moves_unseen_object():
 p=PhaseGuardPolicy(config());assert p.decide(state(observed=False),0).mode=='wait'
 assert p.decide(state(time_s=8.1,observed=False),0).mode=='abandon'

def test_transport_regrasp_requires_actual_tabletop_preconditions():
 p=PhaseGuardPolicy(config());assert p.decide(state(active_insertion=False),3).mode=='abandon'
 assert p.decide(state(active_insertion=False,tabletop=(False,True)),3).mode=='base'
