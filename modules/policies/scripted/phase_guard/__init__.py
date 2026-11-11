"""Explicit phase guard around a base policy; it is not a learned v3 model."""
from contracts.phase_recovery import PhaseDecision

class PhaseGuardPolicy:
 def __init__(self,config):
  self.config=config;self.suspect_since=None;self.unseen_since=None;self.recoveries=0
 def decide(self,s,base_action):
  if base_action==4:return PhaseDecision('abandon','Base policy reported unrecoverable state')
  if not s.active_insertion:
   if base_action in (2,3) and not s.tabletop[base_action-2]:return PhaseDecision('abandon','Tabletop recovery preconditions not met',base_action-2)
   return PhaseDecision('base','No insertion intervention')
  if not s.observed:
   if self.unseen_since is None:self.unseen_since=s.time_s
   if s.time_s-self.unseen_since>=self.config['unseen_timeout_s']:return PhaseDecision('abandon','Insertion observation timeout')
   return PhaseDecision('wait','Insertion observation unavailable')
  self.unseen_since=None
  if s.complete and any(s.held):return PhaseDecision('hold_complete','Original insertion completion geometry; preserve original stability test')
  bad=[a for a in range(2) if s.suspicious[a] or not s.held[a]]
  if not bad and base_action not in (2,3):self.suspect_since=None;return PhaseDecision('base','Grips valid')
  if self.suspect_since is None:self.suspect_since=s.time_s
  if s.time_s-self.suspect_since<self.config['assessment_s']:return PhaseDecision('wait','Pause feed to assess grip')
  if not bad:return PhaseDecision('reanchor','Both grips observationally stable after assessment')
  arm=bad[0]
  if len(bad)>1 or not s.held[1-arm]:return PhaseDecision('abandon','No reliably held supporting arm',arm)
  if self.recoveries>=self.config['max_recoveries']:return PhaseDecision('abandon','Phase recovery budget exhausted',arm)
  if not s.supported_pair:return PhaseDecision('abandon','Airborne connector lacks bounded partial-mating support',arm)
  if s.held[arm] and self.config['unload_min_depth_m']<s.depth_m<self.config['unload_max_depth_m']:
   return PhaseDecision('unload','Both grips valid; bounded early-insertion unloading',arm)
  return PhaseDecision('recapture','One supporting grip; bounded in-place recapture',arm)
 def recovered(self):self.recoveries+=1;self.suspect_since=None
 def reset_transient(self):self.suspect_since=None;self.unseen_since=None
