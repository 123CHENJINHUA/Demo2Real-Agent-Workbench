"""Review task contact geometry separately from scene layout and physics validation."""

import hashlib
import json
from pathlib import Path
import time

import numpy as np

from application.agent_activity import handoff
from application.analysis_agents import request_with_evidence
from application.scene_visual import required
from modules.scene_building.repair import apply, mesh_evidence
from modules.scene_building.preview_details import box_components


def _schema(objects, refs):
    def record(fields):
        return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}

    xyz={'type':'array','minItems':3,'maxItems':3,'items':{'type':'number'}}
    box=record({'center_m':xyz,'size_m':xyz})
    edit=record({'object_id':{'type':'string','enum':list(objects)},
                 'collision_boxes':{'type':'array','minItems':1,'maxItems':8,'items':box},
                 'frame_ref':{'type':'string','enum':refs},'reason':{'type':'string'}})
    return record({'diagnosis':{'type':'string'},'edits':{'type':'array','maxItems':len(objects),'items':edit}})


def review(ctx, spec, *, checks=(), motion=None, comparison_images=(), targets=None, clearance=None):
    approx=spec.get('scene_approximation')
    audit=ctx['dependencies'].get('data_audit') or {}
    if not approx or not required(ctx) or not (audit.get('sample_frames') or audit.get('videos')):
        return None
    def editable(obj):
        return ((obj['geometry_type']=='mesh' and obj.get('asset_path')
                 and obj.get('collision',{}).get('type') in ('convex_hull','mesh','preview_compound'))
                or (obj['geometry_type']=='box' and (obj.get('preview_shape') not in (None,'box')
                                                    or (checks and obj['id'] in approx.get('proxy_objects',[])))))

    objects=[obj for obj in spec['objects'] if editable(obj) and (targets is None or obj['id'] in targets)]
    if not objects:
        return None
    root=Path(ctx['root']).resolve()
    out=Path(ctx['output_dir'])/f'collision_review_{time.time_ns()}'
    out.mkdir(parents=True)
    report_path=(out/'collision_review.json').relative_to(root).as_posix()
    report={'source_spec_hash':spec['content_hash'],'checks':checks,'status':'retained'}
    revised=None
    with handoff(ctx,'场景 Agent','碰撞几何 Agent','根据示教画面、动作接触和参考模型审查碰撞几何',
                 evidence_path=(ctx['dependencies'].get('scene_layout') or {}).get('report_path')) as finish:
        ctx['emit']('task.progress',{'message':'碰撞几何 Agent 正在核对工件接触面、开口与承托几何'})
        measured={}
        for obj in objects:
            ctx['check_cancel']()
            try:
                if obj['geometry_type']=='mesh':
                    measured[obj['id']]=mesh_evidence(root,obj)
                else:
                    size=np.asarray(obj['size_m'])*obj['scale']
                    measured[obj['id']]={'source':'existing_visual_proxy',
                        'local_bounds_m':[(-size/2).tolist(),(size/2).tolist()],
                        'components':box_components(obj.get('preview_shape') or 'box',size.tolist(),[.3,.3,.3])}
            except (OSError, ValueError) as exc:
                report.setdefault('mesh_errors',{})[obj['id']]=str(exc)
        if measured:
            available={key for frame in audit.get('sample_frames',[]) for key in frame.get('event_ids',[])}
            events=[event for event in (audit.get('scene_motion') or {}).get('events',[]) if event['id'] in available]
            evidence={'objects':objects,'reference_geometry':measured,'object_roles':approx.get('object_roles'),
                      'motion_anchors':approx.get('motion_anchor_review') or approx.get('motion_anchors'),
                      'motion_events':events,'failed_checks':checks,'motion_feedback':motion,'fit_evidence':clearance}
            instruction=('You are the collision geometry specialist for this offline demonstration. Review the supplied reference '
                         'meshes against the task images, observed interaction and measured slices. Check only relevant contact, '
                         'grasp, support, and passage surfaces. Preserve observed openings, floors and load-bearing faces. '
                         'When a collision proxy needs correction, return one to eight axis-aligned boxes in the centered, scaled '
                         'object-local frame before world rotation. Do not fill free space used by the observed action. '
                         'The original mesh, object world pose, scale, mass and independent checks are fixed. An empty edit list '
                         'retains the current proxy when evidence does not justify changes. Cite a supplied recorded frame per edit. '
                         'Pause and gripper changes locate possible interactions. Explain what can be checked in simulation, in Chinese.\n'
                         +json.dumps(evidence,ensure_ascii=False))
            reply,proposal,refs=request_with_evidence(ctx | {'output_dir':out,'stage':'collision_geometry'},instruction,
                lambda selected:_schema(measured,selected),comparison_images=list(comparison_images),frame_selection='scene_motion')
            report.update(model_status=reply['status'],proposal=proposal)
            if reply['status']=='succeeded' and isinstance(proposal,dict) and proposal.get('edits'):
                try:
                    edits=[]
                    hashes={}
                    for edit in proposal['edits']:
                        ref=edit['frame_ref']
                        if edit['object_id'] not in measured or ref not in refs:
                            raise ValueError('碰撞修正缺少本任务的参考几何或图像')
                        hashes[ref]=hashlib.sha256((root/ref).read_bytes()).hexdigest()
                        edits.append({'object_id':edit['object_id'],'position_m':None,'rotation_xyz_deg':None,
                                      'collision_boxes':edit['collision_boxes']})
                    revised=apply(spec,{'edits':edits},measured,hashes)
                    snapshot=out/'collision_task_spec.json'
                    snapshot.write_text(json.dumps(revised,ensure_ascii=False,indent=2,allow_nan=False))
                    report.update(status='candidate',spec_path=snapshot.relative_to(root).as_posix(),
                                  spec_hash=revised['content_hash'])
                except (OSError,ValueError,TypeError,KeyError) as exc:
                    report.update(status='rejected',reason=str(exc))
            elif reply['status']!='succeeded':
                report.update(status='unavailable',reason=reply.get('reason') or reply.get('error'))
        if not measured:
            report.update(status='unavailable',reason='缺少可读的参考碰撞几何')
        if report['status']=='retained':
            summary='已审查参考模型，沿用当前碰撞几何'
        else:
            summary={'candidate':'已生成碰撞候选，等待独立仿真检查','rejected':'碰撞候选未通过几何复核',
                     'unavailable':'当前碰撞审查未取得模型结果'}[report['status']]
        (root/report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        finish({'status':'succeeded' if report['status'] in ('candidate','retained') else 'blocked'},
               summary=summary,report_path=report_path)
    return report | {'report_path':report_path}
