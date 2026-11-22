"""Have a scene agent diagnose failed checks and verify its task-scoped corrections."""
import hashlib
import json
from pathlib import Path
import time

from application.agent_activity import handoff
from application.analysis_agents import request_with_evidence
from application.collision_agent import review as review_collision
from application.scene_cycle import previous_scene, build_preview
from application.scene_visual import apply_review, required as visual_required, review as review_visual
from modules.scene_building.repair import apply, mesh_evidence, motion_geometry_ids
from modules.scene_building.layout import physical_layout_key
from modules.scene_validation.service import run as validate_scene
from modules.scene_validation.settling import settling_evidence
from modules.task_specs import load_snapshot


def _schema(objects, refs, *, motion_review=False, support_loss=False, allow_collision=True):
    xyz = {'type':'array','items':{'type':'number'},'minItems':3,'maxItems':3}
    nullable_xyz = {'anyOf':[xyz,{'type':'null'}]}
    def structure(fields):
        return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
    box = structure({'center_m':xyz,'size_m':xyz})
    object_ids = {'type':'string','enum':objects} if objects else {'type':'string'}
    edit = structure({'object_id':object_ids, 'position_m':nullable_xyz,
                      'rotation_xyz_deg':nullable_xyz,'collision_boxes':{'type':'array','maxItems':8 if allow_collision else 0,'items':box}})
    fields={'diagnosis':{'type':'string'},'evidence_ref':{'type':'string','enum':refs},
            'edits':{'type':'array','maxItems':len(objects),'items':edit}}
    if motion_review:
        fields['rig_translation_m']={'type':'null'} if support_loss else nullable_xyz
    return structure(fields)


def repair(ctx, build, validation, *, clearance_check=None, clearance_evidence=None, motion_feedback=None,
           supervisor_instruction=None):
    root = Path(ctx['root']).resolve()
    out = Path(ctx['output_dir'])/('scene_repair_'+str(time.time_ns()))
    out.mkdir(parents=True)
    history = []
    seen = set()
    report = out/'repair_history.json'
    starting = {'task_spec_hash':build['task_spec_hash'],'manifest_path':build['manifest_path'],
                'validation':validation.get('validation_report_path') or validation.get('report_path')}
    supervised=supervisor_instruction is not None
    support_loss=not supervised and (motion_feedback or {}).get('kind')=='initial_support_loss'
    if clearance_evidence:
        starting['clearance'] = clearance_evidence
    if motion_feedback:
        starting['motion_feedback']=motion_feedback
    if supervised:
        starting['supervisor_instruction']=supervisor_instruction
        starting['verified_trial_review']=validation.get('verified_trial_review',[])
    prior_repairs=[]
    if supervised and (prior_ref:=validation.get('repair_report_path')):
        starting['previous_repair']=prior_ref
        try:
            prior_path=(root/prior_ref).resolve()
            prior_path.relative_to(root)
            previous=json.loads(prior_path.read_text())
            prior_repairs=[{key:row[key] for key in ('source_spec_hash','status','manifest_path','validation','diagnosis') if key in row}
                           for row in previous['attempts'][-3:]]
        except (OSError, ValueError, TypeError, KeyError):
            starting['previous_repair_unavailable']=True

    def save():
        report.write_text(json.dumps({'starting_scene':starting,'attempts':history},ensure_ascii=False,indent=2,allow_nan=False))

    def problems(result):
        return [check for check in result.get('checks',[]) if not check['passed']]

    save()
    attempts=1 if motion_feedback or supervised else 3
    for attempt in range(1,attempts+1):
        ctx['check_cancel']()
        folder = out/f'attempt_{attempt}'
        folder.mkdir()
        old = load_snapshot(root,build['spec_path'],build['task_spec_hash'])
        seen.add(physical_layout_key(old))
        if not old.get('scene_approximation'):
            break
        motion_object=(motion_feedback or {}).get('object_id') if not supervised else None
        editable=motion_geometry_ids(old,motion_object) if motion_object else [obj['id'] for obj in old['objects']]
        measurements = {}
        for obj in old['objects']:
            ctx['check_cancel']()
            if obj['id'] in editable and obj.get('asset_path') and obj['geometry_type']=='mesh':
                try:
                    measurements[obj['id']] = mesh_evidence(root,obj)
                except (OSError,ValueError) as exc:
                    ctx['emit']('scene.mesh_evidence_unavailable',{'object_id':obj['id'],'reason':str(exc)})
        manifest_path = root/build['manifest_path']
        manifest_bytes=manifest_path.read_bytes()
        digest=validation.get('scene_manifest_sha256') or (validation.get('artifact_sha256') or {}).get(build['manifest_path'])
        if digest and hashlib.sha256(manifest_bytes).hexdigest()!=digest:
            raise ValueError('场景清单在验证和纠错交接之间发生变化')
        manifest = json.loads(manifest_bytes)
        failed = problems(validation)
        entry = {'attempt':attempt,'source_spec_hash':old['content_hash'],'input_checks':failed,'status':'diagnosing'}
        previous_attempts = prior_repairs + [{key:row[key] for key in ('diagnosis','status','failed_checks','error') if key in row}
                                            for row in history]
        history.append(entry)
        save()
        binding = previous_scene(root,build,run_id=ctx['run_id'],round_number=build.get('refinement_round',0))
        ctx['emit']('task.progress',{'message':f'场景纠错 Agent 正在分析失败项 · 第 {attempt} 次','completed':attempt-1,'total':attempts,'unit':'次'})
        correction_ref = ((build.get('refinement_rounds') or [{}])[-1]).get('corrections')
        intent = json.loads((root/correction_ref).read_text()).get('layout_estimates',{}) if correction_ref else {}
        evidence = {'checks':failed,'objects':old['objects'],'table':old['scene']['table'],
                    'settled_poses':manifest['settled_poses'],'mesh_slices':measurements,'previous_layout_intent':intent,
                    'previous_attempts':previous_attempts,'scene_manifest':build['manifest_path'],
                    'validation_report':validation.get('report_path'), 'settling_review':settling_evidence(manifest,failed)}
        clearance_prompt = ''
        if clearance_evidence:
            evidence['insertion_clearance'] = clearance_evidence
            editable = [clearance_evidence['panel_id'],clearance_evidence['slot_id']]
            clearance_prompt = ('This handoff is for upright insertion clearance. Edit only the collision boxes of the '
                                'named panel and slot. Keep their world poses, measured aperture, slot floor and insertion '
                                'depth. Fit boxes to the measured contact surfaces. The same independent upright fit check '
                                'must pass before building, followed by the local PhysX validation. ')
        prompt = (
            'You are the scene repair agent for this specific offline demonstration. Diagnose the failed checks using '
            'the recorded images, simulated views and numeric scene evidence below. Propose only concrete changed object poses '
            'and, where measured slices expose a needed concavity, two to eight axis-aligned collision boxes. Coordinates of pose '
            'are world meters and extrinsic xyz degrees; collision boxes use the object mesh local axes after centering and scaling '
            'but before object rotation. Preserve physical openings and floor, and check slot long axis versus the panel. A '
            'single convex hull fills an open slot even when its visible mesh has a gap. Use numeric slice profiles to locate its '
            'walls and floor. For supported objects choose the support pose before the object pose; use the actual clearance '
            'of the contacting part, not a nominal mesh envelope if a protrusion is above the slot. Preserve unrelated objects. '
            'Never alter the table, original assets, frozen checks, robot or evaluator. If evidence cannot support a correction, '
            'return an empty edit list and explain why. Write the diagnosis in Chinese. '+clearance_prompt
            +'For an evidenced pose difference, use matching features to propose the required full rotation and adjust support. '
            'An uncertain observation by itself does not justify rotating an object or a camera. '
            'Prioritize the workpiece, its start support and its destination; disregard incidental details that do not affect the manipulation. '
            'Keep visually verified orientation if repairing physical stability or clearance. '
            'Fix measured workspace spacing against the supplied working-surface coordinates, not against an unmatched camera. '
            +'\n'+json.dumps(evidence,ensure_ascii=False)
        )
        if supervised:
            evidence['motion_feedback']=motion_feedback
            evidence['verified_trial_review']=starting['verified_trial_review']
            evidence['robot_registration']=old['robot'].get('preview_registration')
            prompt=('You are the scene specialist for the current demonstration. Compare the actual views, numeric geometry, '
                    'and the failure evidence. Identify which scene assumptions explain the observation. Choose a correction '
                    'grounded in these measurements. Position and XYZ orientation edits are in the world frame. Collision boxes '
                    'use the centered, scaled object mesh coordinates before object rotation and must follow measured contact surfaces. '
                    'If provisional robot registration is at fault, rig_translation_m may specify a common world translation '
                    'of 2–120 mm. Use null otherwise. Preserve frozen evaluation and user-supplied source meshes. '
                    'Use the recomputed trial evidence to distinguish actual workpiece motion from motion of the reference object. '
                    'A region correction gives the nearest boundary in the frame at the sampled time. It does not prescribe a scene edit. '
                    'Settling samples refer to the indicated scene manifest. Compare the workpiece with its declared support over '
                    'the sampled interval. A declared support identifies a reference body, it does not establish actual contact. '
                    'Return empty edits and null when the evidence cannot justify a change. Explain in Chinese and cite a useful frame.'
                    +'\nThe supervisor asked for this investigation: '+supervisor_instruction
                    +'\nCurrent task evidence: '+json.dumps(evidence,ensure_ascii=False))
        elif motion_feedback:
            evidence['motion_feedback']=motion_feedback
            evidence['robot_bases']=[{'id':arm['id'],'base_pose':arm['base_pose']} for arm in old['robot'].get('arms',[])]
            evidence['editable_collision_ids']=editable
            prompt=('Review an unexpected contact or premature object fall in the task-specific offline approach. Distinguish '
                    'provisional robot-to-scene alignment, a workpiece or fixture placement or collision-proxy error, and '
                    'the approach trajectory. Compare the beginning of '
                    'the recording to the current render. Only when the recorded robot-to-workspace landmarks support it, propose '
                    'a common world translation in meters for the estimated rig (2–120 mm in magnitude) with edits empty. '
                    'When the supplied mesh cross-sections establish a mismatch such as a solid convex hull sealing a load-bearing '
                    'opening, propose two to eight local collision boxes matching the measured contact surfaces for an editable ID. '
                    'Keep the visible meshes, object position and orientation and evaluation unchanged. Use null position_m and '
                    'rotation_xyz_deg in every collision edit and null rig_translation_m. Preserve the walls and floor of an opening. '
                    'If evidence supports only a motion error or no specific scene change, use null translation and empty edits. '
                    'Explain the evidence in Chinese. '
                    'A collision event alone does not establish a wrong scene. Cite a useful recorded frame. '
                    +('The workpiece moved relative to its support in the planned contact-free approach before a forceful robot '
                       'contact was recorded. Concentrate on its initial support and collision geometry. Set rig_translation_m to null. '
                       if support_loss else '')
                    +json.dumps(evidence,ensure_ascii=False))
        images=[root/view['path'] for view in binding['views']]
        audit=ctx['dependencies'].get('data_audit') or {}
        bound_visual_evidence=bool(visual_required(ctx) and (audit.get('sample_frames') or audit.get('videos')))
        collision=None
        local_failure=any(row['id'].startswith(('settled:','collision_','initial_support_loss')) for row in failed)
        if local_failure or motion_feedback or clearance_evidence:
            collision=review_collision(ctx | {'output_dir':folder},old,checks=failed,
                                      motion={'fault':motion_feedback,'settling':evidence['settling_review'],
                                              'verified_trials':validation.get('verified_trial_review')},
                                      comparison_images=images,targets=editable,clearance=clearance_evidence)
        if collision:
            entry['collision_review_path']=collision['report_path']
        if collision and collision['status']=='candidate':
            revised=load_snapshot(root,collision['spec_path'],collision['spec_hash'])
            entry.update(model_status=collision.get('model_status'),diagnosis=collision['proposal']['diagnosis'],
                         proposal=collision['proposal'])
        else:
            if bound_visual_evidence:
                prompt=('You are the layout specialist for this offline demonstration. Compare the image evidence with the '
                        'current render and actual measurements. Propose evidence-backed object world poses for the task. '
                        'Keep collision_boxes empty. A separate specialist owns collision geometry. Preserve meshes, '
                        'the table and frozen checks. Use a common rig translation only for evidenced estimated alignment '
                        'when the output schema allows it. Explain in Chinese and cite a frame. '
                        +('\nSupervisor instruction: '+supervisor_instruction if supervised else '')
                        +'\nScene evidence: '+json.dumps(evidence | {'motion_feedback':motion_feedback},ensure_ascii=False))
            response,proposal,refs = request_with_evidence(ctx | {'output_dir':folder,'stage':'scene_repair'},prompt,
                lambda files:_schema(editable,files,motion_review=bool(motion_feedback) or supervised,support_loss=support_loss,
                               allow_collision=collision is None and not bound_visual_evidence),
                comparison_images=[root/view['path'] for view in binding['views']],
                **({'frame_selection':'task_phases'} if supervised else {'frame_selection':'initial'} if motion_feedback else {}))
            if not isinstance(proposal,dict):
                proposal = None
            try:
                json.dumps(proposal,allow_nan=False)
            except (ValueError,TypeError):
                proposal = None
            entry.update(model_status=response['status'],diagnosis=(proposal or {}).get('diagnosis'),proposal=proposal)
            has_edit=proposal and (proposal.get('edits') or ((motion_feedback or supervised) and proposal.get('rig_translation_m') is not None))
            if response['status']!='succeeded' or not has_edit:
                entry.update(status='waiting',error=response.get('error') or response.get('reason') or entry['diagnosis'])
                save()
                break
            manifest_rel = manifest_path.relative_to(root).as_posix()
            hashes = {manifest_rel:hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
            validation_ref = validation.get('report_path')
            if validation_ref:
                hashes[validation_ref] = hashlib.sha256((root/validation_ref).read_bytes()).hexdigest()
            if visual_ref := validation.get('visual_comparison_path'):
                hashes[visual_ref] = hashlib.sha256((root/visual_ref).read_bytes()).hexdigest()
            hashes.update({view['path']:view['sha256'] for view in binding['views']})
            if proposal.get('evidence_ref') in refs:
                ref = proposal['evidence_ref']
                hashes[ref] = hashlib.sha256((root/ref).read_bytes()).hexdigest()
            elif motion_feedback:
                entry.update(status='rejected',error='基座修正缺少可核对的示教帧引用')
                save()
                break
            try:
                if (collision is not None or bound_visual_evidence) and any(edit['collision_boxes'] for edit in proposal['edits']):
                    raise ValueError('已导入示教的碰撞几何由独立 Agent 修正')
                if support_loss and proposal.get('rig_translation_m') is not None:
                    raise ValueError('接触前支撑失稳需要核对工件和支撑的碰撞几何')
                revised = apply(old,proposal,measurements,hashes,motion_object_id=motion_object,
                                allow_estimated_alignment=supervised)
            except (KeyError,TypeError,ValueError) as exc:
                entry.update(status='rejected',error=str(exc))
                save()
                continue
        layout_key=physical_layout_key(revised)
        if layout_key in seen:
            entry.update(status='unchanged',error='纠错方案与已检查的场景重复')
            save()
            break
        seen.add(layout_key)
        snapshot = folder/'corrected_task_spec.json'
        snapshot.write_text(json.dumps(revised,ensure_ascii=False,indent=2,allow_nan=False))
        layout = {'spec_path':snapshot.relative_to(root).as_posix(),'spec_hash':revised['content_hash']}
        if clearance_check:
            try:
                entry['clearance'] = clearance_check(revised)
            except (OSError,KeyError,TypeError,ValueError) as exc:
                entry.update(status='rejected',error=str(exc),spec_path=layout['spec_path'])
                save()
                continue
        entry.update(status='building',spec_path=layout['spec_path'])
        save()
        ctx['emit']('task.progress',{'message':f'重建纠错后的场景 · 第 {attempt} 次'})
        candidate = build_preview(ctx | {'output_dir':folder,'collision_repair_pass':True,
                                         'dependencies':ctx['dependencies'] | {'scene_layout':layout}})
        if candidate['status']!='succeeded':
            entry.update(status='build_failed',error=candidate.get('error') or candidate.get('reason'))
            save()
            continue
        if clearance_check and candidate['task_spec_hash']!=layout['spec_hash']:
            entry.update(status='rejected',error='重建后的场景与入槽核对的任务快照不一致')
            save()
            continue
        with handoff(ctx,'场景 Agent','场景校验 Agent','独立检查修正版场景',
                     evidence_path=candidate['manifest_path']) as finish:
            checked = validate_scene(ctx | {'output_dir':folder/'validation','dependencies':{'scene_build':candidate}})
            physical_passed = checked['status']=='succeeded'
            if physical_passed and visual_required(ctx):
                checked = apply_review(checked,review_visual(ctx | {'output_dir':folder},candidate))
            finish(checked,summary='修正版场景通过检查' if checked['status']=='succeeded' else None,
                   report_path=checked.get('visual_comparison_path') or checked.get('report_path'))
        entry.update(status=checked['status'],validation=checked.get('report_path'),failed_checks=problems(checked),
                     manifest_path=candidate['manifest_path'],visual_comparison_path=checked.get('visual_comparison_path'))
        save()
        if checked['status']=='succeeded':
            repair_ref=report.relative_to(root).as_posix()
            if candidate.get('preview_path') and not motion_feedback:
                ctx['emit']('scene.published',{'build':candidate,'validation_path':checked.get('report_path'),
                    'repair_report_path':repair_ref,'visual_comparison_path':checked.get('visual_comparison_path'),
                    'visual_comparison_status':checked.get('visual_comparison_status')})
            return checked | {'resolved_scene_build':candidate,'repair_report_path':repair_ref}
        unverified_orientation=checked.get('visual_comparison_status')=='orientation_unverified'
        if unverified_orientation or (not clearance_check and problems(checked)
                                      and (physical_passed or len(problems(checked))<=len(failed))):
            build,validation = candidate | {'refinement_rounds':build.get('refinement_rounds',[])},checked
        if unverified_orientation:
            break
    labels = '、'.join(item['id'] for item in problems(validation)[:4])
    return validation | {'status':'blocked','error':f'场景纠错仍待解决（{labels or "检查证据不足"}）',
                         'failed_scene_build':build,'manifest_path':build['manifest_path'],'task_spec_hash':build['task_spec_hash'],
                         'validation_report_path':validation.get('validation_report_path') or validation.get('report_path'),
                         'report_path':report.relative_to(root).as_posix(),
                         'repair_report_path':report.relative_to(root).as_posix()}
