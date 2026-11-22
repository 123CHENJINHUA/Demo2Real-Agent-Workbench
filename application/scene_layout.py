"""Use recorded views to prepare a derived scene-preview snapshot."""
import hashlib
import json
from pathlib import Path

from application.analysis_agents import request_with_evidence, selected_frame_evidence
from modules.scene_building.layout import layout_schema, needs_layout, preview_snapshot
from modules.scene_building.motion_anchors import observed_anchors
from contracts.tasks import canonical_hash
from modules.task_specs import load_snapshot


def _initial_frames(dependencies):
    audit=dependencies.get('data_audit') or {}
    first={}
    for sample in audit.get('sample_frames',[]):
        video=sample.get('source_video')
        time=sample.get('requested_timestamp_seconds')
        if not video or not isinstance(time,(int,float)):
            continue
        if video not in first or time<first[video][0]:
            first[video]=(time,sample['path'])
    return {path:video for video,(_,path) in first.items()}


def _previous_views(root, refinement):
    images = []
    for item in refinement["views"]:
        path = root/item["path"]
        if not path.is_relative_to(root) or path.resolve()!=path or not path.is_file() or path.stat().st_size>6_000_000:
            raise ValueError("上一轮渲染图像不可用")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("上一轮渲染图像发生变化")
        images.append(path)
    return images


def _submitted_meshes(ctx, root, base):
    if base.get("profile_id"):
        return {}
    audit = ctx["dependencies"].get("geometry") or {}
    if not audit and ctx["input"].get("source_geometry_report_path"):
        path = root/ctx["input"]["source_geometry_report_path"]
        if path.resolve()!=path or not path.is_relative_to(root) or not path.is_file():
            raise ValueError("资产审查报告不可用")
        audit = json.loads(path.read_text())
    if audit.get("asset_selection_scope")!="explicit_task_assets":
        return {}
    bound = {obj.get("asset_path") for obj in base["objects"]}
    candidates = {asset["path"]:asset for asset in audit.get("assets",[]) if asset["path"] not in bound
                  and Path(asset["path"]).suffix.lower() in (".obj",".stl") and asset.get("status")=="audited"
                  and asset.get("bytes",0)<=256*1024**2 and any(value>0 for value in asset.get("extent_in_asset_units",[]))}
    return candidates


def prepare(ctx):
    root = Path(ctx["root"]).resolve()
    out = Path(ctx["output_dir"])
    source = ctx["dependencies"].get("task_spec") or ctx["input"]
    path = source.get("spec_path")
    if not path:
        return {"status": "needs_configuration", "reason": "场景布局需要已保存的任务定义"}
    base = load_snapshot(root, path, source.get("spec_hash"))
    identity = {"spec_path": path, "spec_hash": base["content_hash"], "spec_revision": base["revision"],
                "source_task_spec_hash": base["content_hash"]}
    refinement = ctx["input"].get("scene_refinement")
    previous_build = ctx["input"].get("previous_scene_build")
    if previous_build and not refinement:
        return {"status":"succeeded", **identity, "spec_path":previous_build["spec_path"],
                "spec_hash":previous_build["task_spec_hash"],"scene_approximation":True}
    if not needs_layout(base) and not refinement:
        return {"status": "succeeded", **identity}
    ids = [obj["id"] for obj in base["objects"]]
    if len(ids) > 32:
        return {"status": "blocked", "reason": "近似预览最多支持 32 个对象"}
    assets = _submitted_meshes(ctx,root,base)
    locked = {}
    prior = _previous_views(root,refinement) if refinement else []
    review_ref = None
    initial_frames = _initial_frames(ctx['dependencies']) if refinement else {}
    previous_alignment = {}
    audit=ctx['dependencies'].get('data_audit') or {}
    motion=audit.get('scene_motion') or {}
    timed_frames={row['path']:row for row in audit.get('sample_frames',[]) if row.get('event_ids')}
    event_ids={event for frame in timed_frames.values() for event in frame['event_ids']}
    timed_events=[event for event in motion.get('events',[]) if event['id'] in event_ids]
    instruction = (
        "Estimate a provisional support scene for offline visual preview. Use the earliest RGB samples to infer the initial arrangement; "
        "use later samples to identify the workpiece and the intended destination. Prioritize the manipulated object, its initial "
        "support, the target area and the fixtures that affect the motion. Approximate incidental details. Compare front and "
        "side especially for width, depth, vertical extent, "
        "and left/right placement. The table is centered at XY=(0,0), length X runs left to right from the front view, "
        "width Y is front to back. For every object give approximate box lengths in its local axes, tabletop XY center in meters, "
        "yaw in degrees about the up axis, and representative RGB color in [0,1]. Make the local Z dimension the height "
        "in the observed orientation (vertical panels are tall and thin along a horizontal axis). "
        "Use conservative visually plausible estimates for unmeasured dimensions and keep loose boxes separated on the table. "
        "Treat detected hands or cables as small workspace visualization proxies if listed; do not invent robot calibration, latch detail, "
        "measured scale, metric depth or a verified grasp. Reference exact supplied image paths. "
        "Object identities and existing definitions: " + json.dumps(base["objects"],ensure_ascii=False)
    )
    if timed_events:
        instruction += ('\nUse the recorded tool pose and event frames to constrain the active objects and destination. '
                        'Base coordinates are per arm. Pauses and opening changes are contact candidates. '
                        'Use motion_anchors only when the attached frame tagged with the same event shows the interaction. '
                        'Link an initial_contact only when the workpiece or touched fixture is still in its initial pose. '
                        'Link target_contact to the receiving surface or workpiece at the destination. '
                        'If identifiable, give the point on that object corresponding to the tool reference in centered, scaled '
                        'object-local meters. Use null when the offset is not identifiable. Choose only observed phases for this task. '
                        'Events: '+json.dumps({'reference_point':motion['reference_point'],'events':timed_events},ensure_ascii=False))
    if refinement:
        previous = load_snapshot(root,refinement["spec_path"],refinement["spec_hash"])
        previous_alignment=previous.get('scene_approximation',{}).get('placement_alignment',{})
        locked = {obj["id"]:{"asset_path":obj["asset_path"],"scale":obj["scale"]}
                  for obj in previous["objects"] if obj.get("asset_path") in assets}
        instruction += (
            "\nThis is visual correction round "+str(refinement["round"])+". Compare earlier simulated renders to initial recorded "
            "front, side and wrist views. Record task-relevant layout differences, proposed changes and exact evidence paths. "
            "Use the prior scene's dimensions and poses as a starting point and preserve observations that match. "
            "Choose shapes from the observed objects. Use box for a simple solid, channel for an open-top channel, framed_panel "
            "for a thin framed object and cable_bundle for loose leads. Choose workbench_rail only for a visible shared alignment rail. "
            "For any object resting on another object, give the actual support_id and use on_top. Use inside for an observed "
            "seating in an opening of a channel or supplied mesh. Use null for support_id and support_relation "
            "when directly on the table or when no other support is evidenced. "
            "Use every relevant view, including any extra fixed camera. Check end-to-end and across-surface spacing, not merely the order of objects. "
            "Use known model sizes and observed proportions before estimating dimensions. Never claim calibrated metric or contact validation. "
            "Prior rendered views (see calibration flags and camera IDs): "+json.dumps(refinement["views"],ensure_ascii=False)+
            "\nPrior layout: "+json.dumps({"table":previous["scene"]["table"],"objects":previous["objects"]},ensure_ascii=False)
        )
        if review_ref := refinement.get("visual_comparison_path"):
            path_to_review = root/review_ref
            if not path_to_review.is_relative_to(root) or path_to_review.resolve()!=path_to_review:
                raise ValueError("方向对照报告路径无效")
            review = json.loads(path_to_review.read_text())
            if review["task_spec_hash"]!=refinement["spec_hash"]:
                raise ValueError("方向对照报告与上一轮场景不匹配")
            instruction += ("\nIndependent spatial review for the last render. Separate camera/framing errors from real pose or spacing differences. "
                            "Do not move or resize an object solely to match an unmatched camera's perspective. Findings: "
                            +json.dumps(review["checks"],ensure_ascii=False))
    if initial_frames:
        instruction += (
            '\nProvide planar_views only for initial recorded frames with all four corners of the same rectangular working surface visible. '
            'Use normalized image coordinates (left/top 0, right/bottom 1) for corners in this semantic order in the scene approach: '
            '[min X,min Y], [max X,min Y], [max X,max Y], [min X,max Y]. Identify the same physical corners across views, not the '
            'image rectangle. For task-relevant objects directly on that plane, mark the center of their actual contact footprint, '
            'never the image center of the visible body or the top face. Leave out occluded points, objects on other supports and '
            'views of non-rectangular, cut-off or indistinguishable surfaces. Return an empty list when evidence is insufficient. '
            'For a cropped work surface, optionally use line_views for two visible task-relevant object footprint centers on the '
            'same physical line. Select a bound mesh object from that pair with a clearly visible full native-axis span on the '
            'same line (reference_axis is its local X=0, Y=1, Z=2). Give the visible endpoints of that full span in reference_uv, '
            'a separate physically parallel line in parallel_uv (e.g. the opposite edge of a straight rail), and the actual '
            'coplanar footprint centers in centers_uv. Use the same two objects and exact reference axis from two separate cameras. '
            'Do not use partial spans, a plate top instead of its mounted footprint, or estimates for cropped/occluded centers. '
            'Use an empty line_views list if these conditions are not visible. '
            'Use these first-frame paths only: '+json.dumps(initial_frames,ensure_ascii=False))
    if assets:
        instruction += ("\nBind submitted meshes to identified objects when the model name, shape or proportions match. Use each "
                        "mesh at most once. Leave extra and ambiguous meshes unbound. Set asset_ref to null for objects "
                        "without a matching mesh. Do not replace locked bindings. For a bound mesh use mesh_roll_pitch_deg "
                        "for roll and pitch of the native axes before yaw, checked against visible top, bottom and opening. "
                        "Use [0,0] for proxy objects or meshes whose native upright orientation matches the recording. "
                        "Submitted assets and their native bounding extents: "+json.dumps(
                            {name:item["extent_in_asset_units"] for name,item in assets.items()},ensure_ascii=False)
                        +"\nLocked bindings: "+json.dumps(locked,ensure_ascii=False))
    result, proposal, refs = request_with_evidence(ctx, instruction,
        lambda evidence: layout_schema(ids,evidence,refinement=bool(refinement),asset_paths=assets,
                                       initial_refs=[ref for ref in evidence if ref in initial_frames],
                                       motion_ids=sorted({event for ref in evidence if ref in timed_frames
                                                          for event in timed_frames[ref]['event_ids']})),
        comparison_images=prior,frame_selection="scene_motion" if timed_events else "task_phases" if refinement else None)
    if result["status"] != "succeeded":
        return result
    if refinement and not proposal.get("findings"):
        return {"status":"blocked","reason":"场景修正未说明可观察差异"}
    lineage = {key:refinement[key] for key in ("round","run_id","spec_hash")} if refinement else None
    if review_ref:
        lineage["visual_comparison_path"] = review_ref
    spec = preview_snapshot(root,base,proposal,refs,refinement=lineage,
                            prior_refs=[path.relative_to(root).as_posix() for path in prior],assets=assets,locked=locked,
                            initial_frames={ref:initial_frames[ref] for ref in refs if ref in initial_frames},
                            prior_alignment=previous_alignment)
    if timed_events and (anchors:=observed_anchors(spec,proposal,motion,{ref:timed_frames[ref] for ref in refs if ref in timed_frames})):
        for anchor in anchors:
            ref=anchor['frame_ref']
            if spec['dependency_hashes']['evidence'][ref]!=timed_frames[ref]['sha256']:
                raise ValueError('动作采样图像与数据审计记录不匹配')
        spec['scene_approximation']['motion_anchors']=anchors
        spec['dependency_hashes']['evidence'].update((audit.get('initial_robot_pose') or {}).get('source_data_hashes',{}))
        spec['content_hash']=canonical_hash(spec)
    snapshot = out/("preview_"+spec["content_hash"]+".json")
    report_path = out/"scene_layout_report.json"
    report = spec["scene_approximation"] | {"source_spec_path":path,
        "preview_spec_hash":spec["content_hash"],"preview_spec_path":snapshot.relative_to(root).as_posix(),
        "views":selected_frame_evidence(ctx["dependencies"],refs), "model":result.get("model"),
        "requested_model":result.get("requested_model"),"requested_reasoning_effort":result.get("requested_reasoning_effort"),
        "layout_estimates":proposal}
    if refinement:
        report["comparison_views"] = refinement["views"]
    report["submitted_model_bindings"] = {obj["id"]:obj["asset_path"] for obj in spec["objects"] if obj.get("asset_path")}
    report["unbound_model_paths"] = sorted(set(assets)-set(report["submitted_model_bindings"].values()))
    for target, content in ((snapshot,spec),(report_path,report)):
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(content,ensure_ascii=False,indent=2,allow_nan=False))
        temporary.replace(target)
    ctx["emit"]("scene.layout_prepared", {"proxy_objects":len(report["proxy_objects"]),
        "recorded_views":len({view.get("camera") for view in report["views"] if view.get("camera")}),
        "scope":report["scope"]})
    return {"status":"succeeded", **identity, "spec_path":report["preview_spec_path"], "spec_hash":spec["content_hash"],
            "scene_approximation":True, "report_path":report_path.relative_to(root).as_posix()}
