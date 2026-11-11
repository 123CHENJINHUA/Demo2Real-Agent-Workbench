import json
import math
import shutil
import struct
from pathlib import Path
import numpy as np
import pytest
from modules.geometry.functional import run_functional, load_mesh


def _tube(path, outer=1., inner=.5, length=6., count=64):
    vertices, faces = [], []
    for x, radius in ((-length / 2, outer), (length / 2, outer), (-length / 2, inner), (length / 2, inner)):
        for index in range(count):
            angle = 2 * math.pi * index / count
            vertices.append((x, radius * math.cos(angle), radius * math.sin(angle)))
    for index in range(count):
        following = (index + 1) % count
        for a, b, c, d in ((index, following, following+count, index+count), (index+2*count, index+3*count, following+3*count, following+2*count), (index, index+2*count, following+2*count, following), (index+count, following+count, following+3*count, index+3*count)):
            faces.extend(((a,b,c),(a,c,d)))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("\n".join(["v " + " ".join(map(str, point)) for point in vertices] + ["f " + " ".join(str(i+1) for i in face) for face in faces]))


def _ctx(root, assets=None, **inputs):
    value = dict(inputs)
    if assets is not None:
        value["assets"] = assets
    return {"root":root,"output_dir":root/"runs/test/functional","input":value,"run_id":"test"}


def test_hollow_cylinder_axis_and_void_are_candidates_but_key_roll_unknown(tmp_path):
    mesh = tmp_path / "assets/tube.obj"
    _tube(mesh)
    report = run_functional(_ctx(tmp_path,[{"id":"unseen_tube","asset_path":"assets/tube.obj","unit":"m","collision":{"type":"convex_hull"}}]))
    asset = report["assets"][0]
    assert abs(asset["principal_axes_candidates"][0]["axis"][0]) > .99
    assert asset["functional_candidates"]["axial_void"]["supporting_sections"] >= 7
    assert asset["functional_candidates"]["axial_void"]["through_bore_proven"] is False
    assert asset["roll"]["geometric_ambiguity_detected"]
    assert asset["roll"]["status"] == "unresolved"
    assert not asset["roll"]["keyed_datum_verified"]
    assert any(issue["code"] == "single_convex_hull_would_fill_voids" and issue["severity"] == "blocking" for issue in asset["issues"])
    # Independent analytic annulus area checks the triangle section reconstruction.
    area = asset["sections"][3]["material_area"]
    assert area == pytest.approx(math.pi * (1. - .25), rel=.003)
    assert (tmp_path / asset["preview_path"]).read_text().startswith('<svg')
    assert report["physics_profile_provenance"] is None
    assert not report["functional_frames_frozen"]


def test_mesh_unit_unknown_does_not_silently_become_meters(tmp_path):
    _tube(tmp_path/"tube.obj",outer=10.,inner=5.,length=60.)
    report=run_functional(_ctx(tmp_path,[{"id":"millimeter_maybe","asset_path":"tube.obj"}]))
    asset=report["assets"][0]
    assert asset["unit_declaration"] is None
    assert asset["measurements_unit"]=="asset_units_unknown"
    assert max(asset["bounds"][1])==30.
    assert any(issue["code"]=="metric_scale_unknown" for issue in asset["issues"])
    assert asset["functional_candidates"]["profile_feature_correspondence"] is None
    assert not asset["metric_scale_verified"]


def test_engineering_profile_keeps_unmeasured_provenance_and_no_hardware_claim(tmp_path):
    _tube(tmp_path/"tube.obj",outer=.01,inner=.005,length=.06)
    profile=tmp_path/"engineering.json"
    profile.write_text(json.dumps({"units":"m","description":"engineering approximation, unmeasured","roll_candidates_deg":[90.,-90.],"bore_radius_m":.00555,"window_start_x_m":.016,"mouth_x_m":.029}))
    report=run_functional(_ctx(tmp_path,[{"id":"receiver","asset_path":"tube.obj","unit":"m","functional_role":"receiver","functional_probe":{"x_m":.024,"source":"declared engineering audit"}}],physics_profile_path="engineering.json"))
    assert report["physics_profile_provenance"]["validation_scope"]=="engineering_approximation_unmeasured"
    assert report["physics_profile_provenance"]["sha256"]
    assert not report["physics_profile_provenance"]["hardware_measured"]
    assert report["pair_feature_correspondence"]["status"]=="not_established"
    assert not report["hardware_verified"]
    assert not report["geometry_or_parameters_modified"]
    correspondence=report["assets"][0]["functional_candidates"]["profile_feature_correspondence"]
    assert correspondence["line_angle_deg_mod180"] is None
    assert correspondence["declared_radius_m"]==.00555
    assert not correspondence["hardware_verified"]


def test_spec_reference_primitive_fixture_and_unknown_object_are_distinct(tmp_path):
    spec=tmp_path/"task_spec.json"
    spec.write_text(json.dumps({"objects":[{"id":"block","asset_path":None,"geometry_type":"box","size_m":[.04,.05,.06],"unit":"m"}]}))
    ctx=_ctx(tmp_path)
    ctx["dependencies"]={"task_spec":{"spec_path":"task_spec.json", "spec_hash":"declared-canonical-spec-hash", "spec_revision":3}}
    report=run_functional(ctx)
    assert report["status"]=="completed"
    assert report["task_spec_hash"]=="declared-canonical-spec-hash"
    assert report["spec_revision"]==3
    assert report["task_spec_snapshot_sha256"]
    asset=report["assets"][0]
    assert asset["geometry_source"]["synthetic_test_fixture"]
    assert asset["asset_path"] is None and asset["mesh_sha256"] is None
    assert np.subtract(asset["bounds"][1],asset["bounds"][0])==pytest.approx([.04,.05,.06])
    spec.write_text(json.dumps({"objects":[{"id":"unknown_object","asset_path":None,"geometry_type":"unknown"}]}))
    unresolved=run_functional(ctx)
    assert unresolved["status"]=="needs_assets"
    assert unresolved["assets"][0]["status"]=="unresolved"
    assert not unresolved["assets"][0]["functional_candidates"]


def test_upstream_experience_adoption_is_explicit_and_does_not_resolve_failure(tmp_path):
    _tube(tmp_path/"tube.obj")
    ctx=_ctx(tmp_path,[{"id":"tube","asset_path":"tube.obj","unit":"m"}])
    ctx["dependencies"]={"experience":{"cases":[{"case_id":"CONN-006","status":"open","primary_category":"physics_contact","diagnosis":{"state":"hypothesis"}}],"checklist":[{"source_case":"CONN-006","source_revision":1,"text":"验证锁止保持"},{"source_case":"DATA","source_revision":2,"text":"审核视频编码"}]}}
    report=run_functional(ctx)
    reused=report["experience_review"]
    assert reused["checks"][0]["adoption"]=="adopted_as_review_check"
    assert reused["checks"][1]["adoption"]=="not_adopted_in_geometry_stage"
    assert reused["unresolved_source_records"][0]["status"]=="open"
    assert reused["unresolved_source_records"][0]["diagnosis_state"]=="hypothesis"
    assert all(check["status"]=="pending_task_validation" for check in reused["checks"])
    assert not reused["automatic_parameter_application"]



def test_rotated_binary_stl_does_not_hardcode_bore_axis(tmp_path):
    obj=tmp_path/"tube.obj"
    _tube(obj)
    vertices, faces=load_mesh(obj)
    angle=math.pi/4
    rotation=np.array([[math.cos(angle),-math.sin(angle),0.],[math.sin(angle),math.cos(angle),0.],[0.,0.,1.]])
    vertices=vertices@rotation.T
    stl=tmp_path/"rotated.stl"
    with stl.open("wb") as handle:
        handle.write(b"binary hollow tube".ljust(80,b" "))
        handle.write(struct.pack("<I",len(faces)))
        for face in faces:
            handle.write(struct.pack("<12fH",0.,0.,0.,*vertices[face].ravel(),0))
    report=run_functional(_ctx(tmp_path,[{"id":"rotated_tube","asset_path":"rotated.stl","unit":"m"}]))
    asset=report["assets"][0]
    assert abs(np.dot(asset["principal_axes_candidates"][0]["axis"],rotation[:,0]))>.99
    assert asset["functional_candidates"]["axial_void"]["supporting_sections"]>=7
    assert not asset["roll"]["keyed_datum_verified"]


def test_actual_connector_profiles_reproduce_visual_key_line_without_claiming_lock(tmp_path):
    project=Path(__file__).resolve().parents[3]
    profile_path=project/"tasks/connector/profile.json"
    if not profile_path.is_file():
        pytest.skip("Connector engineering fixture not installed")
    objects=json.loads(profile_path.read_text())["defaults"]["objects"]
    references={item["asset_path"] for item in objects}|{item["collision"]["profile_path"] for item in objects}
    for reference in references:
        target=tmp_path/reference
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(project/reference,target)
    report=run_functional(_ctx(tmp_path,objects))
    comparison=report["pair_feature_correspondence"]
    assert comparison["relative_line_roll_deg_mod180"]==pytest.approx(90.024,abs=.2)
    assert comparison["closest_declared_line_error_deg"]<.2
    assert not comparison["mechanical_180deg_symmetry_verified"]
    assert not comparison["lock_stroke_verified"]
    assert not comparison["hardware_verified"]
    assert report["physics_profile_provenance"]["hardware_measured"] is False
    assert all(asset["mesh_sha256"] for asset in report["assets"])
    female=next(asset for asset in report["assets"] if asset["id"]=="connector_female")
    assert any(section["hole_count"]>=3 for section in female["sections"])
    assert any(issue["code"]=="engineering_profile_geometry_difference" for issue in female["issues"])
    import xml.etree.ElementTree as ET
    for preview in report["preview_paths"]:
        assert ET.parse(tmp_path/preview).getroot().tag.endswith("svg")
