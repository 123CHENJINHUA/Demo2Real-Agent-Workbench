"""Require local checks and recorded orientation evidence for the chosen scene."""
import hashlib
import json
from pathlib import Path

from application.scene_repair import repair
from application.scene_visual import apply_review, required, review, reuse_review
from modules.scene_validation.service import run as validate_scene
from modules.scene_validation.settling import settling_evidence
from modules.task_specs import load_snapshot


def _artifact(root, reference):
    path=(root/reference).resolve()
    path.relative_to(root)
    return path


def _validation_record(root, reference, scene):
    seen=set()
    while reference:
        path=_artifact(root,reference)
        if path in seen:
            break
        seen.add(path)
        report=json.loads(path.read_text())
        if report.get('manifest_path') and 'checks' in report:
            return path.relative_to(root).as_posix(),report
        # Earlier repair results sometimes stored a history path as the validation path.
        reference=next((row['validation'] for row in reversed(report.get('attempts',[]))
                        if row.get('manifest_path')==scene and row.get('validation')),
                       (report.get('starting_scene') or {}).get('validation'))
    raise ValueError('未找到失败场景的独立验证报告')


def bind_failure(ctx, failure):
    """Bind recovery to the scene that produced the reported measurements."""
    reference=failure.get('validation_report_path') or failure.get('report_path')
    if not reference:
        return failure,ctx
    root=Path(ctx['root']).resolve()
    original=ctx['dependencies'].get('scene_build') or {}
    failed_build=failure.get('failed_scene_build') or {}
    expected=failure.get('manifest_path') or failed_build.get('manifest_path') or original.get('manifest_path')
    try:
        reference,validation=_validation_record(root,reference,expected)
        scene=validation['manifest_path']
        raw=_artifact(root,scene).read_bytes()
        manifest=json.loads(raw)
        digest=hashlib.sha256(raw).hexdigest()
        spec_hash=manifest['task_spec_hash']
        if ((expected and expected!=scene) or validation['task_spec_hash']!=spec_hash
                or failure.get('task_spec_hash',spec_hash)!=spec_hash
                or failed_build.get('manifest_path',scene)!=scene
                or failed_build.get('task_spec_hash',spec_hash)!=spec_hash
                or (validation.get('artifact_sha256') or {}).get(scene,digest)!=digest):
            raise ValueError('失败结果、独立报告和场景清单的版本不匹配')
        spec_path=manifest['spec_snapshot_path']
        spec=load_snapshot(root,spec_path,spec_hash)
        scene_path=manifest['scene_path']
        observed=settling_evidence(manifest,validation['checks'])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return failure | {'scene_binding_error':str(exc) or '场景证据路径无效'},ctx

    approximation=spec.get('scene_approximation') or {}
    source=failed_build or (original if original.get('manifest_path')==scene else {})
    build=source | {'manifest_path':scene,'task_spec_hash':spec_hash,'spec_path':spec_path,
           'scene_path':scene_path,'preview_path':manifest.get('preview_path'),
           'build_output':str(Path(scene).parent),'spec_revision':spec['revision'],
           'refinement_rounds':source.get('refinement_rounds',original.get('refinement_rounds',[])),
           'scene_approximation':bool(approximation),'source_task_spec_hash':approximation.get('source_task_spec_hash')}
    result=failure | {'failed_scene_build':build,'manifest_path':scene,'task_spec_hash':spec_hash,
                      'scene_manifest_sha256':digest,'validation_report_path':reference,
                      'settling_review':observed}
    return result,ctx | {'dependencies':ctx['dependencies'] | {'scene_build':build}}


def recovery_actions(ctx, failure):
    built=ctx['dependencies'].get('scene_build') or {}
    return {'repair_scene': repair_from_review} if (not failure.get('scene_binding_error')
                                                    and built.get('manifest_path') and built.get('spec_path')) else {}


def repair_from_review(ctx, failure, decision):
    validation=failure | {'report_path':failure.get('validation_report_path') or failure.get('report_path')}
    build=failure.get('failed_scene_build') or ctx['dependencies']['scene_build']
    result=repair(ctx,build,validation,supervisor_instruction=decision['instruction'])
    return result,ctx


def run(ctx):
    built = ctx["dependencies"].get("scene_build") or {}
    rounds = built.get("refinement_rounds") or []
    last = next((row for row in rounds if row.get('round')==built.get('selected_scene_round')),
                rounds[-1] if rounds else {})
    root = Path(ctx["root"]).resolve()
    retrying = bool(ctx.get("recovery_feedback"))
    local_report = None if retrying else last.get("validation")
    result = json.loads((root/local_report).read_text()) if local_report else validate_scene(ctx)
    if local_report and (result.get("task_spec_hash") not in (None,built["task_spec_hash"])
                         or result.get("manifest_path") not in (None,built.get("manifest_path"))):
        local_report = None
        result = validate_scene(ctx)
    if local_report:
        result["report_path"] = local_report
    if result["status"]!="succeeded":
        if built.get("scene_approximation") and built.get("spec_path"):
            return repair(ctx,built,result)
        return result
    if not required(ctx):
        return result

    cached = None if retrying or ctx.get("user_replies") else reuse_review(ctx,built,last.get("visual_comparison_path"))
    visual = cached or review(ctx,built)
    checked = apply_review(result,visual)
    if checked["status"]=="succeeded":
        if not cached or not (ctx.get("active_scene") or {}).get("visual_comparison_path")==visual["report_path"]:
            ctx["emit"]("scene.published",{"build":built,"validation_path":result.get("report_path"),
                                          "visual_comparison_path":visual["report_path"],"visual_comparison_status":visual["status"]})
        return checked

    if visual["status"]=="orientation_mismatch" and built.get("scene_approximation") and built.get("spec_path"):
        return repair(ctx,built,checked)
    return checked | {"report_path":visual["report_path"],"validation_report_path":result.get("report_path")}
