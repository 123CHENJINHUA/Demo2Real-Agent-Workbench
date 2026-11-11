"""Episode-split, class-balanced NumPy BC for the state recovery supervisor."""
from pathlib import Path
import json,hashlib
import numpy as np
from contracts.recovery import FEATURES,LABELS,OBSERVATION,ACTION,SOURCE
from modules.policies.neural.recovery_supervisor import RecoverySupervisor

def load_dataset(path):
    with np.load(path,allow_pickle=False) as d:x=d['features'].astype(float);y=d['labels'].astype(int);ep=d['episode_ids'];split=d['split'];meta=json.loads(str(d['metadata'].item()))
    if meta.get('source')!='actual_physx_teacher_rollouts' or meta.get('synthetic_test_fixture') is not False or meta.get('observation_source')!=SOURCE or meta.get('feature_names')!=list(FEATURES) or meta.get('force_observations') is not False or meta.get('event_labels_in_features') is not False:raise ValueError('Genuine, force-free state-surrogate data required')
    if x.shape!=(len(y),24) or len(ep)!=len(y) or len(split)!=len(y) or not np.isfinite(x).all() or not set(y)<=set(range(5)):raise ValueError('Invalid dataset arrays')
    sets=[set(ep[split==s]) for s in ('train','validation','test')]
    if any(not s for s in sets) or any(sets[i]&sets[j] for i in range(3) for j in range(i+1,3)):raise ValueError('Episode split missing or leaked')
    if any(set(y[split==s])!=set(range(5)) for s in ('train','validation','test')):raise ValueError('Missing class in frozen split')
    return x,y,ep,split,meta

def metrics(y,p):
    matrix=np.zeros((5,5),int)
    for a,b in zip(y,p):matrix[a,b]+=1
    recall=np.diag(matrix)/np.maximum(1,matrix.sum(1));precision=np.diag(matrix)/np.maximum(1,matrix.sum(0));f1=2*recall*precision/np.maximum(1e-12,recall+precision)
    return {'accuracy':float(np.mean(y==p)),'macro_f1':float(f1.mean()),'balanced_accuracy':float(recall.mean()),'confusion_matrix':matrix.tolist(),'per_class':{name:{'precision':float(precision[i]),'recall':float(recall[i]),'f1':float(f1[i]),'samples':int(matrix[i].sum())} for i,name in enumerate(LABELS)}}

def guarded_modes(x,probabilities):
    raw=probabilities.argmax(1);decision=raw.copy();hidden=(x[:,0]<.5)|(x[:,10]<.5);decision[hidden&~np.isin(raw,[1,4])]=1;decision[probabilities.max(1)<.60]=1;return decision

def train(root,dataset,out,epochs=500,emit=lambda *x:None,check_cancel=lambda:None):
    root=Path(root).resolve();dataset=Path(dataset).resolve();out=Path(out).resolve();dataset.relative_to(root);out.relative_to(root);out.mkdir(parents=True,exist_ok=True)
    if not 1<=epochs<=1000:raise ValueError('Invalid epoch budget')
    x,y,ep,split,meta=load_dataset(dataset);m=split=='train';v=split=='validation';mean=x[m].mean(0);scale=np.maximum(x[m].std(0),1e-3);z=(x-mean)/scale;rng=np.random.default_rng(721);sizes=[24,48,32,5];params=[]
    for a,b in zip(sizes,sizes[1:]):params.extend([rng.normal(0,np.sqrt(2/a),(a,b)),np.zeros(b)])
    adam_m=[np.zeros_like(p) for p in params];adam_v=[np.zeros_like(p) for p in params];groups=[np.flatnonzero(m&(y==k)) for k in range(5)];history=[];best=None;best_score=(-1.,-np.inf);v2=meta.get('collection_suite')=='connector_recovery_v2';log_prior=np.log(np.bincount(y[m],minlength=5)/m.sum());alphas=(0.,.25,.5,.75,1.) if v2 else (0.,)
    def forward(batch):
        h=[batch]
        for i in range(3):
            q=h[-1]@params[2*i]+params[2*i+1];h.append(np.maximum(q,0) if i<2 else q)
        logits=h[-1]-h[-1].max(1,keepdims=True);p=np.exp(logits);p/=p.sum(1,keepdims=True);return h,p
    for epoch in range(1,epochs+1):
        check_cancel();indices=np.concatenate([rng.choice(g,256,replace=True) for g in groups]);rng.shuffle(indices);h,p=forward(z[indices]);err=p.copy();err[np.arange(len(indices)),y[indices]]-=1;err/=len(indices);grads=[None]*6
        for i in range(2,-1,-1):
            grads[2*i]=h[i].T@err+1e-5*params[2*i];grads[2*i+1]=err.sum(0)
            if i:err=(err@params[2*i].T)*(h[i]>0)
        for i,g in enumerate(grads):
            adam_m[i]=.9*adam_m[i]+.1*g;adam_v[i]=.999*adam_v[i]+.001*g*g;params[i]-=.002*(adam_m[i]/(1-.9**epoch))/(np.sqrt(adam_v[i]/(1-.999**epoch))+1e-8)
        if epoch%10==0 or epoch==epochs:
            _,original=forward(z[v]);epoch_best=None
            for alpha in alphas:
                logits=np.log(np.maximum(original,1e-300))+alpha*log_prior;logits-=logits.max(1,keepdims=True);vp=np.exp(logits);vp/=vp.sum(1,keepdims=True)
                raw_score=metrics(y[v],vp.argmax(1));score=metrics(y[v],guarded_modes(x[v],vp)) if v2 else raw_score;loss=float(-np.log(np.maximum(vp[np.arange(v.sum()),y[v]],1e-12)).mean());key=(score['macro_f1'],-loss)
                if epoch_best is None or key>epoch_best[0]:epoch_best=(key,score,loss,alpha,raw_score)
                if key>best_score:best_score=key;best=([p.copy() for p in params],epoch,score,alpha,raw_score)
            _,score,loss,alpha,raw_score=epoch_best;history.append({'epoch':epoch,'validation_loss':loss,'prior_alpha':alpha,**score});emit('progress',{'unit':'epochs','completed':epoch,'total':epochs,'validation_macro_f1':score['macro_f1']})
    params,selected_epoch,score,alpha,raw_score=best
    params[-1]=params[-1]+alpha*log_prior
    metadata={k:meta[k] for k in ('feature_names','labels','observation_contract','action_contract','observation_source','task_signature')};metadata.update(model_id='recovery_state_mlp_v1',role='supervisor',architecture=sizes,dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),collector_signature=meta['collector_signature'],selected_epoch=selected_epoch,selected_by=('validation guarded macro_f1 then validation loss; prior alpha grid 0/.25/.5/.75/1; test unused' if v2 else 'validation macro_f1 then validation loss; test unused in fitting'),calibration_prior_alpha=alpha,calibration_logit_bias=(alpha*log_prior).tolist(),validation_raw_metrics=raw_score,training_version='recovery-bc/v2' if v2 else 'recovery-bc/v1',collection_suite=meta.get('collection_suite','connector_recovery_v1'),force_observations=False,synthetic_test_fixture=False,hardware_verified=False)
    weights={'mean':mean,'scale':scale,'classes':np.arange(5),'layers':np.array(3),'metadata':np.array(json.dumps(metadata))}
    for i in range(3):weights['w'+str(i)]=params[2*i];weights['b'+str(i)]=params[2*i+1]
    checkpoint=out/'policy.npz';np.savez_compressed(checkpoint,**weights);RecoverySupervisor(checkpoint);(out/'training_history.json').write_text(json.dumps(history,indent=2));(out/'model_metadata.json').write_text(json.dumps(metadata,indent=2))
    return {'status':'succeeded','checkpoint':str(checkpoint.relative_to(root)),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'architecture':sizes,'selected_epoch':selected_epoch,'calibration_prior_alpha':alpha,'validation_raw_metrics':raw_score,'validation_metric_scope':'guarded decisions' if v2 else 'raw argmax','validation_metrics':score,'observation_source':SOURCE,'role':'supervisor','hardware_verified':False}

def run(ctx):
    path=ctx['dependencies']['recovery_dataset']['dataset_path'];return train(ctx['root'],Path(ctx['root'])/path,ctx['output_dir'],int(ctx['input'].get('epochs',500)),ctx['emit'],ctx['check_cancel'])
