"""Pure joint reference proposal; object-relative planning is a separate plugin."""
from contracts.policies import PolicyDescriptor, MotionProposal, OBSERVATION, MOTION_ACTION
import math

class DemoMotion:
    def __init__(self, times, joints, clock):
        if len(times)!=len(joints) or len(times)<2 or times[0]!=0 or any(b<=a for a,b in zip(times,times[1:])):
            raise ValueError("strictly increasing SI timestamps starting at zero required")
        if any(len(q)!=len(joints[0]) for q in joints) or not all(math.isfinite(v) for q in joints for v in q):
            raise ValueError("invalid joint reference")
        self.times,self.joints,self.clock=times,joints,clock
    def describe(self):
        return PolicyDescriptor("demo_motion_v1","motion",OBSERVATION,MOTION_ACTION,"1")
    def reset(self,epoch):
        self.epoch=epoch;self.started=self.clock()
    def predict(self,obs):
        obs.validate()
        now=self.clock()
        if obs.epoch!=getattr(self,"epoch",None):raise ValueError("epoch mismatch")
        if now-obs.captured_at>.25:return None
        t=min(now-self.started,self.times[-1])
        i=next((i for i in range(len(self.times)-1) if self.times[i+1]>=t),len(self.times)-2)
        a=max(0.,min(1.,(t-self.times[i])/(self.times[i+1]-self.times[i])))
        target=tuple((1-a)*x+a*y for x,y in zip(self.joints[i],self.joints[i+1]))
        return MotionProposal(target,obs.epoch,obs.sequence,now,now+.1)
