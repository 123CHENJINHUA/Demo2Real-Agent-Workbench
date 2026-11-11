"""Independent scene artifact, support, camera and scope validation."""
from pathlib import Path
import json,hashlib,subprocess,os,struct,math
from contracts.tasks import canonical_hash,validate_task_spec
from modules.environment.service import resolve,local_environment

def validate_manifest(root,manifest,usd_audit):
    root=Path(root).resolve();checks=[]
    def check(name,ok,details):checks.append({'id':name,'passed':bool(ok),'details':details})
    def local(ref):
        p=(root/ref).resolve();p.relative_to(root)
        if not p.is_file():raise ValueError('Scene artifact missing '+str(ref))
        return p
    spec=json.loads(local(manifest['spec_snapshot_path']).read_text());validate_task_spec(spec)
    check('task_hash',manifest['task_spec_hash']==canonical_hash(spec)==spec.get('content_hash'),'Build must bind the exact task revision content, not latest mutable draft')
    dependencies=spec.get('dependency_hashes',{})
    groups=[('asset_hash',dependencies.get('assets',{})),('evidence_hash',dependencies.get('evidence',{}))]
    if dependencies.get('profile'):groups.append(('profile_hash',{dependencies['profile']['path']:dependencies['profile']['sha256']}))
    for kind,group in groups:
        for path,expected in group.items():
            check(kind+':'+path,hashlib.sha256(local(path).read_bytes()).hexdigest()==expected,'Changed dependencies invalidate scene evidence')
    check('usd_composition',usd_audit.get('status')=='passed',usd_audit)
    check('usd_physics_scene',bool(usd_audit.get('physics_scenes')),'USD must contain a real PhysicsScene')
    body_paths=set(usd_audit.get('rigid_bodies',[]))
    for obj in spec['objects']:
        path='/World/Objects/'+obj['id']
        check('usd_rigid_body:'+obj['id'],path in body_paths,'Independent USD inspection, not a manifest claim')
        actual=usd_audit.get('object_world_positions',{}).get(path)
        reported=manifest.get('settled_poses',{}).get(obj['id'],{}).get('position_m')
        if actual is not None and reported is not None:
            error=math.sqrt(sum((a-b)**2 for a,b in zip(actual,reported)))
            check('usd_pose_match:'+obj['id'],error<=.002,{'position_error_m':error,'tolerance_m':.002})
    object_paths={'/World/Objects/'+obj['id']:obj for obj in spec['objects']};forbidden=[]
    for joint in usd_audit.get('joints',[]):
        for own,other in ((joint.get('body0',[]),joint.get('body1',[])),(joint.get('body1',[]),joint.get('body0',[]))):
            for body in own:
                if body not in object_paths:continue
                item=object_paths[body];allowed=item.get('collision',{}).get('type')=='connector_keyed_engineering' and item['collision'].get('role')=='male' and joint.get('type')=='PhysicsPrismaticJoint' and other and all(x in ('/World/MaleLatch0','/World/MaleLatch1') for x in other)
                if not allowed:forbidden.append(joint['path'])
    check('usd_no_forbidden_object_attachment',not forbidden,{'forbidden_joints':sorted(set(forbidden)),'scope':'Object-to-robot/world/other-object joints forbidden; only registered own passive latch spring allowed'})
    check('no_connector_pair_joint',not manifest.get('connector_pair_joints'),manifest.get('connector_pair_joints'))
    check('no_controller_or_hardware',manifest.get('hardware_commands_sent')==0 and manifest.get('robot_motion_controller_loaded') is False and manifest.get('contact_execution_authorized') is False,'Preview may not grant task/robot execution')
    check('object_count',len(manifest.get('settled_poses',{}))==len(spec['objects']),'Each task object has a simulated pose')
    table=spec['scene']['table'];top=table['position_m'][2]+table['size_m'][2]/2
    for obj in spec['objects']:
        name=obj['id'];exact=usd_audit.get('exact_object_bounds',{}).get('/World/Objects/'+name,{})
        if not spec.get('source',{}).get('synthetic_test_fixture'):check('exact_support_available:'+name,bool(exact.get('visual') and exact.get('collision')),'Actual mesh vertices and analytic surface bounds required')
        bounds=exact.get('visual') or manifest['world_visual_bounds'][name];low,high=bounds['min'],bounds['max'];d=manifest['dynamics'][name]
        if exact.get('collision'):check('collision_table_support:'+name,exact['collision']['min'][2]>=top-.003,{'collision_bottom_m':exact['collision']['min'][2],'table_top_m':top,'tolerance_m':.003})
        check('finite_state:'+name,all(math.isfinite(float(v)) for v in [*low,*high,*d['linear_velocity_m_s'],*d['angular_velocity_rad_s']]),'Finite local PhysX state')
        check('table_support:'+name,low[2]>=top-.003 and high[2]>=top-.002,{'table_top_m':top,'visual_bottom_m':low[2],'tolerance_m':.003,'geometry_method':usd_audit.get('support_geometry_version','legacy_fixture_aabb')})
        within=all(low[a]>=table['position_m'][a]-table['size_m'][a]/2-.01 and high[a]<=table['position_m'][a]+table['size_m'][a]/2+.01 for a in (0,1))
        check('within_table:'+name,within,{'bounds':bounds})
        speed=math.sqrt(sum(v*v for v in d['linear_velocity_m_s']));omega=math.sqrt(sum(v*v for v in d['angular_velocity_rad_s']))
        check('settled:'+name,speed<.03 and omega<.5,{'linear_speed_m_s':speed,'angular_speed_rad_s':omega,'scope':'local settling only, no grasp validation'})
    for cam in manifest.get('cameras',[]):
        check('usd_camera_exists:'+cam['id'],cam.get('path') in usd_audit.get('cameras',[]),'Camera must exist in independent USD inspection')
        if cam.get('calibration_path'):
            actual=usd_audit.get('camera_properties',{}).get(cam.get('path'),{})
            arm=next((a for a in spec.get('robot',{}).get('arms',[]) if a.get('id')==cam['id']),None)
            if arm and actual:
                import numpy as np
                binding=arm.get('camera',{});cal=json.loads(local(binding['calibration_path']).read_text());K=np.asarray(cal['intrinsics']['K']);w,h=cal['width'],cal['height'];samples=np.array([[0,0,1],[.1,.05,1],[-.05,-.1,.8]],float);projected=samples@K.T;expected_pixels=projected[:,:2]/projected[:,2:3]
                ndc=np.asarray(actual['projection_ndc_samples']);pixels=(ndc*np.array([1,-1])+1)*np.array([w/2,h/2]);projection_error=float(np.max(np.abs(pixels-expected_pixels)))
                expected_mount=np.asarray(binding['T_parent_sdk_flange'])@np.asarray(cal['hand_eye']['matrix']);mount_error=float(np.max(np.abs(np.asarray(actual['T_parent_camera_optical'])-expected_mount)))
                check('usd_calibrated_projection:'+cam['id'],projection_error<=.001,{'pixel_error':projection_error,'source':'reopened USD lens versus bound calibration'})
                check('usd_camera_mount:'+cam['id'],mount_error<=1e-6 and actual['parent_link_path'].endswith('/'+binding.get('parent_link','gripper_base')),{'matrix_error':mount_error,'parent':actual['parent_link_path'],'source':'reopened USD camera versus bound flange transform'})
            else:check('usd_calibrated_camera:'+cam['id'],False,'Missing actual calibrated USD camera or robot binding')
        p=local(cam['rgb_path']);dimensions=None;contrast=0.;image_error=None
        try:
            from PIL import Image,ImageStat
            with Image.open(p) as image:
                if image.format!='PNG':raise ValueError('Scene preview must be PNG')
                image.verify()
            with Image.open(p) as image:
                image.load();dimensions=image.size
                contrast=math.sqrt(sum(v*v for v in ImageStat.Stat(image.convert('RGB')).stddev)/3)
        except (OSError,ValueError,SyntaxError) as exc:image_error=type(exc).__name__+': '+str(exc)
        check('rendered_image:'+cam['id'],dimensions==(cam['width'],cam['height']) and contrast>1 and image_error is None,{'path':cam['rgb_path'],'dimensions':dimensions,'decoded_contrast':contrast,'error':image_error})
        check('projection:'+cam['id'],cam.get('maximum_projection_error_px',float('inf'))<=.001,{'maximum_projection_error_px':cam.get('maximum_projection_error_px'),'scope':'internal optical/USD geometry consistency, not hardware calibration'})
    warning=['Scene checks are local structure/render/settling checks. Full task action and retention have not been executed.','Robot/world calibration, unknown obstacles and engineering contact parameters remain independently unverified.']
    if any('CONN-006' in str(v) for v in spec.get('assumptions',[])):warning.append('Known connector post-release retention failure remains unresolved (CONN-006).')
    return {'status':'succeeded' if all(v['passed'] for v in checks) else 'failed','checks':checks,'warnings':warning,'scope':'scene_structure_camera_and_local_settling_only','task_spec_hash':spec['content_hash'],'spec_revision':spec['revision'],'full_task_evaluated':False,'action_evaluation':'not_executed','retention_evaluation':'not_executed','hardware_verified':False,'contact_execution_authorized':False}

def run(ctx):
    root=Path(ctx['root']).resolve();out=Path(ctx['output_dir']).resolve();out.relative_to(root);out.mkdir(parents=True,exist_ok=True)
    build=ctx.get('dependencies',{}).get('scene_build',{});reference=build.get('manifest_path') or ctx['input'].get('manifest_path')
    if not reference:return {'status':'needs_configuration','reason':'需要成功场景构建的scene_manifest'}
    path=(root/reference).resolve();path.relative_to(root);manifest=json.loads(path.read_text())
    sim=resolve(root,'isaac_sim_root');libs=next((sim/'extscache').glob('omni.usd.libs-*cp311'))
    env=local_environment(root);env['PYTHONPATH']=str(libs)+os.pathsep+str(root);env['LD_LIBRARY_PATH']=str(libs/'bin')
    audit=out/'usd_validation.json';command=[str(sim/'python.sh'),str(root/'workers/check_scene_usd.py'),'--scene',str(root/manifest['scene_path']),'--output',str(audit)]
    ctx['check_cancel']();p=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=60);(out/'usd_worker.log').write_text(p.stdout+p.stderr)
    if not audit.is_file():return {'status':'failed','error':'USD独立复审未生成报告','failure_category':'runtime_environment','failure_stage':'scene_build','evidence_paths':[str((out/'usd_worker.log').relative_to(root))]}
    report=validate_manifest(root,manifest,json.loads(audit.read_text()));report['support_geometry_version']=json.loads(audit.read_text()).get('support_geometry_version');report['manifest_path']=str(path.relative_to(root));report['scene_signature_without_runtime_graphs']=json.loads(audit.read_text()).get('scene_signature_without_runtime_graphs');report['artifact_sha256']=dict(json.loads(audit.read_text()).get('artifact_sha256',{}));report['artifact_sha256'][str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest();report['report_path']=str((out/'validation_report.json').relative_to(root));(out/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if report['status']=='failed':report.update(error='场景局部验证失败；查看逐项checks',failure_category='physics_contact',failure_stage='scene_build',evidence_paths=[report['report_path']])
    ctx['emit']('scene.validation_completed',{'status':report['status'],'passed_checks':sum(c['passed'] for c in report['checks']),'total_checks':len(report['checks']),'full_task_evaluated':False});return report
