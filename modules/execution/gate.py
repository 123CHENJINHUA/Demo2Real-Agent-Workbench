"""Command authority for mock/shadow conformance; no SDK imports."""
import math
from contracts.policies import MOTION_ACTION

class ExecutionGate:
    def __init__(self, backend, clock, joint_limits, max_step=.03):
        self.backend,self.clock,self.limits,self.max_step=backend,clock,joint_limits,max_step
        self.epoch=None;self.last_sequence=-1;self.halted=True
    def begin(self,epoch):
        self.epoch=epoch;self.last_sequence=-1;self.halted=False
    def stop(self):
        self.halted=True;self.epoch=None
        self.backend.hold()
    def apply(self,proposal,feedback):
        now=self.clock()
        if self.halted or proposal.epoch!=self.epoch:raise ValueError("stopped or obsolete epoch")
        if proposal.contract!=MOTION_ACTION or proposal.frame!="robot_joint":raise ValueError("action contract mismatch")
        if proposal.observation_sequence<=self.last_sequence:raise ValueError("replayed/out-of-order action")
        if not all(math.isfinite(t) for t in (now,proposal.created_at,proposal.valid_until)) or not proposal.created_at<=now<=proposal.valid_until or proposal.valid_until-proposal.created_at>.25:raise ValueError("expired or invalid action")
        q=proposal.joints_rad
        if len(q)!=len(self.limits) or len(feedback)!=len(q):raise ValueError("joint count mismatch")
        for x,current,(low,high) in zip(q,feedback,self.limits):
            if not math.isfinite(x) or not math.isfinite(current) or not low<=x<=high or abs(x-current)>self.max_step:raise ValueError("motion exceeds bounds")
        self.backend.send_joint_target(q)
        self.last_sequence=proposal.observation_sequence
        return {"accepted":True,"mode":self.backend.mode}
