"""Review the manipulation and placement geometry against recorded RGB views."""
import hashlib
import json
from pathlib import Path
import time

from application.analysis_agents import request_with_evidence, selected_frame_evidence
from modules.scene_building.planar_alignment import compare
from modules.task_specs import load_snapshot


DIFFERENCES = ("misplaced","misoriented","inverted")
ASSESSMENTS = ("consistent",*DIFFERENCES,"uncertain","indistinguishable")
REQUIRED_ROLES = ("manipulated","initial_placement","target_placement")
ROLES = (*REQUIRED_ROLES,"functional_fixture","context_only")
WORKSPACE = "__workspace__"
SCOPE = "recorded_task_placement/v2"


def required(ctx):
    inputs = ctx["input"]
    return bool(not inputs.get("diagnostic_only") and (inputs.get("source_path") or inputs.get("source_import_manifest_path")))


def _evidence_file(root, ref):
    path = root/ref
    if path.resolve()!=path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("场景对照证据路径无效")
    return path


def _capture(root, build):
    raw = _evidence_file(root,build["manifest_path"]).read_bytes()
    manifest = json.loads(raw)
    if manifest.get("task_spec_hash",build["task_spec_hash"])!=build["task_spec_hash"]:
        raise ValueError("场景渲染与当前任务快照不一致")
    views = []
    for camera in manifest["cameras"]:
        path = _evidence_file(root,camera["rgb_path"])
        if 0 < path.stat().st_size <= 6_000_000:
            views.append({"id":camera["id"],"path":camera["rgb_path"],"calibrated":bool(camera.get("calibration_path")),
                          'approximate_reference_view':bool(camera.get('approximate_reference_view')),
                          'recorded_camera':camera.get('recorded_camera'),
                          "sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    if not views:
        raise ValueError("场景没有可用于方向核对的渲染图像")
    return manifest,views,hashlib.sha256(raw).hexdigest()


def previous_scene(root, build, *, run_id, round_number):
    _,views,_ = _capture(root,build)
    return {"round":round_number,"run_id":run_id,"spec_path":build["spec_path"],
            "spec_hash":build["task_spec_hash"],"views":views}


def _task_intent(ctx, root, manifest, task_spec_hash):
    spec_ref = manifest.get("spec_snapshot_path")
    spec = load_snapshot(root,spec_ref,task_spec_hash) if spec_ref else {}
    proposal_ref = spec.get("source",{}).get("proposal_path")
    analysis = json.loads(_evidence_file(root,proposal_ref).read_text()) if proposal_ref else ctx["dependencies"].get("task_analysis") or {}
    proposal = analysis.get("proposal") or {}
    return {"summary":proposal.get("task_summary"),"phases":spec.get("phases") or proposal.get("phases",[]),
            "roles":[{"name":row.get("name"),"role":row.get("role")} for row in proposal.get("objects",[]) if isinstance(row,dict)],
            "scene_supports":(manifest.get("scene_approximation") or {}).get("object_roles",{}),
            "motion_contact_estimates":(manifest.get('scene_approximation') or {}).get('motion_anchor_review',[]),
            "placement_criteria":(spec.get("evaluation") or {}).get("action",{}).get("criteria",[])}


def _schema(refs, simulated, targets):
    string = {"type":"string"}
    landmark = {"anyOf":[string,{"type":"null"}]}
    def choices(values):
        return {"type":"string","enum":list(values)}
    def record(fields):
        return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}
    return record({
        "task_checks":{"type":"array","minItems":len(REQUIRED_ROLES),"items":record({
            "role":choices(ROLES),"object_id":choices(name for name in targets if name!=WORKSPACE),
            "target":choices(targets),"assessment":choices(ASSESSMENTS),
            "recorded_ref":choices(refs),"simulated_ref":choices(simulated),
            "recorded_cue":landmark,"simulated_cue":landmark,"cue":string})},
    })


def _camera_role(name):
    suffix = name.rsplit(".",1)[-1]
    return {"left_wrist":"left","right_wrist":"right","center":"center","front":"front","side":"side"}.get(suffix)


def _checks(parsed, recorded, views, targets):
    rows = parsed.get("task_checks") if isinstance(parsed,dict) else None
    if not isinstance(rows,list):
        return []
    checked = {}
    review_roles = {}
    simulated = {view["path"]:view for view in views}
    recorded_views = {item["path"]:item for item in recorded}
    for row in rows:
        if not isinstance(row,dict):
            return []
        role,object_id,target,state = (row.get(key) for key in ("role","object_id","target","assessment"))
        if role=="context_only":
            continue
        cue = row.get("cue")
        real_ref,render_ref = row.get("recorded_ref"),row.get("simulated_ref")
        if (not all(isinstance(value,str) for value in (role,object_id,target,state,real_ref,render_ref)) or
                role not in ROLES or object_id not in targets or object_id==WORKSPACE or target not in targets or
                (role=="manipulated" and target!=object_id) or state not in ASSESSMENTS or
                real_ref not in recorded_views or render_ref not in simulated or
                not isinstance(cue,str) or not cue.strip()):
            return []
        identifier = f"task_placement:{object_id}:{role}:{target}"
        if identifier in checked:
            return []
        view = simulated[render_ref]
        recorded_view=recorded_views[real_ref]
        camera=recorded_view.get('camera')
        camera_role=_camera_role(camera or '')
        if view.get('recorded_camera') and view['recorded_camera']!=camera:
            return []
        if view["calibrated"] and camera_role and view["id"]!=camera_role:
            return []
        pairing=('landmark_fitted_view' if view.get('recorded_camera') else
                 'calibrated_camera_reference' if view['calibrated'] and camera_role==view['id']
                 and not view.get('approximate_reference_view') else
                 'approximate_view' if camera_role==view['id'] else 'different_view')
        if (state=="indistinguishable" and role!="manipulated") or (
                state!="uncertain" and not all(isinstance(value,str) and value.strip() for value in (
                row.get("recorded_cue"),row.get("simulated_cue")))):
            state = "uncertain"
            row = row | {"assessment":state,"cue":"本项缺少足够的任务相关对照证据。"}
        review_roles.setdefault(object_id,set()).add(role)
        checked[identifier] = {"id":identifier,"passed":state in ("consistent","indistinguishable"),
                               "details":row | {"object_label":targets[object_id],"target_label":targets[target],
                                                'recorded_camera':camera,'simulated_camera':view['id'],'pairing':pairing,
                                                'recorded_episode_seconds':recorded_view.get('episode_relative_seconds')}}
    mandatory = set(REQUIRED_ROLES)
    return list(checked.values()) if review_roles and all(mandatory<=roles for roles in review_roles.values()) else []


def review(ctx, build):
    root = Path(ctx["root"]).resolve()
    manifest,views,digest = _capture(root,build)
    out = Path(ctx["output_dir"])/("visual_review_"+str(time.time_ns()))
    out.mkdir(parents=True)
    simulated = [view["path"] for view in views]
    targets = {WORKSPACE:"工作区中的目标区域"}
    targets.update({obj["id"]:obj.get("label") or obj["id"] for obj in manifest["objects"]})
    intent = _task_intent(ctx,root,manifest,build["task_spec_hash"])
    ctx["emit"]("task.progress",{"message":"对照示教与仿真中的操作物体、起始摆放和目标位置"})
    instruction = (
        "Review only the parts of this rendered scene needed to reproduce the recorded manipulation. Identify each workpiece "
        "actually manipulated, its starting pose and support, and the intended destination or functional end position using the "
        "supplied task intent and recorded views. For each workpiece include one check of each role: manipulated (same id as target), "
        "initial_placement, and target_placement. For a surface or region with no separate fixture use __workspace__ as its target "
        "and describe the region relative to the workpiece or an identifiable reference. An in-place task can use the same site. "
        "Compare the starting pose with early frames. Use later frames to identify the destination when needed; the rendered "
        "scene is an initial state, so compare the destination site's position and support geometry, even if it is empty. "
        "Do not require a demonstrated final grasp release, precise seating depth, hidden latch or measured contact to check the "
        "scene. Check placement, orientation and open or supporting faces only insofar as they affect this manipulation or fit. "
        "Use functional_fixture only for another visible component with a specific role in this operation, such as an obstacle in "
        "the actual transfer path. Skip incidental wires, connector details, gripper appearance, colors, and background details "
        "unless they affect the observed operation. Incidental details can be omitted or marked context_only. "
        "Distinguish camera roll, perspective, cropping and occlusion from task-relevant differences. Use misplaced for a wrong "
        "location or support. Use uncertain only when the "
        "task-relevant relation cannot be inferred, and indistinguishable only for a visible symmetric workpiece if that axis does "
        "not affect the task. Do not assume objects are flipped. Report differences only from comparable features on both sides. "
        "Pair left_wrist with the calibrated left render and right_wrist with right when present. Other views may corroborate a "
        "feature even without a matched render. Use a center-camera correspondence only when supported by metadata or evidence. "
        "Check spacing along and across the working surface, not just order. Never conclude a wrong distance from different "
        "camera poses, focal lengths or frames of a moving workpiece. Prefer stationary support landmarks in the initial "
        "frames and matching cameras. Treat unmatched views as qualitative. "
        "Cite actual recorded and simulated files. In recorded_cue and simulated_cue state the visible location or support cue "
        "in each image, or use null for an unavailable cue. Explain the task-relevant comparison in cue, in Chinese. "
        "Task intent: "+json.dumps(intent,ensure_ascii=False)
        +"\nNamed scene objects: "+json.dumps(targets,ensure_ascii=False)
        +"\nCandidate renders: "+json.dumps(views,ensure_ascii=False)
    )
    motion=(ctx['dependencies'].get('data_audit') or {}).get('scene_motion')
    model,parsed,refs = request_with_evidence(ctx | {"output_dir":out,"stage":"scene_visual_review"},instruction,
        lambda files:_schema(files,simulated,targets),comparison_images=[root/ref for ref in simulated],
        frame_selection='scene_motion' if motion else 'task_phases')
    recorded = selected_frame_evidence(ctx["dependencies"],refs)
    checks = _checks(parsed,recorded,views,targets) if model["status"]=="succeeded" else []
    if not checks:
        checks = [{"id":"task_placement:evidence","passed":False,
                   "details":{"assessment":"uncertain","cue":model.get("reason") or model.get("error") or "缺少操作物体、起始摆放或目标位置的对照证据"}}]
    states = {check["details"]["assessment"] for check in checks}
    status = ("orientation_mismatch" if states.intersection(DIFFERENCES) else
              "orientation_unverified" if "uncertain" in states else "orientation_verified")
    alignment={}
    if spec_ref:=manifest.get('spec_snapshot_path'):
        spec=load_snapshot(root,spec_ref,build['task_spec_hash'])
        table=spec['scene'].get('table') or {}
        samples=(spec.get('scene_approximation') or {}).get('placement_alignment') or {}
        if table.get('size_m') and table.get('position_m'):
            alignment=compare(table,samples,manifest.get('settled_poses') or {})
            alignment['object_labels']=targets
            for item in alignment['positions']:
                item['label']=targets.get(item['object_id'])
            if alignment['status']=='mismatch':
                status='orientation_mismatch'
                for item in alignment['positions']:
                    if not item['passed']:
                        key=item['object_id']
                        checks.append({'id':'workspace_spacing:'+key,'passed':False,
                                       'details':item|{'assessment':'misplaced','cue':
                                       f'{targets.get(key,key)}在工作面上的落点与多视角定位不符'}})
                for item in alignment['spacings']:
                    if not item['passed']:
                        checks.append({'id':'workspace_spacing:'+':'.join(item['objects']),'passed':False,
                                       'details':item|{'assessment':'misplaced','cue':'两个任务物体的相对间距或方向偏离示教定位'}})
    expected = {item["path"]:item.get("sha256") for item in (ctx["dependencies"].get("data_audit") or {}).get("sample_frames",[])}
    for item in recorded:
        item["sha256"] = hashlib.sha256(_evidence_file(root,item["path"]).read_bytes()).hexdigest()
        if expected.get(item["path"]) not in (None,item["sha256"]):
            raise ValueError("示教照片与数据审计记录的哈希不一致")
    report = {"scope":SCOPE,"status":status,"checks":checks,"task_intent":intent,
              "model_call_status":model["status"],"recorded_frames":recorded,"simulated_views":views,
              "placement_alignment":alignment,"task_spec_hash":build["task_spec_hash"],"scene_manifest":build["manifest_path"],"scene_manifest_sha256":digest}
    path = out/"visual_comparison.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    return report | {"report_path":path.relative_to(root).as_posix()}


def reuse_review(ctx, build, ref):
    if not ref:
        return None
    root = Path(ctx["root"]).resolve()
    try:
        report = json.loads(_evidence_file(root,ref).read_bytes())
        if (report.get("scope")!=SCOPE or report["task_spec_hash"]!=build["task_spec_hash"] or
                report["scene_manifest"]!=build["manifest_path"] or
                report["scene_manifest_sha256"]!=hashlib.sha256(_evidence_file(root,build["manifest_path"]).read_bytes()).hexdigest()):
            return None
        for item in [*report["recorded_frames"],*report["simulated_views"]]:
            if hashlib.sha256(_evidence_file(root,item["path"]).read_bytes()).hexdigest()!=item["sha256"]:
                return None
    except (OSError,KeyError,TypeError,ValueError):
        return None
    return report | {"report_path":ref}


def apply_review(physical, visual):
    alignment=visual.get('placement_alignment') or {}
    result = physical | {"visual_comparison_status":visual["status"],"visual_comparison_path":visual["report_path"],
                         'placement_alignment_status':alignment.get('status','unmeasured')}
    if visual["status"]=="orientation_verified":
        return result
    mismatches = visual["status"]=="orientation_mismatch"
    return result | {"status":"blocked",
                     "error":"操作物体或其摆放与示教有可见差异" if mismatches else "操作物体、起始摆放或目标位置仍需确认",
                     "checks":[*physical.get("checks",[]),*visual["checks"]]}
