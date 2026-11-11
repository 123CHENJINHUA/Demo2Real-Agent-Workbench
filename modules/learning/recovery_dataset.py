"""Assemble real PhysX recovery decisions, without frame or protected-holdout leakage."""
from pathlib import Path
import json,hashlib
import numpy as np
from contracts.recovery import FEATURES,LABELS,OBSERVATION,ACTION,SOURCE,task_signature

def build(root,batches,output):
    root=Path(root).resolve();output=Path(output).resolve();output.relative_to(root);output.mkdir(parents=True,exist_ok=True)
    from backend.store import Store
    store=Store(root/'.runtime/workbench.sqlite3');xs=[];ys=[];ids=[];splits=[];provenance=[];seen=set()
    protected=set()
    path=root/'runs/placement_1794399251647479270/submitted.json'
    if path.is_file():protected={r['run_id'] for r in json.loads(path.read_text())['runs'] if r.get('split')=='holdout'}
    producer=None
    for batch in batches:
        folder=(root/'runs'/batch).resolve();folder.relative_to(root/'runs');submission=json.loads((folder/'submitted.json').read_text())
        suite_snapshot=json.loads((folder/'suite.json').read_text());protected.update(suite_snapshot.get('protected_episode_ids',[]))
        if submission['suite_id'] not in ('connector_recovery_v1','connector_recovery_v2'):raise ValueError('Only declared real recovery collection is accepted')
        for entry in submission['runs']:
            rid=entry['run_id']
            if rid in seen or rid in protected:raise ValueError('Duplicate/protected evaluation episode')
            seen.add(rid);job=store.get_run(rid)
            if job['status'] in ('queued','running'):raise ValueError('Collection still running')
            result=next((t['result'] or {} for t in job['tasks'] if t['stage']=='expert_execution'),{})
            if not result.get('report_path'):raise ValueError('Episode has no actual execution evidence: '+rid)
            raw=json.loads((root/result['report_path']).read_text())
            if raw.get('learned_policy_executed') or raw.get('expert_perception')!=SOURCE:raise ValueError('Teacher-only simulated state episodes required')
            ref=raw.get('supervision_path')
            if not ref:raise ValueError('No actual supervision in '+rid)
            p=(root/ref).resolve();p.relative_to(root)
            with np.load(p,allow_pickle=False) as d:
                x=np.asarray(d['observations'],np.float32);y=np.asarray(d['expert_modes'],int);names=d['feature_names'].tolist();executed=np.asarray(d['executed_modes'],int)
            if names!=list(FEATURES) or x.ndim!=2 or x.shape[1]!=24 or y.shape!=(len(x),) or not len(x) or not np.isfinite(x).all() or not np.array_equal(y,executed):raise ValueError('Invalid real teacher supervision: '+rid)
            hashes=raw.get('source_sha256',{});signature=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
            if producer is None:producer=signature
            elif signature!=producer:raise ValueError('Collector implementation changed across dataset episodes')
            local_success={int(r['arm']) for r in raw.get('report',{}).get('recovery_policy',{}).get('recoveries',[]) if r.get('passed') is True}
            keep=np.ones(len(x),bool)
            for arm in (0,1):
                if arm not in local_success:keep&=y!=2+arm
            x=x[keep];y=y[keep];split=entry['split']
            if split not in ('train','validation','test'):raise ValueError('Explicit episode split required')
            xs.append(x);ys.append(y);ids.extend([rid]*len(x));splits.extend([split]*len(x));provenance.append({'run_id':rid,'batch':batch,'case_id':entry['case_id'],'split':split,'supervision_path':ref,'supervision_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(x),'excluded_unverified_regrasp_rows':int((~keep).sum()),'action_passed':raw.get('action_passed'),'local_recoveries':sorted(local_success),'motion_error':raw.get('report',{}).get('error')})
    x=np.concatenate(xs);y=np.concatenate(ys);ids=np.asarray(ids);splits=np.asarray(splits);counts={s:np.bincount(y[splits==s],minlength=5).tolist() for s in ('train','validation','test')}
    if any(min(v)<1 for v in counts.values()):
        (output/'incomplete_dataset.json').write_text(json.dumps({'class_counts':counts,'episodes':provenance,'error':'No synthetic padding: every class needs real verified examples in each split'},indent=2));raise ValueError('Missing genuine class examples: '+json.dumps(counts))
    meta={'schema_version':'recovery-dataset/v1','feature_names':list(FEATURES),'labels':list(LABELS),'observation_contract':OBSERVATION,'action_contract':ACTION,'observation_source':SOURCE,'task_signature':task_signature(root),'force_observations':False,'event_labels_in_features':False,'synthetic_test_fixture':False,'source':'actual_physx_teacher_rollouts','collector_signature':producer,'episodes':provenance,'protected_position_holdout_excluded':sorted(protected),'protected_exclusions_scope':'old frozen position tests, v1 state tests, and v2 development regression where declared','label_scope':'supervisor decisions; regrasp labels included only when local regrasp was physically verified; task failures remain recorded','class_counts':counts,'collection_suite':submission['suite_id']}
    path=output/'dataset.npz';np.savez_compressed(path,features=x,labels=y,episode_ids=ids,split=splits,metadata=np.array(json.dumps(meta)))
    (output/'dataset_report.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));return {'status':'succeeded','dataset_path':str(path.relative_to(root)),'dataset_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'class_counts':counts,'episode_count':len(provenance),'rows':len(x),'report_path':str((output/'dataset_report.json').relative_to(root))}

def run(ctx):return build(ctx['root'],ctx['input']['collection_batches'],ctx['output_dir'])
