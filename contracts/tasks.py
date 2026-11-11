"""Pure task-spec contract, SI units, immutable content identity; no simulator imports."""
import hashlib, json, math, re
SCHEMA_VERSION='task-spec/v1'

class SpecConflict(ValueError):pass
class SpecPrecondition(ValueError):pass

def canonical_hash(spec):
    content={k:v for k,v in spec.items() if k not in {'content_hash','revision','state','created_at','frozen_at'}}
    return hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def vector(value,size,label,nullable=True):
    if value is None and nullable:return
    if not isinstance(value,list) or len(value)!=size or any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in value):raise ValueError(label+' requires finite SI vector')

def pose(value,label):
    if not isinstance(value,dict):raise ValueError(label+' must be an object')
    vector(value.get('position_m'),3,label+'.position_m');quaternion=value.get('quaternion_wxyz')
    vector(quaternion,4,label+'.quaternion_wxyz')
    if quaternion is not None and abs(sum(x*x for x in quaternion)-1)>0.002:raise ValueError(label+' quaternion must be normalized wxyz')

def positive_scalar(value,label):
    if value is not None and (isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<=0):raise ValueError(label+' must be a positive finite SI value or unknown')

def rigid_transform(value,label):
    if isinstance(value,dict):pose(value,label);return
    if value is None:return
    if not isinstance(value,list) or len(value)!=4:raise ValueError(label+' must be a homogeneous matrix or unknown')
    for row in value:vector(row,4,label+' row',False)
    if any(abs(x-y)>1e-6 for x,y in zip(value[3],[0,0,0,1])):raise ValueError('Invalid homogeneous transform last row')
    rotation=[row[:3] for row in value[:3]]
    for i in range(3):
        for j in range(3):
            if abs(sum(rotation[k][i]*rotation[k][j] for k in range(3))-(1 if i==j else 0))>1e-6:raise ValueError(label+' rotation must be orthonormal')
    a,b,c=rotation
    determinant=a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if abs(determinant-1)>1e-6:raise ValueError(label+' rotation must have determinant +1')

def task_unknowns(spec):
    """Derived missing contract inputs; no defaults or geometry are invented."""
    missing=[]
    for item in spec.get('objects',[]):
        shape=item.get('geometry_type')
        fields={'box':('size_m',),'cylinder':('radius_m','height_m'),'sphere':('radius_m',)}.get(shape,())
        for field in fields:
            if item.get(field) is None:missing.append('schema_unknown: objects.'+item['id']+'.'+field+' is unknown')
    for frame in spec.get('frames',[]):
        name=frame['id'];transform=frame.get('transform')
        if frame.get('parent') is None:missing.append('schema_unknown: frames.'+name+'.parent is unknown')
        if transform is None:missing.append('schema_unknown: frames.'+name+'.transform is unknown')
        elif isinstance(transform,dict):
            for field in ('position_m','quaternion_wxyz'):
                if transform.get(field) is None:missing.append('schema_unknown: frames.'+name+'.'+field+' is unknown')
        if frame.get('orientation_known') is False:missing.append('schema_unknown: frames.'+name+'.orientation is only a candidate, not verified')
    return missing

def validate_task_spec(spec):
    if not isinstance(spec,dict) or spec.get('schema_version')!=SCHEMA_VERSION:raise ValueError('Unsupported task spec version')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(spec.get('task_id',''))):raise ValueError('Invalid task identity')
    if spec.get('state') not in ('draft','frozen') or isinstance(spec.get('revision'),bool) or not isinstance(spec.get('revision'),int) or spec['revision']<1:raise ValueError('Invalid task revision/state')
    if spec.get('contact_execution_authorized') is not False or spec.get('hardware_verified') is not False:raise ValueError('Task spec never grants contact/hardware permission')
    for key in ('source','robot','scene','evaluation','dependency_hashes','field_provenance'):
        if not isinstance(spec.get(key),dict):raise ValueError(key+' must be an object')
    for key in ('objects','frames','phases','assumptions','unresolved'):
        if not isinstance(spec.get(key),list):raise ValueError(key+' must be a list')
    if not all(key in spec['evaluation'] for key in ('action','retention')):raise ValueError('Action completion and retention evaluation must be separate')
    for check in spec['evaluation'].values():
        if isinstance(check,dict) and (check.get('verified') is True or check.get('hardware_verified') is True or check.get('status') in ('verified','passed','succeeded')):raise ValueError('A task definition cannot self-certify physical/hardware evaluation')
    arms=spec['robot'].get('arms',[])
    if not isinstance(arms,list):raise ValueError('Robot arms must be an explicit list')
    arm_ids=set()
    for arm in arms:
        if not isinstance(arm,dict) or not isinstance(arm.get('id'),str) or arm['id'] in arm_ids:raise ValueError('Robot arm IDs must be unique strings')
        arm_ids.add(arm['id'])
        if arm.get('base_pose') is not None:pose(arm['base_pose'],'robot base pose')
        joints=arm.get('initial_joint_positions',{})
        if not isinstance(joints,dict) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in joints.values()):raise ValueError('Initial joints require finite rad/m values')
    ids=set()
    for item in spec['objects']:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',item['id']) or item['id'] in ids:raise ValueError('Object IDs must be unique USD-safe identifiers')
        if not isinstance(item.get('collision'),dict):raise ValueError('Collision declaration must be an object')
        if item.get('geometry_type') not in ('mesh','box','sphere','cylinder','unknown'):raise ValueError('Unknown geometry representation')
        if item.get('geometry_type')=='box' and item.get('size_m') is not None:
            vector(item['size_m'],3,'box.size_m',False)
            if any(v<=0 for v in item['size_m']):raise ValueError('Box dimensions must be positive SI values')
        if item.get('geometry_type') in ('cylinder','sphere'):positive_scalar(item.get('radius_m'),'primitive.radius_m')
        if item.get('geometry_type')=='cylinder':positive_scalar(item.get('height_m'),'cylinder.height_m')
        ids.add(item['id']);pose(item.get('pose',{}),'objects.'+item['id'])
        vector(item.get('scale'),3,'object scale')
        if item.get('scale') is not None and any(v<=0 for v in item['scale']):raise ValueError('Object scale must be positive')
        mass=item.get('mass_kg')
        if mass is not None and (isinstance(mass,bool) or not isinstance(mass,(int,float)) or not math.isfinite(mass) or mass<=0):raise ValueError('Object mass must be positive kg or unknown')
    table=spec['scene'].get('table',{})
    if table:
        vector(table.get('size_m'),3,'table.size_m');vector(table.get('position_m'),3,'table.position_m')
        if table.get('size_m') and any(v<=0 for v in table['size_m']):raise ValueError('Table size must be positive')
    frame_ids=set()
    for frame in spec['frames']:
        if not isinstance(frame,dict) or not isinstance(frame.get('id'),str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',frame['id']):raise ValueError('Frames require USD-safe explicit IDs')
        identifier=frame['id']
        if identifier in frame_ids or identifier in ids or identifier=='world':raise ValueError('Frame IDs must be unique and unambiguous')
        frame_ids.add(identifier);rigid_transform(frame.get('transform'),'frame '+identifier+' transform')
    parents={frame['id']:frame.get('parent') for frame in spec['frames']}
    allowed=ids|frame_ids|{'world'}
    for identifier,parent in parents.items():
        if parent is not None and (not isinstance(parent,str) or parent not in allowed):raise ValueError('Frame parent must reference world, an object, another frame, or unknown')
        seen={identifier};current=parent
        while current in parents:
            if current in seen:raise ValueError('Frame parent graph cannot contain a cycle')
            seen.add(current);current=parents[current]
    # Fail before serialization on NaN/infinity anywhere, including robot/calibration fields.
    json.dumps(spec,allow_nan=False)
    return spec
