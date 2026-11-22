from copy import deepcopy
import hashlib
import json

import pytest

from contracts.tasks import canonical_hash
from modules.scene_building.layout import needs_layout, preview_snapshot
from modules.geometry.preview import run as review_preview
from modules.scene_building.preview_details import box_components


def test_layout_adds_proxy_and_keeps_known_geometry_and_source(tmp_path):
    frame = tmp_path / "side.jpg"
    frame.write_bytes(b"side fixture")
    known = {"id":"fixture","geometry_type":"box","asset_path":None,"size_m":[.3,.2,.05],
             "scale":[1,1,1],"mass_kg":2.0,"collision":{"type":"box"},
             "pose":{"position_m":[0,0,.8],"quaternion_wxyz":[1,0,0,0]}}
    panel = {"id":"panel","geometry_type":"unknown","asset_path":None,"mass_kg":1.5,
             "collision":{"type":"unknown"},"pose":{"position_m":None,"quaternion_wxyz":None}}
    source = {"schema_version":"task-spec/v1","task_id":"fixture","revision":1,"state":"draft",
              "profile_id":None,"source":{},"robot":{},"scene":{"table":{}},
              "objects":[known,panel],"evaluation":{"action":{},"retention":{}},
              "frames":[],"phases":[],"unresolved":[],"assumptions":[],"field_provenance":{},
              "dependency_hashes":{"evidence":{},"assets":{}},"hardware_verified":False,
              "contact_execution_authorized":False}
    source["content_hash"] = canonical_hash(source)
    original = deepcopy(source)
    estimate = {"size_m":[.3,.03,.4],"center_xy_m":[0,0],"yaw_deg":20,
                "color_rgb":[.2,.2,.4],"evidence_refs":["side.jpg"]}
    draft = {"table_size_m":[1,1,.06],"objects":[estimate | {"id":name} for name in ("fixture","panel")]}
    assert needs_layout(source)
    preview = preview_snapshot(tmp_path, source, draft, ["side.jpg"])
    assert source == original and preview["objects"][0] == known
    proxy = preview["objects"][1]
    assert proxy["geometry_type"] == "box" and proxy["mass_kg"] == 1.5
    assert proxy["pose"]["position_m"][:2] != [0,0]
    assert preview["scene_approximation"]["source_task_spec_hash"] == source["content_hash"]
    assert preview["scene_approximation"]["proxy_objects"] == ["panel"]
    assert not preview["scene_approximation"]["metric_geometry_verified"]
    assert frame.name in preview["dependency_hashes"]["evidence"]
    assert preview["content_hash"] == canonical_hash(preview) != source["content_hash"]
    assert not needs_layout(preview)
    (tmp_path / "preview.json").write_text(json.dumps(preview))
    reviewed = review_preview({"root":tmp_path,"output_dir":tmp_path/"geometry",
        "input":{"assets":preview["objects"],"preview_proxy_ids":["panel"]},
        "dependencies":{"task_spec":{"spec_path":"preview.json","spec_hash":preview["content_hash"],"revision":1}}})
    report = json.loads((tmp_path/reviewed["report_path"]).read_text())
    assert report["assets"][0]["geometry_source"]["type"] == "procedural_fixture"
    assert report["assets"][1]["geometry_source"]["type"] == "multi_view_preview_proxy"
    assert report["task_spec_path"] == "preview.json"
    with pytest.raises(ValueError,match="引用已选示教画面"):
        preview_snapshot(tmp_path,source,draft | {"objects":[draft["objects"][0],draft["objects"][1] | {"evidence_refs":["wrong.jpg"]}]},["side.jpg"])

    mesh = tmp_path / "user-panel.obj"
    mesh.write_text("v 0 0 0\nv 300 0 0\nv 0 30 0\nv 0 0 400\nf 1 2 3\nf 1 4 2\n")
    assets = {mesh.name:{"sha256":hashlib.sha256(mesh.read_bytes()).hexdigest(),
                        "bounds":[[0,0,0],[300,30,400]]}}
    supplied = draft | {"objects":[draft["objects"][0],draft["objects"][1] | {"asset_ref":mesh.name}]}
    with_model = preview_snapshot(tmp_path,source,supplied,["side.jpg"],assets=assets)
    model = with_model["objects"][1]
    assert model["geometry_type"]=="mesh" and model["asset_path"]==mesh.name
    assert model["scale"]==[.001,.001,.001]
    assert with_model["scene_approximation"]["proxy_objects"]==[]
    assert with_model["dependency_hashes"]["assets"]=={mesh.name:assets[mesh.name]["sha256"]}
    from modules.scene_building.motion_anchors import apply_motion_anchors, observed_anchors
    event={'id':'left_closing_1','arm':'left','position_in_base_m':[.05,0.,model['pose']['position_m'][2]]}
    motion={'events':[event],'arms':{'left':{'urdf_path':'nero.urdf'}}}
    anchor={'object_id':'panel','event_id':event['id'],'role':'initial_contact',
            'object_point_m':[0.,0.,0.],'frame_ref':frame.name}
    assert not observed_anchors(with_model,{'motion_anchors':[anchor]},motion,{})
    contacts=observed_anchors(with_model,{'motion_anchors':[anchor]},motion,{frame.name:{'event_ids':[event['id']]}})
    with_model['scene_approximation']['motion_anchors']=contacts
    with_model['robot']['arms']=[{'id':'left','urdf_path':'nero.urdf',
                                'base_pose':{'position_m':[.2,0.,0.],'quaternion_wxyz':[1.,0.,0.,0.]}}]
    anchored=apply_motion_anchors(with_model)
    assert anchored['objects'][1]['pose']['position_m'][:2]==pytest.approx([.25,0.])
    assert anchored['objects'][1]['pose']['position_m'][2]==model['pose']['position_m'][2]
    assert anchored['objects'][0]==known and anchored['scene_approximation']['motion_anchor_review'][0]['used_for_initial_position']
    different_estimate = supplied | {"objects":[supplied["objects"][0],
        supplied["objects"][1] | {"size_m":[.35,.03,.45],"asset_ref":None}]}
    repeat = preview_snapshot(tmp_path,source,different_estimate,["side.jpg"],assets=assets,locked={"panel":model})
    assert repeat["objects"][1]["asset_path"]==mesh.name and repeat["objects"][1]["scale"]==model["scale"]
    oriented = supplied | {"objects":[supplied["objects"][0],supplied["objects"][1] | {
        "yaw_deg":0,"mesh_roll_pitch_deg":[180,0]}]}
    assert preview_snapshot(tmp_path,source,oriented,["side.jpg"],assets=assets)["objects"][1]["pose"][
        "quaternion_wxyz"]==pytest.approx([0,1,0,0])
    assert preview_snapshot(tmp_path,source,draft,["side.jpg"],assets=assets)["objects"][1]["geometry_type"]=="box"
    extra = assets | {"unused.stl":{"sha256":"unused","bounds":[[0,0,0],[1,1,1]]}}
    assert preview_snapshot(tmp_path,source,supplied,["side.jpg"],assets=extra)["objects"][1]["asset_path"]==mesh.name
    with pytest.raises(ValueError,match="未审查或重复绑定"):
        preview_snapshot(tmp_path,source,supplied | {"objects":[supplied["objects"][0] | {"asset_ref":mesh.name},supplied["objects"][1]]},["side.jpg"],assets=assets)


def test_refinement_builds_open_holders_and_binds_support(tmp_path):
    (tmp_path/"side.jpg").write_bytes(b"side evidence")
    (tmp_path/"prior.png").write_bytes(b"prior render")
    source = {"schema_version":"task-spec/v1","task_id":"fixture","revision":1,"state":"draft",
        "profile_id":None,"source":{},"robot":{},"scene":{"table":{}},"evaluation":{"action":{},"retention":{}},
        "frames":[],"phases":[],"unresolved":[],"assumptions":[],"field_provenance":{},
        "dependency_hashes":{"evidence":{},"assets":{}},"hardware_verified":False,"contact_execution_authorized":False}
    source["objects"] = [{"id":name,"geometry_type":"unknown","asset_path":None,"mass_kg":None,
        "collision":{"type":"unknown"},"pose":{"position_m":None,"quaternion_wxyz":None}}
        for name in ("panel","holder")]
    source["content_hash"] = canonical_hash(source)
    estimate = {"center_xy_m":[.2,0],"yaw_deg":0,"color_rgb":[.2,.2,.4],"evidence_refs":["side.jpg"]}
    proposal = {"table_size_m":[1,1,.06],"workbench_rail":True,"objects":[
        estimate|{"id":"panel","shape":"framed_panel","size_m":[.3,.025,.32],"support_id":"holder"},
        estimate|{"id":"holder","shape":"channel","size_m":[.35,.09,.06],"support_id":None},
    ]}
    revised = preview_snapshot(tmp_path,source,proposal,["side.jpg"],refinement={"round":1},prior_refs=["prior.png"])
    panel,holder = revised["objects"]
    assert panel["preview_support_id"]=="holder"
    assert panel["pose"]["position_m"][:2]==holder["pose"]["position_m"][:2]
    assert panel["pose"]["position_m"][2] > holder["pose"]["position_m"][2]
    assert {item["id"] for item in revised["scene"]["preview_reference_views"]}=={"front","side"}
    assert "prior.png" in revised["dependency_hashes"]["evidence"]
    geometry = box_components("channel",holder["size_m"],[1,1,0])
    assert len(geometry)==3 and geometry[1][2][1]==-geometry[2][2][1]
    assert geometry[0][1][2] < holder["size_m"][2]

    # A second task stacks a plain workpiece on a platform, with no channel or panel.
    stack_source = deepcopy(source)
    for obj,name in zip(stack_source["objects"],("parcel","platform")):
        obj["id"] = name
    stack_source["content_hash"] = canonical_hash(stack_source)
    stacking = proposal | {"objects":[
        estimate|{"id":"parcel","shape":"box","size_m":[.1,.05,.07],"support_id":"platform","support_relation":"on_top"},
        estimate|{"id":"platform","shape":"box","size_m":[.35,.2,.06],"support_id":None,"support_relation":None},
    ]}
    stacked = preview_snapshot(tmp_path,stack_source,stacking,["side.jpg"],refinement={"round":1},prior_refs=["prior.png"])
    workpiece,platform = stacked["objects"]
    assert workpiece["pose"]["position_m"][2] == pytest.approx(platform["pose"]["position_m"][2]+.03+.035+.002)
    assert stacked["scene_approximation"]["object_roles"]["parcel"]["support_relation"]=="on_top"


def test_new_import_runs_two_sequential_scene_corrections(tmp_path, monkeypatch):
    from application import scene_cycle

    output = tmp_path/"scene_build"
    previous = []
    rendered = []
    published = []
    def render(ctx):
        version = len(rendered)
        image = tmp_path/f"render{version}.png"
        image.write_bytes(f"image {version}".encode())
        manifest = tmp_path/f"manifest{version}.json"
        manifest.write_text(json.dumps({"cameras":[{"id":"overview","rgb_path":image.name}]}))
        build = {"status":"succeeded","preview_path":image.name,"manifest_path":manifest.name,
                 "spec_path":f"spec{version}.json","task_spec_hash":f"hash{version}","scene_path":f"scene{version}.usd"}
        rendered.append(build)
        return build
    def correct(ctx):
        binding = ctx["input"]["scene_refinement"]
        previous.append(binding)
        assert binding["views"][0]["sha256"]==hashlib.sha256((tmp_path/binding["views"][0]["path"]).read_bytes()).hexdigest()
        return {"status":"succeeded","report_path":f"layout{binding['round']}.json"}
    monkeypatch.setattr(scene_cycle,"build_scene",render)
    monkeypatch.setattr(scene_cycle,"prepare",correct)
    monkeypatch.setattr(scene_cycle,"validate_scene",lambda ctx:{"status":"succeeded","report_path":"local.json"})
    monkeypatch.setattr(scene_cycle,"review_visual",lambda ctx,built:{
        "status":"orientation_mismatch" if built["task_spec_hash"]=="hash1" else "orientation_verified",
        "report_path":built["task_spec_hash"]+".visual.json"})
    result = scene_cycle.run({"root":tmp_path,"output_dir":output,"input":{"source_path":"/fixture/import"},
        "dependencies":{"task_spec":{"spec_path":"spec0.json"}},"run_id":"fixture",
        "emit":lambda kind,data:published.append(data) if kind=="scene.published" else None,"check_cancel":lambda:None})
    assert [binding["spec_hash"] for binding in previous]==["hash0","hash1"]
    assert [entry["round"] for entry in result["refinement_rounds"]]==[1,2]
    assert result["task_spec_hash"]=="hash2"
    assert previous[1]["visual_comparison_path"]=="hash1.visual.json"
    assert [item["build"]["task_spec_hash"] for item in published]==["hash2"]


def test_scene_cycle_does_not_rebuild_repeated_geometry(tmp_path,monkeypatch):
    from application import scene_cycle

    output=tmp_path/'scene_build'
    spec={'objects':[{'id':'workpiece','pose':{'position_m':[.2,.1,.3]}}],
          'scene':{'table':{'position_m':[0,0,0],'size_m':[1,1,.1]}}}
    builds=[]

    def build(ctx):
        index=len(builds)
        record={'status':'succeeded','preview_path':f'render{index}.png','manifest_path':f'manifest{index}.json',
                'spec_path':f'spec{index}.json','task_spec_hash':str(index),'scene_path':f'scene{index}.usd'}
        builds.append(record)
        return record

    def prepare(ctx):
        number=ctx['input']['scene_refinement']['round']
        return {'status':'succeeded','spec_path':f'candidate{number}.json','report_path':f'layout{number}.json'}

    monkeypatch.setattr(scene_cycle,'load_snapshot',lambda root,ref,*args:spec|{'evidence':[ref]})
    monkeypatch.setattr(scene_cycle,'build_preview',build)
    monkeypatch.setattr(scene_cycle,'previous_scene',lambda root,previous,**kw:{'round':kw['round_number']})
    monkeypatch.setattr(scene_cycle,'prepare',prepare)
    monkeypatch.setattr(scene_cycle,'validate_scene',lambda ctx:{'status':'succeeded','report_path':'local.json'})
    monkeypatch.setattr(scene_cycle,'review_visual',lambda ctx,scene:{'status':'orientation_verified',
                        'report_path':'review.json','placement_alignment':{'status':'checked'}})
    result=scene_cycle.run({'root':tmp_path,'output_dir':output,'input':{'source_path':'/fixture'},'run_id':'task',
        'dependencies':{'task_spec':{'spec_path':'original.json'}},'emit':lambda *args:None,'check_cancel':lambda:None})
    assert len(builds)==2 and result['task_spec_hash']=='1' and result['selected_scene_round']==1
    assert result['refinement_rounds'][-1]['status']=='unchanged'
