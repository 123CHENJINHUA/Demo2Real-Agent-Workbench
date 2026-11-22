"""Build, compare and render successive preview revisions for recorded demonstrations."""
import hashlib
import json
from pathlib import Path
import time

from application.agent_activity import handoff
from application.collision_agent import review as review_collision
from application.scene_layout import prepare
from application.scene_visual import previous_scene, required as visual_required, review as review_visual, reuse_review
from modules.rig_binding import discover
from modules.scene_building.layout import physical_layout_key
from modules.scene_building.motion_anchors import apply_motion_anchors
from modules.scene_building.rig_preview import registered_preview
from modules.scene_building.service import run as build_scene
from modules.scene_validation.service import run as validate_scene
from modules.task_specs import load_snapshot

REFINEMENT_BUDGET = 2


def _save(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
    temporary.replace(path)


def _reuse_layout(root, entry):
    path = entry.get("corrections") if entry else None
    if not path:
        return None
    report = json.loads((root/path).read_text())
    spec = load_snapshot(root,report["preview_spec_path"],report["preview_spec_hash"])
    return {"status":"succeeded","spec_path":report["preview_spec_path"],"spec_hash":spec["content_hash"],
            "spec_revision":spec["revision"],"report_path":path}


def _rank(entry):
    visual=entry.get('visual_comparison_status')
    spatial=entry.get('placement_alignment_status')
    return (entry['status']=='succeeded',
            {None:2,'orientation_verified':2,'orientation_unverified':1,'orientation_mismatch':0}.get(visual,0),
            spatial=='checked',entry.get('placement_check_count',0),-entry.get('placement_worst_residual',0))


def build_preview(ctx):
    root = Path(ctx["root"]).resolve()
    deps, inputs = ctx["dependencies"], ctx["input"]
    ref = (deps.get("scene_layout") or {}).get("spec_path") or inputs.get("spec_path") or (deps.get("task_spec") or {}).get("spec_path")
    if not ref:
        return build_scene(ctx)
    try:
        spec = load_snapshot(root, ref)
    except (OSError, ValueError):
        return build_scene(ctx)
    preview=spec
    if not spec['robot'].get('arms'):
        audit = deps.get("data_audit") or {}
        rig = spec["robot"].get("recorded_rig")
        if not rig:
            metadata = (deps.get("demo_import") or {}).get("metadata") or audit.get("metadata", {})
            rig = discover(root, metadata, inputs.get("calibration_bundle"))
        audit_ref = audit.get("report_path")
        initial = audit.get("initial_robot_pose")
        audit_evidence = None
        if initial and audit_ref:
            path = (root/audit_ref).resolve()
            path.relative_to(root)
            raw = path.read_bytes()
            if json.loads(raw).get("initial_robot_pose") != initial:
                raise ValueError("数据审查报告中的初始关节记录发生变化")
            audit_evidence = (audit_ref, hashlib.sha256(raw).hexdigest())
        preview = registered_preview(root, spec, rig, initial, audit_evidence) or spec
        if preview is not spec:
            ctx['emit']('task.progress',{'message':'根据桌面和双臂基座关系估计 Nero 在 Isaac 场景中的位置'})
    preview=apply_motion_anchors(preview)
    out=Path(ctx['output_dir'])
    out.mkdir(parents=True,exist_ok=True)
    layout=deps.get('scene_layout') or {}
    if preview is not spec:
        snapshot=out/('registered_'+preview['content_hash']+'.json')
        _save(snapshot,preview)
        layout=layout | {'spec_path':snapshot.relative_to(root).as_posix(),'spec_hash':preview['content_hash']}
        ctx=ctx | {'dependencies':deps | {'scene_layout':layout}}

    correction=None if ctx.get('collision_repair_pass') else review_collision(ctx,preview,
                                     comparison_images=ctx.get('collision_comparison_images',()))
    if not correction:
        return build_scene(ctx)
    tracking={'collision_review_path':correction['report_path'],'collision_review_status':correction['status']}
    if correction['status']=='candidate':
        revised=layout | {key:correction[key] for key in ('spec_path','spec_hash')}
        candidate=build_scene(ctx | {'dependencies':deps | {'scene_layout':revised}})
        tracking['collision_candidate_status']=candidate['status']
        if candidate['status']=='succeeded':
            validation_dir=(root/correction['report_path']).parent/'validation'
            with handoff(ctx,'碰撞几何 Agent','场景校验 Agent','独立检查碰撞候选的局部物理',
                         evidence_path=candidate['manifest_path']) as finish:
                checked=validate_scene(ctx | {'output_dir':validation_dir,'dependencies':{'scene_build':candidate}})
                finish(checked)
            tracking.update(collision_candidate_manifest=candidate['manifest_path'],
                            collision_candidate_status=checked['status'],collision_validation_path=checked.get('report_path'))
            if checked['status']=='succeeded':
                return candidate | tracking
    return build_scene(ctx) | tracking


def run(ctx):
    inputs, deps = ctx["input"],ctx["dependencies"]
    needs_visual = visual_required(ctx)
    gate = {"requires_visual_review":True} if needs_visual else {}
    auto = bool(inputs.get("source_path") and not inputs.get("diagnostic_only") and deps.get("task_spec"))
    if not (auto or inputs.get("iterate_scene")):
        return build_preview(ctx) | gate

    root = Path(ctx["root"]).resolve()
    output = Path(ctx["output_dir"])
    output.mkdir(parents=True,exist_ok=True)
    baseline = inputs.get("previous_scene_build")
    start = int(inputs.get("previous_scene_round",0))
    if baseline is None:
        ctx["emit"]("task.progress", {"message":"构建可供对照的初版场景","completed":0,"total":REFINEMENT_BUDGET,"unit":"轮"})
        baseline = build_preview(ctx)
        if baseline["status"]!="succeeded":
            return baseline | gate
    report_path = output/"refinement_history.json"
    old_report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    prior_entries = old_report.get("rounds",[]) if old_report.get("baseline_spec_hash")==baseline["task_spec_hash"] else []
    rounds = []
    report = {"scope":"recorded_multi_view_scene_comparison","rounds":rounds,
              "baseline_render":baseline["preview_path"],"baseline_spec_hash":baseline["task_spec_hash"],
              "refinement_budget":REFINEMENT_BUDGET}
    if old_report:
        archive = output/("refinement_history_attempt_"+str(time.time_ns())+".json")
        _save(archive,old_report)
        report["previous_attempt_reports"] = [*old_report.get("previous_attempt_reports",[]),archive.relative_to(root).as_posix()]
    previous = baseline
    visual_path = None
    previous_id = inputs.get("previous_scene_run_id") or ctx["run_id"]
    seen=set()
    for index in range(REFINEMENT_BUDGET):
        ctx["check_cancel"]()
        number = start+index+1
        # Each attempt keeps its artifacts intact. An explicit retry gets a fresh directory.
        prior = next((item for item in prior_entries if item["round"]==number and item["previous_render"]==previous["preview_path"]),None)
        prior_visual = reuse_review(ctx,prior["build"],prior.get("visual_comparison_path")) if (
            prior and prior.get("build") and needs_visual and not ctx.get("user_replies")) else None
        if prior and prior.get("build") and prior["status"]=="succeeded" and (
                not needs_visual or (prior_visual and prior_visual["status"]=="orientation_verified")):
            rounds.append(prior)
            previous,previous_id = prior["build"],ctx["run_id"]
            visual_path = prior.get("visual_comparison_path")
            if prior.get('layout_key'):
                seen.add(prior['layout_key'])
            continue
        path = output/("refinement_"+str(number)+"_"+str(time.time_ns()))
        path.mkdir()
        entry = {"round":number,"previous_render":previous["preview_path"],"status":"running"}
        rounds.append(entry)
        _save(report_path,report)
        ctx["emit"]("task.progress", {"message":f"正在对照上一版渲染 · 第 {number} 轮",
                                      "completed":index,"total":REFINEMENT_BUDGET,"unit":"轮"})
        binding = previous_scene(root,previous,run_id=previous_id,round_number=number)
        if visual_path:
            binding["visual_comparison_path"] = visual_path
        round_ctx = ctx | {"input":inputs | {"scene_refinement":binding},"output_dir":path}
        layout = (_reuse_layout(root,prior) if not visual_path and not ctx.get("user_replies") else None) or prepare(round_ctx)
        if layout["status"]!="succeeded":
            entry.update(status=layout["status"],error=layout.get("error") or layout.get("reason"))
            _save(report_path,report)
            if any(row.get('build') for row in rounds):
                break
            return layout | gate | {"report_path":report_path.relative_to(root).as_posix()}
        entry["corrections"]=layout["report_path"]
        if layout.get('spec_path'):
            key=physical_layout_key(load_snapshot(root,layout['spec_path'],layout.get('spec_hash')))
            entry['layout_key']=key
            if key in seen:
                entry.update(status='unchanged',reason='修正结果与已验证或已尝试的场景重复')
                break
            seen.add(key)
        ctx["emit"]("task.progress", {"message":f"重新构建修正后的场景 · 第 {number} 轮"})
        built = build_preview(ctx | {"dependencies":deps | {"scene_layout":layout},
                                      "collision_comparison_images":[root/item["path"] for item in binding.get("views",[])]})
        if built["status"]!="succeeded":
            entry.update(status=built["status"],error=built.get("error") or built.get("reason"))
            _save(report_path,report)
            if any(row.get('build') for row in rounds):
                break
            return built | gate | {"report_path":report_path.relative_to(root).as_posix()}
        ctx["emit"]("task.progress", {"message":f"核对 USD 和局部物理 · 第 {number} 轮"})
        validation = validate_scene(ctx | {"dependencies":{"scene_build":built},"output_dir":path/"validation"})
        visual = review_visual(round_ctx,built) if needs_visual and validation["status"]=="succeeded" else None
        visual_path = visual["report_path"] if visual else None
        entry.update(render=built["preview_path"],spec_hash=built["task_spec_hash"],
                     scene=built["scene_path"],build=built,validation=validation.get("report_path"),status=validation["status"])
        if visual:
            alignment=visual.get('placement_alignment') or {}
            measured=[*alignment.get('positions',[]),*alignment.get('spacings',[])]
            entry.update(visual_comparison_path=visual_path,visual_comparison_status=visual["status"],
                         placement_alignment_status=alignment.get('status','unmeasured'),placement_check_count=len(measured),
                         placement_worst_residual=max((row['error']/row['tolerance'] for row in measured),default=0.))
        _save(report_path,report)
        if validation["status"]=="succeeded" and (not needs_visual or visual["status"]=="orientation_verified") and (
                _rank(entry)>=max((_rank(item) for item in rounds if item.get('build')),default=_rank(entry))):
            ctx["emit"]("scene.published",{"build":built,"validation_path":validation.get("report_path"),
                                          "visual_comparison_path":visual_path,"visual_comparison_status":visual["status"] if visual else None})
        previous,previous_id = built,ctx["run_id"]
    candidates=[item for item in rounds if item.get('build')]
    chosen=max(reversed(candidates),key=_rank) if candidates else None
    report['selected_round']=chosen['round'] if chosen else start
    _save(report_path,report)
    return (chosen['build'] if chosen else baseline) | gate | {'refinement_rounds':rounds,
             'selected_scene_round':report['selected_round'],'report_path':report_path.relative_to(root).as_posix(),
             'refinement_round':start+len(rounds)}
