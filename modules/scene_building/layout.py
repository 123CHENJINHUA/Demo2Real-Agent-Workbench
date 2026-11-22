"""Build a preview snapshot from bounded, explicitly unmeasured layout estimates."""
import copy
import hashlib
import math
from pathlib import Path

from PIL import Image

from contracts.tasks import canonical_hash, validate_task_spec, vector
from modules.scene_building.planar_alignment import apply_line_spacing, fit_reference_camera, line_spacing, observations, table_xy


def _needs_geometry(obj):
    return (obj["geometry_type"] == "unknown" or
            (obj["geometry_type"] == "mesh" and not obj.get("asset_path")) or
            (obj["geometry_type"] == "box" and obj.get("size_m") is None))


def needs_layout(spec):
    """Registered profiles and edited complete scenes keep their own geometry."""
    if spec.get("profile_id") or not spec.get("objects"):
        return False
    table = spec.get("scene", {}).get("table", {})
    return any(table.get(key) is None for key in ("size_m", "position_m")) or any(
        _needs_geometry(obj) or
        obj.get("pose", {}).get("position_m") is None or
        obj.get("pose", {}).get("quaternion_wxyz") is None or
        obj.get("mass_kg") is None
        for obj in spec["objects"]
    )


def physical_layout_key(spec):
    """Ignore revision, evidence, and sub-millimetre jitter when looking for repeated scenes."""
    def quantize(value):
        if isinstance(value,float):
            return round(value,4)
        if isinstance(value,list):
            return [quantize(item) for item in value]
        if isinstance(value,dict):
            return {key:quantize(item) for key,item in value.items()}
        return value

    fields=('id','geometry_type','pose','scale','size_m','asset_path','mass_kg','collision','preview_shape')
    return canonical_hash(quantize({
        'objects':[{key:obj[key] for key in fields if key in obj} for obj in spec['objects']],
        'table':spec['scene']['table'], 'rail':spec['scene'].get('preview_workbench_rail'),
        'robot_bases':[arm.get('base_pose') for arm in spec.get('robot',{}).get('arms',[])],
        'views':[{key:view[key] for key in ('id','eye_m','target_m','up','focal_fraction') if key in view}
                 for view in spec['scene'].get('preview_reference_views',[])],
    }))


def layout_schema(object_ids, evidence_refs, *, refinement=False, asset_paths=(), initial_refs=(), motion_ids=()):
    def array(length, minimum, maximum):
        return {"type": "array", "minItems": length, "maxItems": length,
                "items": {"type": "number", "minimum": minimum, "maximum": maximum}}

    def structure(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

    object_fields = {
        "id": {"type": "string", "enum": list(object_ids)},
        "size_m": array(3, .002, 3.),
        "center_xy_m": array(2, -3., 3.),
        "yaw_deg": {"type": "number", "minimum": -180, "maximum": 180},
        "color_rgb": array(3, 0., 1.),
        "evidence_refs": {"type": "array", "minItems": 1, "maxItems": 4,
                          "items": {"type": "string", "enum": list(evidence_refs)}},
    }
    properties = {"table_size_m": array(3, .02, 4.)}
    if motion_ids:
        anchor=structure({"object_id":{"type":"string","enum":list(object_ids)},
                          "event_id":{"type":"string","enum":list(motion_ids)},
                          "role":{"type":"string","enum":["initial_contact","target_contact"]},
                          "object_point_m":{"anyOf":[array(3,-3.,3.),{"type":"null"}]},
                          "frame_ref":{"type":"string","enum":list(evidence_refs)}})
        properties['motion_anchors']={'type':'array','maxItems':2*len(object_ids),'items':anchor}
    if initial_refs:
        uv=array(2,0,1)
        pair={'type':'array','minItems':2,'maxItems':2,'items':uv}
        object_id={'type':'string','enum':list(object_ids)}
        footprint=structure({'object_id':{'type':'string','enum':list(object_ids)},'center_uv':uv})
        view=structure({'frame_ref':{'type':'string','enum':list(initial_refs)},
                        'corners_uv':{'type':'array','minItems':4,'maxItems':4,'items':uv},
                        'footprints':{'type':'array','maxItems':len(object_ids),'items':footprint}})
        properties['planar_views']={'type':'array','maxItems':len(initial_refs),'items':view}
        line=structure({'frame_ref':{'type':'string','enum':list(initial_refs)},'reference_object':object_id,
                        'reference_axis':{'type':'integer','enum':[0,1,2]},'reference_uv':pair,'parallel_uv':pair,
                        'objects':{'type':'array','minItems':2,'maxItems':2,'items':object_id},'centers_uv':pair})
        properties['line_views']={'type':'array','maxItems':len(initial_refs)*3,'items':line}
    if asset_paths:
        object_fields["asset_ref"] = {"anyOf":[{"type":"string","enum":list(asset_paths)},{"type":"null"}]}
        object_fields["mesh_roll_pitch_deg"] = array(2,-180,180)
    if refinement:
        object_fields.update(
            shape={"type": "string", "enum": ["box", "channel", "framed_panel", "cable_bundle"]},
            support_id={"anyOf": [{"type": "string", "enum": list(object_ids)}, {"type": "null"}]},
            support_relation={"anyOf":[{"type":"string","enum":["on_top","inside"]},{"type":"null"}]},
        )
        finding = structure({"difference":{"type":"string"}, "correction":{"type":"string"},
                             "evidence_ref":{"type":"string","enum":list(evidence_refs)}})
        properties["findings"] = {"type":"array","minItems":1,"maxItems":8,"items":finding}
        properties["workbench_rail"] = {"type":"boolean"}
    properties["objects"] = {"type": "array", "minItems": len(object_ids), "maxItems": len(object_ids),
                             "items": structure(object_fields)}
    return structure(properties)


def _bounded(value, length, low, high, label):
    vector(value, length, label, False)
    if any(not low <= entry <= high for entry in value):
        raise ValueError(label + " is outside the preview layout bounds")
    return [float(entry) for entry in value]


def _place(center, half, occupied, table, *, allow_expansion):
    """Keep inferred boxes on the support and out of intersecting solids."""
    margin = .008
    cx, cy = table["position_m"][:2]
    sx, sy = table["size_m"][:2]
    for step in range(121):
        if step == 0:
            candidates = [center]
        else:
            distance = .04 * step
            candidates = [[center[0] + dx * distance, center[1] + dy * distance]
                          for dx, dy in ((0,1),(0,-1),(1,0),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1))]
        for x,y in candidates:
            if not (abs(x-cx)+half[0]+margin <= (2 if allow_expansion else sx/2)
                    and abs(y-cy)+half[1]+margin <= (2 if allow_expansion else sy/2)):
                continue
            if any(abs(x-ox) < half[0]+hx+margin and abs(y-oy) < half[1]+hy+margin
                   for ox,oy,hx,hy in occupied):
                continue
            occupied.append((x,y,*half[:2]))
            if allow_expansion:
                table["size_m"][:2] = [max(sx,2*(abs(x-cx)+half[0]+margin)), max(sy,2*(abs(y-cy)+half[1]+margin))]
            return x,y
    raise ValueError("估算对象无法放置在工作面上，请检查桌面尺寸与对象布局")


def _half_extents(size, quaternion):
    w,x,y,z = quaternion
    rotation = [
        [1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)],
        [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)],
        [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)],
    ]
    return [sum(abs(value)*length for value,length in zip(row,size))/2 for row in rotation]


def _rotation(roll, pitch, yaw):
    cr,cp,cy = (math.cos(math.radians(angle)/2) for angle in (roll,pitch,yaw))
    sr,sp,sy = (math.sin(math.radians(angle)/2) for angle in (roll,pitch,yaw))
    return [cr*cp*cy+sr*sp*sy,sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy]


def _support_order(objects, supports):
    pending = list(enumerate(objects))
    placed = {None}
    while pending:
        ready = [pair for pair in pending if supports.get(pair[1]["id"]) in placed]
        if not ready:
            raise ValueError("场景物体的支撑关系成环")
        ids = {obj["id"] for _,obj in ready}
        pending = [pair for pair in pending if pair[1]["id"] not in ids]
        yield from ready
        placed.update(ids)


def _object_dims(obj, estimate):
    return obj.get("preview_bounds_size_m") or [
        length*scale for length,scale in zip(obj.get("size_m") or estimate,obj.get("scale") or [1.,1.,1.])]


def _place_on_support(obj, dims, xy, support, support_dims, relation):
    support_pose = support["pose"]
    x,y,z = support_pose["position_m"]
    if relation=="inside":
        if support.get("preview_shape")=="channel":
            sx,sy,sz = support_dims
            if dims[0]>sx or dims[1]+.004>.64*sy:
                raise ValueError(f'「{obj.get("label") or obj["id"]}」与预览槽的尺寸无法配合')
            obj["pose"]["quaternion_wxyz"] = support_pose["quaternion_wxyz"].copy()
            return [x,y,z-.24*sz+dims[2]/2+.002]
        if support.get("geometry_type")!="mesh":
            raise ValueError(f'「{support.get("label") or support["id"]}」的内部支撑面需要可用的空腔几何')
        # Without a verified inner floor, seed over the mesh for visual and physical refinement.
    surface = _half_extents(support_dims,support_pose["quaternion_wxyz"])
    footprint = _half_extents(dims,obj["pose"]["quaternion_wxyz"])
    offset = [max(0.,a-b) for a,b in zip(surface[:2],footprint[:2])]
    center = [min(base+limit,max(base-limit,estimate)) for base,limit,estimate in zip((x,y),offset,xy)]
    return [*center,z+surface[2]+footprint[2]+.002]


def preview_snapshot(root, base, proposal, frame_refs, *, refinement=None, prior_refs=(), assets=None, locked=None,
                     initial_frames=None, prior_alignment=None):
    root = Path(root).resolve()
    spec = copy.deepcopy(base)
    objects = spec["objects"]
    if not 1 <= len(objects) <= 32:
        raise ValueError("场景近似需要 1 至 32 个已识别对象")
    records = proposal.get("objects", [])
    by_id = {entry["id"]: entry for entry in records}
    if len(records) != len(objects) or set(by_id) != {obj["id"] for obj in objects}:
        raise ValueError("场景草图中的对象必须与任务定义逐一对应")
    assets,locked = assets or {},locked or {}
    bindings = {name:locked.get(name,{}).get("asset_path") or estimate.get("asset_ref") for name,estimate in by_id.items()}
    selected_assets = [ref for ref in bindings.values() if ref]
    if len(set(selected_assets))!=len(selected_assets) or not set(selected_assets).issubset(assets):
        raise ValueError("场景引用了未审查或重复绑定的模型")
    evidence = spec["dependency_hashes"]["evidence"]
    source_refs = [*frame_refs,*prior_refs]
    if review_ref := (refinement or {}).get("visual_comparison_path"):
        source_refs.append(review_ref)
    for ref in source_refs:
        path = root / ref
        if not path.is_file() or path.resolve() != path or not path.is_relative_to(root):
            raise ValueError("场景引用了无效示教帧")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if ref in evidence and evidence[ref] != digest:
            raise ValueError("示教帧在场景生成前发生变化")
        evidence[ref] = digest

    provenance = spec["field_provenance"]
    def record(key, refs):
        provenance[key] = {"source": "multi_view_proxy_assumption", "evidence_refs": refs,
                           "validation_scope": "approximate_scene_preview_only"}

    scene = spec["scene"]
    table = scene.setdefault("table", {})
    expand = table.get("size_m") is None
    if expand:
        table["size_m"] = _bounded(proposal["table_size_m"], 3, .02, 4., "table_size_m")
        record("/scene/table/size_m", frame_refs)
    if table.get("position_m") is None:
        table["position_m"] = [0., 0., .75 - table["size_m"][2]/2]
        record("/scene/table/position_m", frame_refs)
    top = table["position_m"][2] + table["size_m"][2]/2
    occupied = []
    for obj in objects:
        pose = obj["pose"]
        if pose.get("position_m") is not None:
            dims = obj.get("size_m") or _bounded(by_id[obj["id"]]["size_m"],3,.002,3.,"size_m")
            dims = [size*scale for size,scale in zip(dims,obj.get("scale") or [1.,1.,1.])]
            half = _half_extents(dims,pose.get("quaternion_wxyz") or [1.,0.,0.,0.])
            occupied.append((*pose["position_m"][:2],*half[:2]))
    supports = {}
    relations = {}
    if refinement:
        for name, estimate in by_id.items():
            support = estimate["support_id"]
            if support is not None:
                if support==name or support not in by_id:
                    raise ValueError("场景引用了无效的支撑物体")
                supports[name] = support
                legacy_inside = estimate["shape"]=="framed_panel" and by_id[support]["shape"]=="channel"
                relations[name] = estimate.get("support_relation") or ("inside" if legacy_inside else "on_top")
                if relations[name] not in ("inside","on_top"):
                    raise ValueError("场景引用了无效的支撑方式")
    alignment=observations(proposal.get('planar_views'),initial_frames or {},
                           set(by_id)-set(supports))
    for key,value in (prior_alignment or {}).get('objects',{}).items():
        if key in by_id and key not in supports and all(ref in (initial_frames or {}) for ref in value['frame_refs']):
            alignment['objects'][key]=value
    generated = {}
    objects_by_id = {obj["id"]:obj for obj in objects}
    locked_positions={obj['id'] for obj in objects if obj['pose'].get('position_m') is not None}
    for index,obj in _support_order(objects,supports):
        estimate = by_id[obj["id"]]
        size = _bounded(estimate["size_m"], 3, .002, 3., obj["id"] + ".size_m")
        xy = _bounded(estimate["center_xy_m"], 2, -3., 3., obj["id"] + ".center_xy_m")
        placement=alignment['objects'].get(obj['id'])
        if placement:
            xy=table_xy(table,placement['table_uv']).tolist()
        yaw, = _bounded([estimate["yaw_deg"]], 1, -180., 180., "yaw_deg")
        color = _bounded(estimate["color_rgb"], 3, 0., 1., "color_rgb")
        refs = estimate["evidence_refs"]
        if not isinstance(refs,list) or not refs or len(refs)>4 or any(ref not in frame_refs for ref in refs):
            raise ValueError(obj["id"] + " 的场景假设需要引用已选示教画面")
        prefix = f"/objects/{index}"
        reference = bindings[obj["id"]]
        if _needs_geometry(obj) and reference:
            asset = assets[reference]
            mesh = root/reference
            if not mesh.is_relative_to(root) or mesh.resolve()!=mesh or not mesh.is_file():
                raise ValueError("所选模型文件不可用")
            if hashlib.sha256(mesh.read_bytes()).hexdigest()!=asset["sha256"]:
                raise ValueError("所选模型文件发生变化")
            lows,highs = asset["bounds"]
            extent = [hi-lo for lo,hi in zip(lows,highs)]
            prior_scale = locked.get(obj["id"],{}).get("scale")
            factor = prior_scale[0] if prior_scale else max(size)/max(extent)
            if not .000001<=factor<=1000:
                raise ValueError("模型尺度超出可预览范围")
            obj.update(geometry_type="mesh",asset_path=reference,unit="m",scale=[factor]*3,
                       collision={"type":"convex_hull"},preview_mesh_center_asset=[(lo+hi)/2 for lo,hi in zip(lows,highs)],
                       preview_bounds_size_m=[value*factor for value in extent],preview_color_rgb=color)
            spec["dependency_hashes"]["assets"][reference] = asset["sha256"]
            for key in ("geometry_type","asset_path","unit","scale","collision","preview_mesh_center_asset",
                        "preview_bounds_size_m","preview_color_rgb"):
                record(prefix+"/"+key,refs)
        elif _needs_geometry(obj):
            obj.update(geometry_type="box", size_m=size, unit="m", scale=[1.,1.,1.],
                       collision={"type": "box"}, preview_color_rgb=color)
            generated[obj["id"]] = obj
            for key in ("geometry_type", "size_m", "unit", "scale", "collision", "preview_color_rgb"):
                record(prefix+"/"+key, refs)
            if refinement:
                obj["preview_shape"] = estimate["shape"]
                record(prefix+"/preview_shape",refs)
        if obj.get("mass_kg") is None:
            obj["mass_kg"] = max(.02,min(10.,math.prod(size)*200))
            record(prefix+"/mass_kg",refs)
        pose = obj["pose"]
        if pose.get("quaternion_wxyz") is None:
            roll,pitch = _bounded(estimate.get("mesh_roll_pitch_deg",[0,0]),2,-180.,180.,"mesh_roll_pitch_deg") if (
                obj["geometry_type"]=="mesh") else (0.,0.)
            pose["quaternion_wxyz"] = _rotation(roll,pitch,yaw)
            record(prefix+"/pose/quaternion_wxyz",refs)
        if pose.get("position_m") is None:
            dims = _object_dims(obj,size)
            half = _half_extents(dims,pose["quaternion_wxyz"])
            support = objects_by_id.get(supports.get(obj["id"]))
            if support and ("support_relation" in estimate or support["id"] in generated):
                support_dims = _object_dims(support,by_id[support["id"]]["size_m"])
                pose["position_m"] = _place_on_support(obj,dims,xy,support,support_dims,relations[obj["id"]])
                obj["preview_support_id"] = support["id"]
                record(prefix+"/preview_support_id",refs)
            else:
                x,y = _place(xy,half,occupied,table,allow_expansion=expand)
                pose["position_m"] = [x,y,top+half[2]+.002]
            record(prefix+"/pose/position_m",refs)
            if placement:
                provenance[prefix+'/pose/position_m']={'source':alignment['method'],
                    'evidence_refs':placement['frame_refs'],'validation_scope':'relative_to_estimated_table_plane'}
    line_rows=line_spacing(proposal.get('line_views'),initial_frames or {},objects_by_id,supports)
    measured_lines={tuple(row['objects']):row for row in line_rows}
    for row in (prior_alignment or {}).get('line_spacings',[]):
        if all(ref in (initial_frames or {}) for ref in row['frame_refs']) and not set(row['objects'])&set(supports):
            measured_lines[tuple(row['objects'])]=row
    alignment['line_spacings']=[row for ids,row in measured_lines.items() if not set(ids)<=set(alignment['objects'])]
    index_by_id={obj['id']:i for i,obj in enumerate(objects)}
    for key,refs in apply_line_spacing(table,alignment,objects_by_id,supports,locked_positions):
        provenance[f'/objects/{index_by_id[key]}/pose/position_m']={'source':'recorded_line_spacing/v1',
            'evidence_refs':refs,'validation_scope':'relative_to_reference_mesh_dimension'}
    center = table["position_m"][:2]
    distance = max(table["size_m"][:2])
    if not scene.get("preview_camera"):
        scene["preview_camera"] = {"eye_m": [center[0]+distance,center[1]-1.2*distance,top+.85*distance],
                                   "target_m": [*center,top+.08*distance]}
        record("/scene/preview_camera",frame_refs)
    if refinement:
        views = {view['id']:view for view in [
            {"id":"front","eye_m":[center[0],center[1]-.78*distance,top+.27*distance],
             "target_m":[*center,top+.10*distance]},
            {"id":"side","eye_m":[center[0]-.78*distance,center[1]-.10*distance,top+.22*distance],
             "target_m":[*center,top+.11*distance]},
        ]}
        for item in proposal.get('planar_views') or []:
            if not isinstance(item,dict):
                continue
            ref=item.get('frame_ref')
            if ref not in (initial_frames or {}):
                continue
            source=Path(initial_frames[ref]).parts
            if len(source)<2:
                continue
            camera=source[1]
            suffix=camera.rsplit('.',1)[-1]
            if suffix in ('left_wrist','right_wrist','center'):
                continue
            try:
                with Image.open(root/ref) as image:
                    width,height=image.size
                    reduction=min(1,960/max(width,height))
                    dims=(round(width*reduction),round(height*reduction))
                    fitted=fit_reference_camera(table,item['corners_uv'],dims)
            except (KeyError,TypeError,ValueError,OSError,ArithmeticError):
                continue
            if fitted:
                identifier=suffix if suffix in ('front','side') else 'camera_'+hashlib.sha256(camera.encode()).hexdigest()[:10]
                views[identifier]=fitted|{'id':identifier,'recorded_camera':camera,'frame_ref':ref}
        scene['preview_reference_views']=list(views.values())
        scene.setdefault("physics_hz",120)
        scene.setdefault("settle_seconds",.65)
        scene["preview_workbench_rail"] = proposal["workbench_rail"]
        record("/scene/preview_reference_views",frame_refs)
        record("/scene/preview_workbench_rail",frame_refs)
    spec.update(state="draft", contact_execution_authorized=False, hardware_verified=False,
                scene_approximation={"source_task_spec_hash":base["content_hash"], "source_revision":base["revision"],
                                     "proxy_objects":list(generated), "reference_frames":frame_refs,
                                     "scope":"approximate_scene_preview_only", "metric_geometry_verified":False})
    if refinement:
        spec["scene_approximation"]["refinement"] = refinement | {"previous_render_paths":list(prior_refs)}
        spec["scene_approximation"]["object_roles"] = {
            name:{"shape":estimate["shape"],"support_id":supports.get(name),"support_relation":relations.get(name)}
            for name,estimate in by_id.items()
        }
    spec['scene_approximation']['placement_alignment']=alignment
    spec.pop("frozen_at",None)
    spec["assumptions"].append("Preview proxies use multi-view visual estimates and an assumed support layout. No calibrated object dimensions, gripper state or contact evidence is claimed.")
    spec["content_hash"] = canonical_hash(spec)
    return validate_task_spec(spec)
