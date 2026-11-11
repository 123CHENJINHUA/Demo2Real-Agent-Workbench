"""Recompute task outcomes from recorded states, independently of controller booleans."""
from pathlib import Path
import hashlib,json,subprocess,os,xml.etree.ElementTree as ET
import numpy as np
from modules.environment.service import resolve,local_environment
from contracts.experiments import apply_scope


def failure_category(result,report):
    error=str(result.get('report',{}).get('error') or result.get('error') or '')
    if any(word in error for word in ('IK','reachable','key candidate')):return 'planning_control'
    if error and all(v['passed'] is None for v in report['stage_results'].values()):return 'runtime_environment'
    return 'physics_contact'


def evaluate_trace(root,plan,result,trace):
    from tasks.connector.components.keying_geometry import keyed_error,keyed_retained,profile
    root=Path(root).resolve();checks=[]
    def check(name,ok,details=None):checks.append({'id':name,'passed':bool(ok),'details':details})
    c=profile();spec=json.loads((root/plan['spec_path']).read_text())
    arrays={name:np.asarray(trace[name]) for name in ('time','phase','objects','joints','jaws','commanded_joints','contact_forces_N','latch_deflections_m') if name in trace}
    n=len(arrays.get('time',[]));required=('time','phase','objects','joints','jaws','commanded_joints','contact_forces_N','latch_deflections_m')
    shapes={'objects':(n,2,4,4),'joints':(n,2,7),'jaws':(n,2,2),'commanded_joints':(n,2,7),'contact_forces_N':(n,4,2,3),'latch_deflections_m':(n,2),'time':(n,),'phase':(n,)}
    valid=n>2 and all(name in arrays and arrays[name].shape==shapes[name] for name in required)
    check('complete_trace_shapes',valid,{k:list(v.shape) for k,v in arrays.items()})
    finite=valid and all(np.isfinite(arrays[k]).all() for k in required if k!='phase');check('finite_trace',finite)
    stages={k:{'passed':None,'details':'stage not reached or trace unavailable'} for k in ('grasp','insertion','release','retention')}
    if finite:
        t=arrays['time'];o=arrays['objects'];q=arrays['joints'];jaws=arrays['jaws'];forces=np.linalg.norm(arrays['contact_forces_N'],axis=-1);clips=arrays['latch_deflections_m'];phases=arrays['phase'].astype(str)
        check('monotonic_simulation_clock',bool(np.all(np.diff(t)>0)),{'start_s':float(t[0]),'end_s':float(t[-1])})
        rotation=o[:,:,:3,:3];check('rigid_object_transforms',np.allclose(o[:,:,3,:],[0,0,0,1],atol=1e-5) and np.allclose(rotation.swapaxes(-1,-2)@rotation,np.eye(3),atol=1e-4) and np.allclose(np.linalg.det(rotation),1,atol=1e-4))
        limits=[]
        for arm in spec['robot']['arms']:
            xml=ET.parse(root/arm['urdf_path']).getroot();joints={j.attrib['name']:j for j in xml.findall('joint')};limits.append([[float(joints['joint'+str(i)].find('limit').get('lower')),float(joints['joint'+str(i)].find('limit').get('upper'))] for i in range(1,8)])
        limits=np.asarray(limits);check('measured_joint_limits',np.all(q>=limits[None,:,:,0]-.002) and np.all(q<=limits[None,:,:,1]+.002),'URDF limits with 0.002rad numerical tolerance')
        command=arrays['commanded_joints'];check('commanded_joint_limits',np.all(command>=limits[None,:,:,0]-1e-6) and np.all(command<=limits[None,:,:,1]+1e-6))
        check('no_lowering_phase',not any('lower' in p or 'place' in p for p in set(phases)),{'phases':list(dict.fromkeys(phases))})
        grasp=np.flatnonzero(phases=='dex_grasp');release=np.flatnonzero(phases=='release');coop=np.flatnonzero(np.isin(phases,['dex_coordination_closed_loop','cooperative_insert']))
        if len(grasp):
            k=int(grasp[-1]);details=[]
            for a in range(2):
                lift=float(o[k,a,2,3]-o[0,a,2,3]);gap=float(jaws[k,a,0]-jaws[k,a,1]);contact=forces[k,a*2:a*2+2,a];ok=lift>.035 and .008<gap<.035 and bool(np.all(contact>.2));details.append({'arm':a,'lift_m':lift,'jaw_gap_m':gap,'contact_forces_N':contact.tolist(),'passed':ok})
            stages['grasp']={'passed':all(d['passed'] for d in details),'details':details}
        if len(release):
            k=int(release[0])-1
            if k>=0:
                error=keyed_error(o[k],config=c);flexed=bool(np.all(np.max(clips[:k+1],axis=0)>.0001));seated=bool(np.all(np.abs(clips[k])<c['latch_seated_deflection_m']));depth=spec['evaluation']['action']['depth_window_m'];engaged=keyed_retained(error,c) and flexed and seated;inserted=bool(engaged and depth[0]<=error['depth_m']<=depth[1] and abs(error['roll_error_rad'])<c['evaluation_roll_rad'] and error['lateral_error_m']<.0012 and error['angle_rad']<.06)
                stages['insertion']={'passed':inserted,'details':dict(error,both_latches_flexed=flexed,both_latches_seated=seated,engaged=bool(engaged))}
            gaps=(jaws[-1,:,0]-jaws[-1,:,1]);released=bool(np.min(gaps)>.06 and np.max(forces[-1])<.1)
            stages['release']={'passed':released,'details':{'jaw_gaps_m':gaps.tolist(),'max_finger_object_force_N':float(np.max(forces[-1]))}}
            error=keyed_error(o[-1],config=c);seated=bool(np.all(np.abs(clips[-1])<c['latch_seated_deflection_m']));flexed=bool(np.all(np.max(clips,axis=0)>.0001));retained=bool(keyed_retained(error,c) and seated and flexed)
            stages['retention']={'passed':retained,'details':dict(error,both_latches_seated=seated,both_latches_flexed=flexed)}
            check('direct_release_arm_targets_hold',np.max(np.abs(command[release]-command[release[0]]))<1e-6,'Both arm commands remain fixed while jaws open; no lowering')
    check('ground_truth_teacher_scope',result.get('expert_perception')==('simulated_pose_estimate' if plan.get('recovery') else 'simulator_ground_truth'),'Not a vision/student or hardware evaluation')
    check('no_hardware_commands',result.get('hardware_commands_sent')==0 and result.get('hardware_verified',False) is False)
    check('loaded_exact_scene',result.get('source_scene_path',result.get('loaded_scene_path'))==json.loads((root/plan['scene_manifest_path']).read_text())['scene_path'])
    for key in ('task_spec_hash','spec_revision'):
        check('binding_'+key,result.get(key)==spec.get('content_hash' if key=='task_spec_hash' else 'revision'))
    action=all(stages[k]['passed'] is True for k in ('grasp','insertion','release'));retention=stages['retention']['passed'];integrity=all(v['passed'] for v in checks)
    return {'status':'succeeded' if integrity and action and retention is True else 'failed','checks':checks,'stage_results':stages,'action_passed':bool(action and integrity),'retention_passed':retention,'full_task_passed':bool(integrity and action and retention is True),'scope':'fixed_scene_ground_truth_scripted_expert_only','hardware_verified':False,'hardware_commands_sent':0,'generalization_evaluated':False,'policy_trained':False,'task_spec_hash':spec['content_hash'],'spec_revision':spec['revision']}


def run(ctx):
    from modules.planning.expert_binding import load_plan
    root=Path(ctx['root']).resolve();out=Path(ctx['output_dir']).resolve();out.relative_to(root);out.mkdir(parents=True,exist_ok=True)
    execution=ctx.get('dependencies',{}).get('expert_execution',{});reference=execution.get('plan_path') or ctx['input'].get('plan_path');plan=load_plan(root,reference)
    report_path=(root/execution['report_path']).resolve();report_path.relative_to(root);raw=json.loads(report_path.read_text());result=dict(raw);result.update({k:execution[k] for k in ('task_spec_hash','spec_revision','expert_perception','hardware_commands_sent','hardware_verified') if k in execution})
    trace_ref=result.get('trajectory_path')
    if not isinstance(trace_ref,str) or not (root/trace_ref).is_file():
        report={'status':'failed','error':'执行未产生轨迹，独立动作验收未开展：'+str(result.get('error') or result.get('report',{}).get('error','')),'checks':[{'id':'actual_execution_trace_exists','passed':False}],'stage_results':{k:{'passed':None,'details':'未产生实际轨迹，未验收'} for k in ('grasp','insertion','release','retention')},'action_passed':None,'retention_passed':None,'full_task_passed':False,'hardware_commands_sent':0,'hardware_verified':False,'failure_category':'runtime_environment','failure_stage':'simulation_test','report_path':str((out/'expert_validation.json').relative_to(root)),'expert_report_path':str(report_path.relative_to(root))}
        report['evidence_paths']=[report['report_path'],report['expert_report_path']];(root/report['report_path']).write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
    trace_path=(root/trace_ref).resolve();trace_path.relative_to(root)
    with np.load(trace_path,allow_pickle=False) as trace:report=evaluate_trace(root,plan,result,trace)
    scene_ref=result.get('execution_scene_path');audit=None
    if scene_ref:
        sim=resolve(root,'isaac_sim_root');libs=next((sim/'extscache').glob('omni.usd.libs-*cp311'));env=local_environment(root);env['PYTHONPATH']=str(libs)+os.pathsep+str(root);env['LD_LIBRARY_PATH']=str(libs/'bin');audit_path=out/'execution_usd_audit.json'
        process=subprocess.run([str(sim/'python.sh'),str(root/'workers/check_scene_usd.py'),'--scene',str(root/scene_ref),'--output',str(audit_path)],env=env,cwd=root,capture_output=True,text=True,timeout=60);(out/'usd_worker.log').write_text(process.stdout+process.stderr)
        if audit_path.is_file():audit=json.loads(audit_path.read_text())
    objects={'/World/Objects/'+obj['id']:obj for obj in json.loads((root/plan['spec_path']).read_text())['objects']};forbidden=[]
    for joint in (audit or {}).get('joints',[]):
        for own,other in ((joint['body0'],joint['body1']),(joint['body1'],joint['body0'])):
            for body in own:
                if body in objects:
                    allowed=objects[body].get('collision',{}).get('role')=='male' and joint['type']=='PhysicsPrismaticJoint' and other and all(p in ('/World/MaleLatch0','/World/MaleLatch1') for p in other)
                    if not allowed:forbidden.append(joint['path'])
    usd_ok=bool(audit and audit.get('status')=='passed' and audit.get('physics_scenes') and all(p in audit.get('rigid_bodies',[]) for p in objects) and not forbidden)
    preparation=result.get('scene_preparation')
    if preparation:
        prepared=(root/preparation['prepared_scene_path']).resolve();prepared.relative_to(root);prepared_audit=out/'prepared_usd_audit.json'
        p=subprocess.run([str(sim/'python.sh'),str(root/'workers/check_scene_usd.py'),'--scene',str(prepared),'--output',str(prepared_audit)],env=env,cwd=root,capture_output=True,text=True,timeout=60)
        prep=json.loads(prepared_audit.read_text()) if prepared_audit.is_file() else {}
        source=(root/plan['scene_path']).resolve();source_validation=json.loads((root/plan['scene_validation_path']).read_text())
        expected_signature=source_validation.get('scene_signature_without_runtime_graphs')
        prep_ok=bool(prep.get('status')=='passed' and expected_signature and prep.get('scene_signature_without_runtime_graphs')==expected_signature and result.get('loaded_scene_path')==str(prepared.relative_to(root)) and preparation['original_scene_sha256']==hashlib.sha256(source.read_bytes()).hexdigest() and preparation['prepared_scene_sha256']==hashlib.sha256(prepared.read_bytes()).hexdigest())
        report['checks'].append({'id':'only_transient_renderer_changes','passed':prep_ok,'details':{'source_signature':expected_signature,'prepared_signature':prep.get('scene_signature_without_runtime_graphs')}})
        if not prep_ok:usd_ok=False
    report['checks'].append({'id':'execution_usd_no_object_attachment','passed':usd_ok,'details':{'forbidden_joints':forbidden,'actual_usd_audit':str((out/'execution_usd_audit.json').relative_to(root))}})
    if not usd_ok:report.update(status='failed',full_task_passed=False,action_passed=False)
    report['evidence_sha256']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (report_path,trace_path)}
    report['report_path']=str((out/'expert_validation.json').relative_to(root));report['expert_report_path']=str(report_path.relative_to(root));report['trajectory_path']=str(trace_path.relative_to(root))
    if not report['full_task_passed']:
        report.update(error='离线专家尚未满足完整动作/保持或证据检查；查看分阶段结果',failure_category=failure_category(result,report),failure_stage='simulation_test',evidence_paths=[report['report_path'],str(report_path.relative_to(root))])
    apply_scope(report,plan.get('evaluation_scope','full_task'))
    if report['status']=='failed' and plan.get('evaluation_scope')=='action_only':
        report['error']='本轮动作或证据检查未通过：'+str(result.get('report',{}).get('error') or '查看独立stage_results和checks')
    if report['status']=='succeeded':
        if report.get('retention_passed') is False:
            report['issues']=[{'failure_category':'physics_contact','failure_stage':'simulation_test','error':'保持失败按用户要求仅记录；动作实验通过，不等于完整任务通过','evidence_paths':[report['report_path']]}]
        for key in ('error','failure_category','failure_stage','evidence_paths'):report.pop(key,None)
    (root/report['report_path']).write_text(json.dumps(report,ensure_ascii=False,indent=2));ctx['emit']('expert.validation_completed',{'status':report['status'],'action_passed':report['action_passed'],'retention_passed':report['retention_passed'],'full_task_passed':report['full_task_passed']});return report
