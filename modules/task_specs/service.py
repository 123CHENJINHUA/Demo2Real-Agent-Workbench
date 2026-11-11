"""Append-only task contracts; explicit profile selection, evidence hashes and field provenance."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
import copy, fcntl, hashlib, json, os, re
from contracts.tasks import SCHEMA_VERSION,SpecConflict,SpecPrecondition,canonical_hash,validate_task_spec,task_unknowns
VERSION='task_specs.v1'

def _time():return datetime.now(timezone.utc).isoformat()
def _file(root,value):
    root=Path(root).resolve();raw=Path(value)
    path=(root/raw).absolute() if not raw.is_absolute() else raw.absolute()
    if not path.resolve().is_relative_to(root):raise ValueError('Task references must stay within project')
    current=path
    while current!=root:
        if current.is_symlink():raise ValueError('Task references cannot traverse symlinks')
        current=current.parent
    if not path.is_file():raise ValueError('Task evidence/asset does not exist: '+str(value))
    return path.resolve()

def _hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()

def _folder(root,task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(task_id)):raise ValueError('Invalid task identity')
    root=Path(root).resolve();path=root/'runs'/task_id/'task_spec';current=path
    while current!=root:
        if current.is_symlink():raise ValueError('Task revision directories cannot traverse symlinks')
        current=current.parent
    if not path.resolve().is_relative_to(root):raise ValueError('Task directory escapes project')
    return path

@contextmanager
def _locked(root,task_id):
    directory=_folder(root,task_id);directory.mkdir(parents=True,exist_ok=True)
    with (directory/'.revision.lock').open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        try:yield directory
        finally:fcntl.flock(stream,fcntl.LOCK_UN)

def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())

def profiles(root):
    root=Path(root).resolve();items=[];ids=set()
    for path in sorted((root/'tasks').glob('*/profile.json')):
        path=_file(root,path);data=json.loads(path.read_text());identifier=data.get('id')
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identifier) or identifier in ids:raise ValueError('Invalid or duplicate registered profile ID')
        if not isinstance(data.get('defaults',{}),dict):raise ValueError('Profile defaults must be an object')
        if not isinstance(data.get('synthetic_test_fixture',False),bool):raise ValueError('Profile synthetic_test_fixture must be an explicit boolean')
        ids.add(identifier);items.append(data|{'profile_path':str(path.relative_to(root)),'profile_sha256':_hash(path),'automatic_selection':False})
    return items

def _profile(root,identifier):
    if identifier is None:return None
    for item in profiles(root):
        if item['id']==identifier:return item
    raise ValueError('Profile is not registered; select an explicit registered profile')

def _latest(root,task_id):
    paths=sorted((_folder(root,task_id)/'revisions').glob('r*.json'))
    if not paths:return None
    path=paths[-1];spec=json.loads(path.read_text());validate_task_spec(spec)
    if spec.get('content_hash')!=canonical_hash(spec):raise ValueError('Stored task revision content hash mismatch')
    return spec,path

def _wrapper(root,task_id,latest=None):
    latest=latest or _latest(root,task_id)
    if latest is None:raise FileNotFoundError('Task spec has not been generated')
    spec,path=latest;directory=_folder(root,task_id)
    history=[]
    for file in sorted((directory/'revisions').glob('r*.json')):
        value=json.loads(file.read_text());history.append({k:value.get(k) for k in ('revision','state','content_hash','created_at','profile_id')}|{'spec_path':str(file.relative_to(root))})
    snapshots=[str(p.relative_to(root)) for p in sorted((directory/'frozen').glob('*.json'))]
    return {'spec':spec,'spec_path':str(path.relative_to(root)),'etag':'"r'+str(spec['revision'])+'-'+spec['content_hash']+'"','history':history,'freeze_snapshots':snapshots,'contact_execution_authorized':False,'artifacts':[{'path':str(path.relative_to(root)),'sha256':_hash(path),'name':'Task spec r'+str(spec['revision'])}]}

def get(root,task_id):return _wrapper(Path(root).resolve(),task_id)

def _expect(current,expected_revision=None,etag=None):
    if current is None:
        if expected_revision not in (None,0):raise SpecConflict('Task spec does not yet exist')
        return
    spec=current[0]
    if expected_revision is None and etag is None:raise SpecPrecondition('expected_revision or If-Match is required')
    if expected_revision is not None and (isinstance(expected_revision,bool) or expected_revision!=spec['revision']):raise SpecConflict('Task spec changed; reload the latest revision')
    actual='"r'+str(spec['revision'])+'-'+spec['content_hash']+'"'
    if etag is not None and etag!=actual:raise SpecConflict('Task spec ETag changed; reload before editing')

def _leaves(value,prefix=''):
    if isinstance(value,dict):
        for key,item in value.items():yield from _leaves(item,prefix+'/'+key.replace('~','~0').replace('/','~1'))
    elif isinstance(value,list):
        for index,item in enumerate(value):yield from _leaves(item,prefix+'/'+str(index))
    else:yield prefix,value

def _assets(root,spec):
    refs=[]
    for obj in spec['objects']:
        for key in ('asset_path','visual_asset_path'):
            if obj.get(key):refs.append(obj[key])
        collision=obj.get('collision') or {}
        if collision.get('profile_path'):refs.append(collision['profile_path'])
    robot=spec.get('robot',{})
    if robot.get('asset_path'):refs.append(robot['asset_path'])
    for arm in robot.get('arms',[]):
        for key in ('urdf_path','asset_path'):
            if arm.get(key):refs.append(arm[key])
        camera=arm.get('camera') or {}
        if camera.get('calibration_path'):refs.append(camera['calibration_path'])
    result={str(_file(root,value).relative_to(root)):_hash(_file(root,value)) for value in sorted(set(refs))}
    profile=_profile(root,spec.get('profile_id'))
    if profile:
        for value,digest in profile.get('asset_signatures',{}).items():
            path=_file(root,value);actual=_hash(path)
            if actual!=digest:raise ValueError('Registered profile asset signature changed: '+value)
            result[str(path.relative_to(root))]=actual
    return result

def _verify_dependencies(root,spec):
    dependencies=spec.get('dependency_hashes',{})
    groups=[dependencies.get('assets',{}),dependencies.get('evidence',{})]
    profile=dependencies.get('profile')
    if profile:groups.append({profile['path']:profile['sha256']})
    for group in groups:
        for value,digest in group.items():
            if _hash(_file(root,value))!=digest:raise ValueError('Referenced asset/evidence changed; regenerate and review: '+value)

def _reference_hashes(root,spec):
    records=dict(spec.get('dependency_hashes',{}).get('evidence',{}))
    for path,value in _leaves({k:spec.get(k) for k in ('phases','frames','field_provenance')}):
        if '/evidence_refs/' in path and isinstance(value,str):
            file=_file(root,value);relative=str(file.relative_to(root));digest=_hash(file)
            if relative in records and records[relative]!=digest:raise ValueError('Task evidence changed during revision: '+relative)
            records[relative]=digest
    return records

def _commit(root,directory,spec):
    spec['contact_execution_authorized']=False;spec['hardware_verified']=False
    spec['validation']={'physical':'not_verified','hardware':'not_verified','prior_scene_results':'must_match_exact_spec_hash_and_revision'}
    spec['dependency_hashes']['evidence']=_reference_hashes(root,spec)
    spec['content_hash']=canonical_hash(spec);validate_task_spec(spec)
    path=directory/'revisions'/('r'+str(spec['revision']).zfill(6)+'.json');_write(path,spec)
    return _wrapper(root,spec['task_id'],(spec,path))

def generate(root,task_id,*,inputs=None,dependencies=None,profile_id=None,expected_revision=None,etag=None):
    root=Path(root).resolve();inputs=inputs or {};dependencies=dependencies or {}
    profile_id=profile_id if profile_id is not None else inputs.get('profile_id')
    geometry=dependencies.get('geometry') or {}
    if geometry.get('report_path'):geometry=json.loads(_file(root,geometry['report_path']).read_text())
    audited={item.get('path'):item.get('sha256') for item in geometry.get('assets',[]) if isinstance(item,dict) and item.get('status')=='audited'}
    matches=[]
    for candidate in profiles(root):
        meshes=[item.get('asset_path') for item in candidate.get('defaults',{}).get('objects',[]) if item.get('geometry_type')=='mesh' and item.get('asset_path')]
        signatures=candidate.get('asset_signatures',{})
        if meshes and all(audited.get(path) and audited[path]==signatures.get(path) for path in meshes):matches.append(candidate['id'])
    assignment='explicit_profile_selection' if profile_id is not None else 'unbound'
    selection_scope=geometry.get('asset_selection_scope','explicit_task_assets' if inputs.get('asset_paths') else 'project_inventory_unbound')
    if profile_id is None and len(matches)==1 and selection_scope=='explicit_task_assets':profile_id=matches[0];assignment='hash_matched_template_draft'
    profile=_profile(root,profile_id)
    with _locked(root,task_id) as directory:
        current=_latest(root,task_id);_expect(current,expected_revision,etag)
        defaults=copy.deepcopy(profile.get('defaults',{})) if profile else {}
        source={'run_id':task_id,'synthetic_test_fixture':profile.get('synthetic_test_fixture',False) if profile else False};evidence={};unresolved=[]
        bindings={'manifest_path':('demo_import','manifest_path','demo_import/manifest.json'),'proposal_path':('task_analysis','report_path','task_analysis/task_proposal.json'),'geometry_report_path':('geometry','report_path','geometry/geometry_audit.json')}
        for name,(stage,key,fallback) in bindings.items():
            value=(dependencies.get(stage) or {}).get(key)
            if not value and (root/'runs'/task_id/fallback).is_file():value=str((root/'runs'/task_id/fallback).relative_to(root))
            source[name]=value
            if value:
                path=_file(root,value);source[name]=str(path.relative_to(root));evidence[source[name]]=_hash(path)
            else:unresolved.append(name+' unavailable; no evidence was invented')
        analysis=dependencies.get('task_analysis') or {}
        if source.get('proposal_path'):analysis=json.loads(_file(root,source['proposal_path']).read_text())
        proposal=analysis.get('proposal') or {}
        if not isinstance(proposal,dict):proposal={}
        for phase in proposal.get('phases',[]):
            if isinstance(phase,dict):
                for value in phase.get('evidence_refs',[]):
                    path=_file(root,value);evidence[str(path.relative_to(root))]=_hash(path)
        geometry=dependencies.get('geometry') or {}
        for key in ('functional_report_path',):
            if geometry.get(key):
                path=_file(root,geometry[key]);source[key]=str(path.relative_to(root));evidence[source[key]]=_hash(path)
        checklist=(dependencies.get('experience') or {}).get('checklist_path')
        if checklist:
            path=_file(root,checklist);source['experience_checklist_path']=str(path.relative_to(root));evidence[source['experience_checklist_path']]=_hash(path)
        source['evidence']=[{'path':p,'sha256':h} for p,h in sorted(evidence.items())]
        if profile is None:unresolved.append('Select a registered task profile explicitly; model names do not select implementations')
        objects=defaults.get('objects')
        if objects is None:
            objects=[{'id':'object_'+str(i),'label':item.get('name') if isinstance(item,dict) else str(item),'asset_path':None} for i,item in enumerate(proposal.get('objects',[]))]
        for item in objects:
            item.setdefault('asset_path',item.pop('mesh_path',None));item.setdefault('geometry_type','mesh' if item.get('asset_path') else 'unknown')
            item.setdefault('scale',[1,1,1] if item.get('asset_path') else None)
            if isinstance(item['scale'],(float,int)):item['scale']=[item['scale']]*3
            item.setdefault('pose',{'position_m':None,'quaternion_wxyz':None});item.setdefault('mass_kg',None);item.setdefault('collision',{'type':'unknown'})
            for key in ('asset_path','mass_kg'):
                if item.get(key) is None and (key!='asset_path' or item.get('geometry_type') not in ('box','sphere','cylinder')):unresolved.append(item.get('id','object')+'.'+key+' is unknown')
            if item['pose'].get('position_m') is None or item['pose'].get('quaternion_wxyz') is None:unresolved.append(item.get('id','object')+'.pose is unknown; not reconstructed from RGB appearance')
        scene=defaults.get('scene',{'table':{'size_m':None,'position_m':None}})
        evaluation=defaults.get('evaluation',{})
        evaluation.setdefault('action',{'status':'unverified','criteria':proposal.get('success_conditions',[])})
        evaluation.setdefault('retention',{'status':'unverified','required':None,'criteria':[]})
        spec={'schema_version':SCHEMA_VERSION,'task_id':task_id,'revision':1 if current is None else current[0]['revision']+1,'state':'draft','profile_id':profile_id,'source':source,'robot':defaults.get('robot',{'asset_path':None}),'objects':objects,'scene':scene,'frames':defaults.get('frames',[]),'phases':proposal.get('phases',[]),'evaluation':evaluation,'assumptions':defaults.get('assumptions',[]),'unresolved':unresolved+(profile.get('unresolved',[]) if profile else [])+defaults.get('unresolved',[])+proposal.get('unknowns',[]),'dependency_hashes':{'evidence':evidence,'profile':{'path':profile['profile_path'],'sha256':profile['profile_sha256']} if profile else None},'field_provenance':{},'created_at':_time(),'contact_execution_authorized':False,'hardware_verified':False}
        validate_task_spec(spec)
        spec['dependency_hashes']['assets']=_assets(root,spec)
        for path,value in _leaves({k:spec[k] for k in ('robot','objects','scene','frames','phases','evaluation','assumptions')}):
            origin='unknown' if value is None else 'model_proposal' if path.startswith('/phases') or not profile else 'registered_profile'
            refs=[source['proposal_path']] if origin=='model_proposal' and source.get('proposal_path') else [profile['profile_path']] if origin=='registered_profile' else []
            spec['field_provenance'][path]={'source':origin,'evidence_refs':refs,'validation_scope':'unverified_model_proposal' if origin=='model_proposal' else 'registered_profile_assumption_not_demo_reconstruction' if origin=='registered_profile' else 'unknown'}
        spec['profile_assignment']={'method':assignment,'asset_selection_scope':selection_scope,'evidence_refs':[source['geometry_report_path']] if assignment=='hash_matched_template_draft' and source.get('geometry_report_path') else [],'contact_execution_authorized':False}
        spec['profile_candidates']=[{'profile_id':identifier,'basis':'complete_geometry_audit_mesh_path_and_hash_match','automatic_selection':assignment=='hash_matched_template_draft' and identifier==profile_id} for identifier in matches]
        if assignment=='hash_matched_template_draft':
            spec['assumptions'].append('Unique registered mesh path+hash match binds a template draft only; poses, mass, calibration and contact assumptions remain unverified for this demonstration.')
            spec['field_provenance']['/assumptions/'+str(len(spec['assumptions'])-1)]={'source':'geometry_hash_match','evidence_refs':[source['geometry_report_path']] if source.get('geometry_report_path') else [],'validation_scope':'unverified_template_binding'}
        spec['unresolved']=[value for value in spec['unresolved'] if not isinstance(value,str) or not value.startswith('schema_unknown: ')]+task_unknowns(spec)
        return _commit(root,directory,spec)

def save(root,task_id,value,*,expected_revision=None,etag=None):
    root=Path(root).resolve()
    with _locked(root,task_id) as directory:
        current=_latest(root,task_id)
        if current is None:raise FileNotFoundError('Generate a task spec before editing')
        _expect(current,expected_revision,etag);previous=current[0];_verify_dependencies(root,previous)
        spec=copy.deepcopy(value)
        if spec.get('task_id')!=task_id or spec.get('source')!=previous['source']:raise ValueError('Task identity and original source evidence cannot be edited')
        _profile(root,spec.get('profile_id'))
        if spec.get('profile_id')!=previous.get('profile_id'):raise ValueError('Use generate with explicit profile selection to change profiles')
        spec.update(revision=previous['revision']+1,state='draft',created_at=_time(),hardware_verified=False,contact_execution_authorized=False)
        spec.pop('frozen_at',None);spec['dependency_hashes']=copy.deepcopy(previous['dependency_hashes']);validate_task_spec(spec);spec['dependency_hashes']['assets']=_assets(root,spec)
        provenance=copy.deepcopy(previous['field_provenance']);old=dict(_leaves(previous))
        for path,item in _leaves({k:spec.get(k) for k in ('robot','objects','scene','frames','phases','evaluation','assumptions','unresolved')}):
            if path not in old or old[path]!=item:provenance[path]={'source':'user_edit','evidence_refs':[],'validation_scope':'user_supplied_unverified'}
        spec['field_provenance']=provenance
        spec['unresolved']=[value for value in spec['unresolved'] if not isinstance(value,str) or not value.startswith('schema_unknown: ')]+task_unknowns(spec)
        return _commit(root,directory,spec)

def freeze(root,task_id,*,expected_revision=None,etag=None,scope=None):
    if scope!='offline_contract':raise ValueError('Freeze scope must explicitly be offline_contract; no execution permission is granted')
    root=Path(root).resolve()
    with _locked(root,task_id) as directory:
        current=_latest(root,task_id)
        if current is None:raise FileNotFoundError('Task spec does not exist')
        _expect(current,expected_revision,etag);_verify_dependencies(root,current[0])
        spec=copy.deepcopy(current[0])
        if spec['state']!='frozen':
            spec.update(revision=spec['revision']+1,state='frozen',created_at=_time(),frozen_at=_time());result=_commit(root,directory,spec)
        else:result=_wrapper(root,task_id,current)
        snapshot=directory/'frozen'/(spec['content_hash']+'.json')
        if not snapshot.exists():_write(snapshot,spec)
        result['freeze_snapshots']=[str(p.relative_to(root)) for p in sorted((directory/'frozen').glob('*.json'))]
        result['frozen_snapshot_path']=str(snapshot.relative_to(root));result['freeze_scope']='offline_contract_only'
        result['artifacts'].append({'path':str(snapshot.relative_to(root)),'sha256':_hash(snapshot),'name':'Frozen offline contract'})
        return result

def pin(root,task_id,*,expected_revision=None,etag=None):
    root=Path(root).resolve()
    with _locked(root,task_id):
        current=_latest(root,task_id)
        if current is None:raise FileNotFoundError('Task spec does not exist')
        _expect(current,expected_revision,etag);_verify_dependencies(root,current[0]);return _wrapper(root,task_id,current)

def run(ctx):
    root=Path(ctx['root']).resolve();task_id=ctx['run_id']
    if _latest(root,task_id) is not None:result=get(root,task_id)
    else:result=generate(root,task_id,inputs=ctx['input'],dependencies=ctx.get('dependencies',{}),profile_id=ctx['input'].get('profile_id'))
    ctx['emit']('task_spec.generated',{'revision':result['spec']['revision'],'content_hash':result['spec']['content_hash'],'profile_id':result['spec']['profile_id']})
    return {'status':'completed','spec_path':result['spec_path'],'spec_hash':result['spec']['content_hash'],'revision':result['spec']['revision'],'profile_id':result['spec']['profile_id'],'state':result['spec']['state'],'unresolved':result['spec']['unresolved'],'quality_state':'draft_unverified','contact_execution_authorized':False}


def load_snapshot(root,spec_path,expected_hash=None):
    root=Path(root).resolve();path=_file(root,spec_path);spec=json.loads(path.read_text());validate_task_spec(spec)
    if spec.get('content_hash')!=canonical_hash(spec) or expected_hash is not None and spec['content_hash']!=expected_hash:raise ValueError('Pinned task spec content hash mismatch')
    _verify_dependencies(root,spec);return spec
