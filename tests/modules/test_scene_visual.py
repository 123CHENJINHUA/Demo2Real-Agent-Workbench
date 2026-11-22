import json

import pytest
from PIL import Image, ImageDraw

from application import scene_review, scene_visual
from application.stage_diagnosis import question_for_failure


@pytest.mark.parametrize("outcome,wrist,goal_assessment,paired_cues",[
    ("orientation_verified","left","consistent",True),
    ("orientation_mismatch","left","misplaced",True),
    ("orientation_unverified","right","consistent",True),
    ("orientation_unverified","left","misplaced",False),
    ("orientation_unverified","left",None,True),
])
def test_placement_binds_recorded_views_and_ignores_nonessential_objects(tmp_path,monkeypatch,outcome,wrist,goal_assessment,paired_cues):
    image = Image.new("RGB",(50,50),"white")
    ImageDraw.Draw(image).rectangle((8,8,38,13),fill="black")
    image.save(tmp_path/"left_wrist.jpg")
    rendered = image.rotate(180) if goal_assessment=="misplaced" else image
    rendered.save(tmp_path/"render.png")
    manifest = {"task_spec_hash":"fixture","objects":[
        {"id":"panel","label":"Panel"},{"id":"slot","label":"Holder"},{"id":"goal","label":"Goal slot"},
        {"id":"cables","label":"Loose cables"},{"id":"grippers","label":"Grippers"}],
                "cameras":[{"id":wrist,"rgb_path":"render.png","calibration_path":"fixture.json"}]}
    (tmp_path/"scene_manifest.json").write_text(json.dumps(manifest))
    build = {"manifest_path":"scene_manifest.json","task_spec_hash":"fixture"}
    audit = {"sample_frames":[{"path":"left_wrist.jpg","source_video":"videos/observation.images.left_wrist/demo.mp4"}]}
    proposal = {"proposal":{"task_summary":"Transfer the Panel from the Holder to the Goal slot.",
                            "objects":[{"name":"Panel","role":"Workpiece"},{"name":"Goal slot","role":"Destination"}]}}
    ctx = {"root":tmp_path,"output_dir":tmp_path/"review","input":{"source_path":"/fixture"},
           "dependencies":{"data_audit":audit,"task_analysis":proposal},"emit":lambda *_:None}

    def reviewer(ctx,instruction,schema,comparison_images,frame_selection):
        assert "even if it is empty" in instruction
        assert "Skip incidental wires" in instruction
        assert proposal["proposal"]["task_summary"] in instruction
        assert "Distinguish camera roll" in instruction
        assert comparison_images==[tmp_path/"render.png"]
        assert frame_selection=="task_phases"
        refs = ["left_wrist.jpg"]
        assert schema(refs)["properties"]["task_checks"]["minItems"]==3
        evidence = {"object_id":"panel","recorded_ref":refs[0],"simulated_ref":"render.png",
                    "cue":"核对相对于工作台边缘的位置","recorded_cue":"工作台右侧","simulated_cue":"工作台右侧"}
        rows = [
            evidence | {"role":"manipulated","target":"panel","assessment":"consistent"},
            evidence | {"role":"initial_placement","target":"slot","assessment":"consistent"},
            evidence | {"role":"context_only","target":"cables","assessment":"uncertain","recorded_cue":None},
            evidence | {"role":"context_only","target":"grippers","assessment":"misoriented"},
        ]
        if goal_assessment:
            simulated_cue = "工作台左侧" if goal_assessment=="misplaced" else "工作台右侧"
            rows.append(evidence | {"role":"target_placement","target":"goal","assessment":goal_assessment,
                                    "simulated_cue":simulated_cue if paired_cues else None})
        return {"status":"succeeded"},{"task_checks":rows},refs

    monkeypatch.setattr(scene_visual,"request_with_evidence",reviewer)
    result = scene_visual.review(ctx,build)
    assert result["status"]==outcome
    if outcome=="orientation_verified":
        assert [row["details"]["target"] for row in result["checks"]]==["panel","slot","goal"]
    physical = {"status":"succeeded","report_path":"local.json"}
    checked = scene_visual.apply_review(physical,result)
    assert (checked["status"]=="succeeded")== (outcome=="orientation_verified")
    assert scene_visual.reuse_review(ctx,build,result["report_path"])==result
    (tmp_path/"left_wrist.jpg").write_bytes(b"changed")
    assert scene_visual.reuse_review(ctx,build,result["report_path"]) is None


@pytest.mark.parametrize("visual_status",["orientation_mismatch","orientation_unverified"])
def test_local_stability_does_not_bypass_visual_failure(tmp_path,monkeypatch,visual_status):
    build = {"spec_path":"fixture.json","scene_approximation":True}
    ctx = {"root":tmp_path,"input":{"source_path":"/fixture"},"dependencies":{"scene_build":build}}
    physical = {"status":"succeeded","report_path":"local.json"}
    check = {"id":"visual_orientation:slot","passed":False,"details":{"cue":"槽口方向"}}
    monkeypatch.setattr(scene_review,"validate_scene",lambda ctx:physical)
    monkeypatch.setattr(scene_review,"review",lambda *args:{
        "status":visual_status,"report_path":"orientation.json","checks":[check]})
    attempted = []
    monkeypatch.setattr(scene_review,"repair",lambda ctx,built,checked:attempted.append(checked) or checked)
    result = scene_review.run(ctx)
    assert result["status"]=="blocked" and result["checks"]==[check]
    assert bool(attempted)==(visual_status=="orientation_mismatch")
    question = question_for_failure("scene_validation",result | {
        "diagnosis":{"decision":"needs_input","next_action":"请指出哪个物体翻转了。"}})
    assert question["report_path"]=="orientation.json" and "翻转" not in question["prompt"]
    assert ("可见差异" in question["context"])==(visual_status=="orientation_mismatch")


@pytest.mark.parametrize('retrying',[False,True])
def test_scene_review_revalidates_when_cache_uses_another_build_or_retry(tmp_path,monkeypatch,retrying):
    manifest='candidate.json'
    cached={'status':'succeeded','task_spec_hash':'same_snapshot',
            'manifest_path':manifest if retrying else 'old_build.json'}
    (tmp_path/'cached.json').write_text(json.dumps(cached))
    built={'task_spec_hash':'same_snapshot','manifest_path':manifest,'refinement_rounds':[{'validation':'cached.json'}]}
    ctx={'root':tmp_path,'input':{},'dependencies':{'scene_build':built}}
    if retrying:
        ctx['recovery_feedback']={'instruction':'重新做独立静置检查'}
    measured={'status':'failed','manifest_path':manifest,'checks':[]}
    monkeypatch.setattr(scene_review,'validate_scene',lambda working:measured)
    assert scene_review.run(ctx)==measured
