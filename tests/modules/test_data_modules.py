import json
import hashlib
from pathlib import Path
import shutil
import struct
import zipfile
import pytest
import yaml
from modules.demo_import import run as import_demo, audit
from modules.experience import search, record_incident, list_incidents, run as experience_run
from modules.geometry import run as audit_geometry
from modules.task_analysis import run as task_analysis, validate_proposal

PROJECT = Path(__file__).resolve().parents[2]

@pytest.fixture
def project(tmp_path):
    root = tmp_path / "workbench"
    root.mkdir()
    knowledge = PROJECT / "knowledge"
    # tests/modules -> project (parents[2])
    shutil.copytree(knowledge, root / "knowledge")
    (root / "config").mkdir()
    (root / "config/environments.yaml").write_text("environments: {}\n")
    return root


def context(root, name, inputs):
    return {"root": root, "run_id": "test", "output_dir": root / "runs/test" / name, "input": inputs, "emit": lambda *a: None, "check_cancel": lambda: None}


def test_import_independent_integrity_and_npz_without_pickle(project, tmp_path):
    source = tmp_path / "demo"
    (source / "meta").mkdir(parents=True)
    (source / "meta/info.json").write_text(json.dumps({"fps": 30, "total_frames": 15}))
    header = repr({"descr": "<f8", "fortran_order": False, "shape": (15, 7)}).encode("latin1") + b"\n"
    with zipfile.ZipFile(source / "episode.npz", "w") as z:
        z.writestr("joint.npy", b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + b"\0" * 840)
    value = import_demo(context(project, "demo_import", {"source_path": str(source)}))
    assert value["file_count"] == 2
    assert value["metadata"]["format"] == "lerobot"
    assert value["metadata"]["arrays"][0]["arrays"][0]["shape"] == [15, 7]
    repeat = import_demo(context(project, "demo_import", {"source_path": str(source)}))
    assert repeat["reused_verified_import"]
    shutil.rmtree(source)
    report = audit(context(project, "data_audit", {"import_result": value}))
    assert report["integrity_files_checked"] == 2
    assert not report["semantic_analysis_performed"]
    (project / value["dataset_path"] / "meta/info.json").write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        audit(context(project, "data_audit", {"import_result": value}))


def test_import_rejects_symlink_recursion_and_budget(project, tmp_path):
    source = tmp_path / "demo"
    source.mkdir()
    (source / "x").write_text("123456")
    (source / "alias").symlink_to(source / "x")
    with pytest.raises(ValueError, match="Symlink"):
        import_demo(context(project, "demo_import", {"source_path": str(source)}))
    (source / "alias").unlink()
    with pytest.raises(ValueError, match="budget"):
        import_demo(context(project, "demo_import", {"source_path": str(source), "max_total_bytes": 2}))
    with pytest.raises(ValueError, match="contain"):
        import_demo(context(project, "demo_import", {"source_path": str(project)}))


def test_cross_object_feature_search_does_not_apply_parameters(project):
    results = search(project, "卡槽/带键插销")
    assert results[0]["case_id"] == "CONN-002"
    case = results[0]
    assert case["match_reasons"][0]["feature"] == "keyed_insertion"
    assert "90° 候选" in case["applicability"]["parameters_not_to_copy"]
    assert all(x["verified"] for x in case["evidence_verification"])
    assert not case["hardware_verified"]
    open_case = search(project, "松手落桌")[0]
    assert open_case["case_id"] == "CONN-006"
    assert open_case["status"] == "open"


def test_evidence_tamper_is_detected(project):
    case = search(project, "带键插销")[0]
    (project / case["evidence"][0]).write_text("changed")
    changed = search(project, "带键插销")[0]
    assert changed["evidence_verification"][0]["verified"] is False


def test_incidents_append_classification_and_scope(project):
    value = record_incident(project, {"observed_behavior": "connector slipped", "primary_category": "physics_contact", "stage": "simulation_test", "mode": "simulation", "hardware_verified": True})
    assert not value["hardware_verified"]
    with pytest.raises(ValueError, match="revision_reason"):
        record_incident(project, {"incident_id": value["incident_id"], "observed_behavior": "updated"})
    changed = record_incident(project, {"incident_id": value["incident_id"], "observed_behavior": "root cause still unknown", "revision_reason": "new evidence"})
    assert changed["revision"] == 2
    assert (project / value["record_path"]).is_file()
    assert next(item for item in list_incidents(project) if item["incident_id"] == value["incident_id"])["revision"] == 2
    with pytest.raises(ValueError, match="Unknown primary"):
        record_incident(project, {"observed_behavior": "x", "primary_category": "made_up"})
    with pytest.raises(ValueError, match="escapes"):
        record_incident(project, {"observed_behavior": "x", "evidence": ["../outside"]})


def test_geometry_bounds_without_hallucinated_frames(project):
    mesh = project / "assets/object.obj"
    mesh.parent.mkdir()
    mesh.write_text("v 0 0 0\nv 2 0 0\nv 0 3 1\nf 1 2 3\n")
    report = audit_geometry(context(project, "geometry", {"asset_paths": ["assets"]}))
    result = report["assets"][0]
    assert result["extent_in_asset_units"] == [2., 3., 1.]
    assert result["unit"] == "unknown"
    assert result["functional_axes"] == "not_inferred"
    assert not report["assets_changed"]


def test_metadata_is_not_task_semantics(project):
    value = task_analysis(context(project, "task_analysis", {"import_result": {"metadata": {"fps": 30}}}))
    assert value["status"] == "needs_configuration"
    assert value["reason"] == "needs_multimodal_analysis"
    assert not value["proposal"]["phases"]
    with pytest.raises(ValueError):
        validate_proposal({"task_summary": "invented"})
    proposal = {"task_summary": "Visible pick", "objects": [], "phases": [{"name": "pick", "evidence_refs": ["frame-1"]}], "success_conditions": [], "unknowns": [], "required_assets": [], "confidence": .5}
    with pytest.raises(ValueError, match="outside supplied"):
        validate_proposal(proposal, ["frame-2"])
    assert validate_proposal(proposal, ["frame-1"])["verification_state"] == "model_proposal_unverified"



def test_incident_retains_evidence_after_source_changes(project):
    original = project / "failure.txt"
    original.write_text("failed at step 3")
    result = record_incident(project, {"observed_behavior": "slip", "evidence": ["failure.txt"]})
    evidence = result["evidence"][0]
    original.write_text("new run overwrote log")
    assert (project / evidence["path"]).read_text() == "failed at step 3"
    assert evidence["source_reference"] == "failure.txt"


def test_actual_video_decoder_sampling(project, tmp_path):
    import subprocess
    ffmpeg = Path("/usr/bin/ffmpeg")
    if not ffmpeg.exists():
        pytest.skip("No ffmpeg runtime on this host")
    (project / "config/environments.yaml").write_text(yaml.safe_dump({"environments": {"ffmpeg_executable": str(ffmpeg)}}))
    source = tmp_path / "video_demo"
    source.mkdir()
    subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1", "-pix_fmt", "yuv420p", str(source / "camera.mp4")], check=True, capture_output=True)
    imported = import_demo(context(project, "demo_import", {"source_path": str(source)}))
    value = audit(context(project, "data_audit", {"import_result": imported, "frames_per_video": 3}))
    assert value["videos"][0]["decoded"] is True
    assert len(value["sample_frames"]) == 3
    assert (project / value["videos"][0]["contact_sheet"]).is_file()
    assert value["sample_frames"][0]["requested_timestamp_seconds"] > 0



def test_lerobot_episode_interval_and_lossy_depth_metadata(project, tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    import subprocess
    source = tmp_path / "lerobot"
    video = source / "videos/observation.images.front/chunk-000/file-000.mp4"
    video.parent.mkdir(parents=True)
    (source / "meta/episodes/chunk-000").mkdir(parents=True)
    (source / "meta/info.json").write_text(json.dumps({"fps": 10, "codebase_version": "v3.0", "total_episodes": 2}))
    (source / "meta/nero_depth_encoding.json").write_text(json.dumps({"storage": "H.264 8-bit", "note": "lossy"}))
    prefix = "videos/observation.images.front"
    pq.write_table(pa.Table.from_pylist([{"episode_index": 1, "length": 5, prefix + "/chunk_index": 0, prefix + "/file_index": 0, prefix + "/from_timestamp": .2, prefix + "/to_timestamp": .7}]), source / "meta/episodes/chunk-000/file-000.parquet")
    ffmpeg = Path("/usr/bin/ffmpeg")
    if not ffmpeg.is_file():
        pytest.skip("No ffmpeg runtime")
    subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    (project / "config/environments.yaml").write_text(yaml.safe_dump({"environments": {"ffmpeg_executable": str(ffmpeg)}}))
    imported = import_demo(context(project, "demo_import", {"source_path": str(source)}))
    assert imported["metadata"]["episode_metadata_status"] == "read"
    value = audit(context(project, "data_audit", {"import_result": imported, "episode_index": 1, "frames_per_video": 2}))
    assert value["sample_selection_scope"] == "episode_metadata_interval"
    assert value["episode_index"] == 1
    assert all(.2 <= frame["requested_timestamp_seconds"] <= .7 for frame in value["sample_frames"])
    assert any(item["code"] == "lossy_depth_encoding" for item in value["findings"])
    with pytest.raises(ValueError, match="not present"):
        audit(context(project, "audit_missing", {"import_result": imported, "episode_index": 20}))


def test_model_result_without_frames_never_claims_semantic_success(project):
    proposal = {"task_summary": "metadata guess", "objects": [], "phases": [], "success_conditions": [], "unknowns": [], "required_assets": [], "confidence": .8}
    result = task_analysis(context(project, "task_analysis", {"semantic_model_result": proposal}))
    assert result["status"] == "needs_configuration"
    assert result["model_supplement_status"] == "not_used_without_actual_frame_evidence"



def test_incidents_fast_revisions_choose_numeric_latest_before_category_filter(project):
    first = record_incident(project, {"observed_behavior": "first", "primary_category": "physics_contact"})
    identifier = first["incident_id"]
    for number in range(2, 26):
        result = record_incident(project, {"incident_id": identifier, "observed_behavior": f"revision {number}", "revision_reason": "rapid regression", "primary_category": "planning_control" if number == 25 else "physics_contact"})
        assert result["revision"] == number
    selected = [item for item in list_incidents(project) if item["incident_id"] == identifier]
    assert len(selected) == 1
    assert selected[0]["revision"] == 25
    assert selected[0]["observed_behavior"] == "revision 25"
    assert not any(item["incident_id"] == identifier for item in list_incidents(project, "physics_contact"))
    assert any(item["incident_id"] == identifier and item["revision"] == 25 for item in list_incidents(project, "planning_control"))
    assert len(list((project / "knowledge/incidents").glob(identifier + ".r*.json"))) == 25



def test_new_incident_searchable_in_next_task_but_remains_unreviewed(project):
    evidence = project / "usd_failure.log"
    evidence.write_text("missing texture at ../../../assets/material.png")
    first = record_incident(project, {"observed_behavior": "USD纹理路径丢失，迁移依赖不完整", "primary_category": "workflow_integrity", "stage": "scene_build", "mode": "simulation", "reusable_checklist": ["复制后验证USD纹理引用在项目内"], "evidence": ["usd_failure.log"], "status": "verified_in_scope", "hardware_verified": True})
    cases = search(project, "USD纹理", category="workflow_integrity", stage="scene_build")
    result = next(item for item in cases if item["case_id"] == first["incident_id"])
    assert result["record_type"] == "incident_unreviewed"
    assert result["review_status"] == "unreviewed"
    assert result["diagnosis"]["state"] == "unknown"
    assert result["status"] == "open"
    assert not result["verification"]["passed_subchecks"]
    assert not result["hardware_verified"]
    assert result["evidence_verification"][0]["verified"]
    assert result["evidence_verification"][0]["expected_sha256"] == first["evidence"][0]["sha256"]
    assert result["automatic_parameter_application"] is False
    report = experience_run(context(project, "experience", {"query": "USD纹理"}))
    check = next(item for item in report["checklist"] if item["source_case"] == first["incident_id"])
    assert check["source_revision"] == 1
    assert check["source_record_type"] == "incident_unreviewed"
    assert check["source_review_status"] == "unreviewed"
    assert check["source_diagnosis_state"] == "unknown"
    assert check["status"] == "pending_task_validation"
    assert check["adoption"] == "review_required"
    assert check["parameters_not_to_copy"]
    assert report["automatic_parameter_application"] is False
    assert not any(item["case_id"] == first["incident_id"] for item in search(project, "USD纹理", category="physics_contact"))
    # Snapshot integrity is byte verification only, never curated validation.
    snapshot = project / first["evidence"][0]["path"]
    snapshot.write_text("tampered")
    changed = next(item for item in search(project, "USD纹理") if item["case_id"] == first["incident_id"])
    assert changed["evidence_verification"][0]["verified"] is False


def test_incident_search_uses_only_latest_revision_and_preserves_unresolved(project):
    first = record_incident(project, {"observed_behavior": "旧迁移依赖错误", "stage": "scene_build", "primary_category": "workflow_integrity"})
    latest = record_incident(project, {"incident_id": first["incident_id"], "revision_reason": "additional observation", "observed_behavior": "新的USD纹理引用错误", "stage": "scene_build", "primary_category": "workflow_integrity", "diagnosis": {"state": "hypothesis"}, "resolved": False})
    results = [item for item in search(project, "USD纹理/迁移依赖") if item["case_id"] == first["incident_id"]]
    assert len(results) == 1
    assert results[0]["revision"] == latest["revision"] == 2
    assert results[0]["diagnosis"]["state"] == "hypothesis"
    assert results[0]["status"] == "open"
    assert results[0]["preventive_checks"]
    assert results[0]["verification"]["scope"] == "unreviewed incident report"
    assert not any(item["case_id"] == first["incident_id"] for item in search(project, "旧迁移依赖错误" ) if item.get("observed_behavior") == "旧迁移依赖错误")
