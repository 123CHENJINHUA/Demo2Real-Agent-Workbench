"""Portable recovery MLP; simulated-state contract, no motors or simulator imports."""
from pathlib import Path
import json,numpy as np
from contracts.recovery import FEATURES,LABELS,OBSERVATION,ACTION,SOURCE
from contracts.policies import PolicyDescriptor,SkillDecision

class RecoverySupervisor:
    def __init__(self,path):
        with np.load(path,allow_pickle=False) as d:
            self.mean=d['mean'].copy();self.scale=d['scale'].copy();self.classes=d['classes'].astype(int)
            self.layers=[(d[f'w{i}'].copy(),d[f'b{i}'].copy()) for i in range(int(d['layers']))];self.metadata=json.loads(str(d['metadata'].item()))
        m=self.metadata
        if m.get('observation_contract')!=OBSERVATION or m.get('action_contract')!=ACTION or m.get('observation_source')!=SOURCE or m.get('feature_names')!=list(FEATURES) or m.get('labels')!=list(LABELS):raise ValueError('Recovery checkpoint contract mismatch')
        if self.mean.shape!=(24,) or self.scale.shape!=(24,) or not np.array_equal(self.classes,np.arange(5)) or not np.all(self.scale>0):raise ValueError('Invalid recovery normalization/classes')
        previous=24
        for w,b in self.layers:
            if w.ndim!=2 or w.shape[0]!=previous or b.shape!=(w.shape[1],):raise ValueError('Invalid recovery layer shape')
            if not np.isfinite(w).all() or not np.isfinite(b).all():raise ValueError('Nonfinite recovery weights')
            previous=w.shape[1]
        if previous!=5 or not np.isfinite(self.mean).all() or not np.isfinite(self.scale).all():raise ValueError('Invalid recovery output')
    def describe(self):return PolicyDescriptor('recovery_state_mlp_v1','supervisor',OBSERVATION,ACTION,'1',False)
    def reset(self,epoch):self.epoch=epoch
    def probabilities(self,x):
        z=(np.asarray(x,float)-self.mean)/self.scale
        if z.shape[-1]!=24 or not np.isfinite(z).all():raise ValueError('Recovery actor requires 24 finite state features')
        for i,(w,b) in enumerate(self.layers):
            z=z@w+b
            if i<len(self.layers)-1:z=np.maximum(z,0)
        z-=z.max(axis=-1,keepdims=True);p=np.exp(z);return p/p.sum(axis=-1,keepdims=True)
    def act(self,x):
        p=self.probabilities(x);return int(self.classes[p.argmax()]),float(p.max())
    def predict(self,obs):
        if obs.contract!=OBSERVATION or obs.source!=SOURCE or obs.epoch!=getattr(self,'epoch',None):raise ValueError('Recovery observation contract/source/epoch mismatch')
        action,confidence=self.act(obs.features);return SkillDecision(LABELS[action],f'state-surrogate MLP confidence={confidence:.3f}',ACTION)
