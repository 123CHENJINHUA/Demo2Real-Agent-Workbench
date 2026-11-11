"""Mesh-derived feature candidates with explicit geometry and provenance boundaries.

This worker emits evidence, never edits functional dimensions or robot frames.
Only NumPy and the standard library are needed; no Isaac, GPU or device imports.
"""
from __future__ import annotations
import hashlib
import html
import json
import math
from pathlib import Path
import struct
import numpy as np

VERSION = "geometry.functional.v1"
UNITS = {"m": 1., "mm": .001, "cm": .01}


def _local(root: Path, value) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else root / path
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Functional geometry paths must remain inside project; symlinks rejected")
    return path.resolve()


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_mesh(path: Path, cancel=lambda: None):
    if path.stat().st_size > 256 * 1024**2:
        raise ValueError("Mesh exceeds 256 MiB functional audit budget")
    vertices, faces = [], []
    if path.suffix.lower() == ".obj":
        with path.open(encoding="utf-8", errors="replace") as source:
            for number, line in enumerate(source):
                if number % 10000 == 0:
                    cancel()
                fields = line.split()
                if not fields:
                    continue
                if fields[0] == "v":
                    vertices.append([float(x) for x in fields[1:4]])
                elif fields[0] == "f":
                    polygon = []
                    for field in fields[1:]:
                        index = int(field.split("/")[0])
                        if index == 0:
                            raise ValueError("OBJ face index zero is invalid")
                        polygon.append(index - 1 if index > 0 else len(vertices) + index)
                    faces.extend((polygon[0], polygon[index], polygon[index + 1]) for index in range(1, len(polygon) - 1))
                if len(vertices) > 500000 or len(faces) > 1000000:
                    raise ValueError("Mesh vertex/triangle budget exceeded")
    elif path.suffix.lower() == ".stl":
        with path.open("rb") as source:
            header = source.read(84)
            count = struct.unpack("<I", header[80:84])[0] if len(header) == 84 else -1
            if 84 + count * 50 == path.stat().st_size:
                if count > 1000000:
                    raise ValueError("STL triangle budget exceeded")
                for number in range(count):
                    if number % 10000 == 0:
                        cancel()
                    values = struct.unpack("<12fH", source.read(50))
                    vertices.extend([values[offset:offset + 3] for offset in (3, 6, 9)])
            else:
                source.seek(0)
                for number, line in enumerate(source):
                    if number % 10000 == 0:
                        cancel()
                    fields = line.decode("ascii", "replace").split()
                    if fields and fields[0].casefold() == "vertex":
                        vertices.append([float(x) for x in fields[1:4]])
                    if len(vertices) > 3000000:
                        raise ValueError("STL vertex budget exceeded")
                if len(vertices) % 3:
                    raise ValueError("Incomplete STL triangle")
            faces = np.arange(len(vertices), dtype=np.int64).reshape(-1, 3)
    else:
        raise ValueError("Functional mesh audit supports OBJ and STL only")
    points = np.asarray(vertices, dtype=float)
    triangles = np.asarray(faces, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4 or triangles.ndim != 2 or triangles.shape[1] != 3:
        raise ValueError("Mesh needs finite 3D vertices and triangular faces")
    if not np.isfinite(points).all() or triangles.min() < 0 or triangles.max() >= len(points):
        raise ValueError("Nonfinite vertex or invalid triangle index")
    # Weld exact/seam duplicates using a tolerance relative to model span.
    tolerance = max(float(np.ptp(points, axis=0).max()) * 1e-9, 1e-12)
    _, first, inverse = np.unique(np.rint((points - points.min(axis=0)) / tolerance).astype(np.int64), axis=0, return_index=True, return_inverse=True)
    return points[first], inverse[triangles]


def _surface_pca(vertices, faces):
    reference = vertices.mean(axis=0)
    tri = vertices[faces] - reference
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    areas = np.linalg.norm(cross, axis=1) * .5
    valid = areas > max(float(areas.max()) * 1e-12, 1e-30)
    tri, areas, cross = tri[valid], areas[valid], cross[valid]
    total = areas.sum()
    if total <= 0:
        raise ValueError("Mesh contains no nondegenerate surface area")
    sums = tri.sum(axis=1)
    center = np.sum(sums * (areas / 3)[:, None], axis=0) / total
    # Exact uniform-triangle second moment, independent of surface tessellation.
    moments = (np.einsum("tki,tkj->tij", tri, tri) + np.einsum("ti,tj->tij", sums, sums)) / 12
    covariance = np.einsum("t,tij->ij", areas, moments) / total - np.outer(center, center)
    values, basis = np.linalg.eigh(covariance)
    values, basis = values[::-1], basis[:, ::-1]
    for index in range(3):
        if basis[np.argmax(np.abs(basis[:, index])), index] < 0:
            basis[:, index] *= -1
    if np.linalg.det(basis) < 0:
        basis[:, 2] *= -1
    return center + reference, values, basis, areas.sum(), len(faces) - int(valid.sum())


def _topology(faces):
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    _, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    boundary = int(np.count_nonzero(counts == 1))
    nonmanifold = int(np.count_nonzero(counts > 2))
    return {"boundary_edges": boundary, "nonmanifold_edges": nonmanifold, "closed_edge_topology": boundary == 0 and nonmanifold == 0, "self_intersection_checked": False, "physical_solid_verified": False}


def _area(poly):
    return .5 * float(np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - poly[:, 1] * np.roll(poly[:, 0], -1)))


def _contains(poly, point):
    x, y = point
    a, b = poly, np.roll(poly, -1, axis=0)
    active = (a[:, 1] > y) != (b[:, 1] > y)
    if not active.any():
        return False
    a, b = a[active], b[active]
    intercept = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return bool(np.count_nonzero(x < intercept) % 2)


def _hull(points):
    points = sorted(set(tuple(point) for point in points))
    if len(points) < 3:
        return np.asarray(points)
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return np.asarray(lower[:-1] + upper[:-1])


def section(vertices, faces, origin, axis, u, v):
    """Intersect actual triangles, reconstruct closed contour candidates, retain gaps."""
    triangle = vertices[faces]
    distances = (triangle - origin) @ axis
    crossing = (distances.min(axis=1) <= 0) & (distances.max(axis=1) > 0)
    triangle, distances = triangle[crossing], distances[crossing]
    intersections = np.zeros((len(triangle), 3, 3))
    counts = np.zeros(len(triangle), dtype=np.int8)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = distances[:, a], distances[:, b]
        mask = ((da <= 0) & (db > 0)) | ((db <= 0) & (da > 0))
        indices = np.flatnonzero(mask)
        ratio = da[mask] / (da[mask] - db[mask])
        points = triangle[mask, a] + ratio[:, None] * (triangle[mask, b] - triangle[mask, a])
        intersections[indices, counts[indices]] = points
        counts[indices] += 1
    hits = intersections[counts == 2, :2] - origin
    segments = np.stack((hits @ u, hits @ v), axis=-1)
    if not len(segments):
        return {"segments": [], "loops": [], "closed_contour_count": 0, "hole_count": 0, "status": "no_intersection"}
    tolerance = max(float(np.ptp(vertices, axis=0).max()) * 1e-6, 1e-12)
    quantized = np.rint(segments.reshape(-1, 2) / tolerance).astype(np.int64)
    _, first, mapping = np.unique(quantized, axis=0, return_index=True, return_inverse=True)
    nodes = segments.reshape(-1, 2)[first]
    edges = np.unique(np.sort(mapping.reshape(-1, 2), axis=1), axis=0)
    edges = edges[edges[:, 0] != edges[:, 1]]
    adjacency = [[] for _ in nodes]
    for a, b in edges:
        adjacency[a].append(int(b)); adjacency[b].append(int(a))
    visited, loops = set(), []
    ambiguous_nodes = sum(bool(neighbors) and len(neighbors) != 2 for neighbors in adjacency)
    for seed in range(len(nodes)):
        if seed in visited or len(adjacency[seed]) != 2:
            continue
        trail, previous, current = [], -1, seed
        while current not in visited and len(adjacency[current]) == 2:
            visited.add(current); trail.append(current)
            options = adjacency[current]
            following = options[0] if options[0] != previous else options[1]
            previous, current = current, following
        if current == seed and len(trail) >= 3:
            poly = nodes[trail]
            if abs(_area(poly)) > tolerance**2:
                loops.append(poly)
    records = []
    for index, poly in enumerate(loops):
        # First boundary point nudged toward its mean avoids centroid-in-hole errors.
        probe = poly[0] * (1 - 1e-7) + poly.mean(axis=0) * 1e-7
        nesting = sum(_contains(other, probe) for number, other in enumerate(loops) if number != index)
        signed_area = _area(poly)
        a, b = poly, np.roll(poly, -1, axis=0)
        weights = a[:, 0] * b[:, 1] - b[:, 0] * a[:, 1]
        centroid = np.sum((a + b) * weights[:, None], axis=0) / (6 * signed_area)
        records.append({"area": abs(signed_area), "centroid": centroid.tolist(), "bounds": [poly.min(axis=0).tolist(), poly.max(axis=0).tolist()], "nesting": nesting, "hole": bool(nesting % 2), "points": poly.tolist()})
    material_area = sum((-1 if record["hole"] else 1) * record["area"] for record in records)
    hull = _hull(nodes)
    hull_area = abs(_area(hull)) if len(hull) >= 3 else 0.
    origin_containment = sum(_contains(poly, (0., 0.)) for poly in loops)
    return {"segments": segments.tolist(), "loops": records, "closed_contour_count": len(records), "hole_count": sum(record["hole"] for record in records), "ambiguous_nodes": ambiguous_nodes, "center_void_candidate": origin_containment > 0 and origin_containment % 2 == 0, "material_area": material_area, "convex_hull_area": hull_area, "convex_hull_added_area_fraction": max(0., (hull_area - material_area) / hull_area) if hull_area else None, "status": "closed_contour_candidates" if records and not ambiguous_nodes else "incomplete_contours"}


def _opposed_features(cross_section, holes):
    candidates = [record for record in cross_section["loops"] if record["hole"] == holes]
    if len(candidates) < 2:
        return None
    best = None
    for index, a in enumerate(candidates):
        for b in candidates[index + 1:]:
            ca, cb = np.array(a["centroid"]), np.array(b["centroid"])
            ra, rb = np.linalg.norm(ca), np.linalg.norm(cb)
            if min(ra, rb) < 1e-12:
                continue
            opposition = -float(np.dot(ca, cb) / (ra * rb))
            balance = min(ra, rb) / max(ra, rb)
            area_balance = min(a["area"], b["area"]) / max(a["area"], b["area"])
            if opposition < .9 or balance < .6 or area_balance < .3:
                continue
            score = opposition * balance * area_balance
            angle = math.degrees(math.atan2(*(cb - ca)[::-1])) % 180
            record = {"line_angle_deg_mod180": angle, "opposition": opposition, "radial_balance": balance, "feature_centroids": [a["centroid"], b["centroid"]], "status": "opposed_contour_feature_candidate", "mechanical_symmetry_verified": False}
            if best is None or score > best[0]:
                best = (score, record)
    return best[1] if best else None


def _svg(path, title, sections, basis, bounds, unit):
    width, tile_w, tile_h = 1200, 300, 270
    rows = max(1, math.ceil(len(sections) / 4))
    height = 115 + rows * tile_h
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="#101c2a"/>', f'<text x="24" y="30" fill="#e3edf5" font-family="sans-serif" font-size="19">{html.escape(title)}</text>', f'<text x="24" y="55" fill="#a7becf" font-family="sans-serif" font-size="13">Triangle intersections | cyan=outer, orange=hole candidates | unit: {html.escape(unit)}</text>', '<text x="24" y="77" fill="#f4c379" font-family="sans-serif" font-size="12">Geometric candidates only. PCA axis != assembly axis. No hidden lock or force validation.</text>']
    for index, item in enumerate(sections):
        left, top = (index % 4) * tile_w, 100 + (index // 4) * tile_h
        segments = np.asarray(item.get("segments", []), dtype=float)
        if not len(segments):
            continue
        low, high = segments.reshape(-1, 2).min(axis=0), segments.reshape(-1, 2).max(axis=0)
        center = (low + high) * .5
        factor = 210 / max(float((high - low).max()), 1e-12)
        def pixel(point):
            result = (np.asarray(point) - center) * factor
            return (left + tile_w / 2 + result[0], top + 130 - result[1])
        parts.append(f'<text x="{left + 14}" y="{top + 18}" fill="#c6d8e5" font-family="sans-serif" font-size="12">{html.escape(str(item.get("label", index)))}</text>')
        if item.get("loops"):
            for loop in item["loops"]:
                coords = " ".join(f"{x:.2f},{y:.2f}" for x, y in [pixel(point) for point in loop["points"]])
                color = "#ffb454" if loop["hole"] else "#4cd8d0"
                parts.append(f'<polygon points="{coords}" fill="none" stroke="{color}" stroke-width="1.2"/>')
        else:
            for segment in segments[::max(1, len(segments) // 2000)]:
                a, b = pixel(segment[0]), pixel(segment[1])
                parts.append(f'<path d="M{a[0]:.2f},{a[1]:.2f} L{b[0]:.2f},{b[1]:.2f}" stroke="#f1bd61" fill="none"/>')
        origin = pixel((0., 0.))
        parts.append(f'<circle cx="{origin[0]:.2f}" cy="{origin[1]:.2f}" r="2.4" fill="#ec6684"/>')
        parts.append(f'<text x="{left + 14}" y="{top + 251}" fill="#9bb2c7" font-family="sans-serif" font-size="11">loops={item["closed_contour_count"]} holes={item["hole_count"]} unresolved={item.get("ambiguous_nodes",0)}</text>')
    parts.append('</svg>')
    path.write_text("\n".join(parts), encoding="utf-8")


def _issue(code, message, severity="review", category="geometry_function", **extra):
    return {"code": code, "primary_category": category, "stage": "asset_modeling", "severity": severity, "status": "open", "message": message, "hardware_verified": False} | extra


def _profile_probe(vertices, faces, item, profile):
    declared_probe = item.get("functional_probe") or {}
    role = item.get("collision", {}).get("role") or item.get("functional_role") or declared_probe.get("role")
    role = {"external_latch": "male", "receiver": "female"}.get(role, role)
    if role not in ("male", "female") or not profile:
        return None
    explicit_x = item.get("feature_probe_x_m", declared_probe.get("x_m"))
    if explicit_x is not None:
        positions = [float(explicit_x)]
        source = declared_probe.get("source") or "explicit_task_feature_probe"
    else:
        keys = ("latch_head_front_x_m", "latch_head_back_x_m") if role == "male" else ("window_start_x_m", "mouth_x_m")
        if not all(key in profile for key in keys):
            return None
        a, b = (float(profile[key]) for key in keys)
        positions = [a + (b - a) * fraction for fraction in (.25, .5, .75)]
        source = "scan_inside_declared_engineering_feature_interval"
    scans = []
    for x in positions:
        cross_section = section(vertices, faces, np.array([x, 0., 0.]), np.array([1., 0., 0.]), np.array([0., 1., 0.]), np.array([0., 0., 1.]))
        feature = _opposed_features(cross_section, role == "female")
        cross_section.update(label=f"profile local X={x:.6f}m", plane_origin=[x, 0., 0.], plane_axis=[1., 0., 0.], transverse_basis=[[0., 1., 0.], [0., 0., 1.]], opposed_feature=feature)
        scans.append(cross_section)
    features = [scan["opposed_feature"] for scan in scans if scan["opposed_feature"]]
    angle = None
    if features:
        angles = np.radians([record["line_angle_deg_mod180"] * 2 for record in features])
        angle = float(np.degrees(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) * .5 % 180)
    radial = []
    expected_key = "pin_radius_m" if role == "male" else "bore_radius_m"
    for scan in scans:
        central = [record for record in scan["loops"] if record["hole"] == (role == "female")]
        if central:
            target = min(central, key=lambda record: np.linalg.norm(record["centroid"]))
            radial.append(math.sqrt(target["area"] / math.pi))
    return {"role": role, "probe_source": source, "coordinate_assumption": "profile local +X, with Y/Z used only because explicitly bound engineering collision role", "scan_sections": scans, "line_angle_deg_mod180": angle, "supported_feature_sections": len(features), "equivalent_radius_estimates_m": radial, "declared_radius_key": expected_key, "declared_radius_m": profile.get(expected_key), "radius_difference_m": [value - float(profile[expected_key]) for value in radial] if expected_key in profile else [], "radius_comparison_scope": "mesh section area-equivalent radius vs engineering approximation; does not validate CAD tolerance or real fit", "hardware_verified": False}


def _clipped_surface_patch(triangle, origin, axis, half_length):
    polygon = list(triangle)
    for sign in (1., -1.):
        clipped = []
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            da = sign * np.dot(a - origin, axis) - half_length
            db = sign * np.dot(b - origin, axis) - half_length
            if da <= 0:
                clipped.append(a)
            if (da <= 0) != (db <= 0):
                clipped.append(a + (b - a) * da / (da - db))
        polygon = clipped
        if len(polygon) < 3:
            return 0., None
    total, moment = 0., np.zeros(3)
    for index in range(1, len(polygon) - 1):
        a, b, c = polygon[0], polygon[index], polygon[index + 1]
        area = np.linalg.norm(np.cross(b - a, c - a)) * .5
        total += area
        moment += area * (a + b + c) / 3
    return total, moment / total if total > 0 else None


def _grasp_candidates(identifier, vertices, faces, center, basis, limits, unit):
    tri = vertices[faces]
    centroids = tri.mean(axis=1)
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    doubled_area = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(doubled_area[:, None], 1e-30)
    axial = limits[1][0] - limits[0][0]
    output = []
    for fraction in (.35, .5, .65):
        distance = limits[0][0] + axial * fraction
        candidate_center = center + distance * basis[:, 0]
        local = centroids - candidate_center
        signed = (tri - candidate_center) @ basis[:, 0]
        half_length = axial * .075
        slab = (signed.min(axis=1) <= half_length) & (signed.max(axis=1) >= -half_length)
        if not slab.any():
            continue
        slab_vertices = tri[slab].reshape(-1, 3)
        for transverse in (1, 2):
            jaw = basis[:, transverse]
            positions = (slab_vertices - candidate_center) @ jaw
            low, high = positions.min(), positions.max()
            width = float(high - low)
            supports = []
            for sign, extreme in ((-1, low), (1, high)):
                alignment = np.abs(normals @ jaw)
                near_support = np.abs(local @ jaw - extreme) < max(width * .12, 1e-12)
                active = slab & near_support & (alignment >= .75) & (doubled_area > 1e-30)
                patches = [(number, *_clipped_surface_patch(tri[number], candidate_center, basis[:, 0], half_length)) for number in np.flatnonzero(active)]
                patches = [patch for patch in patches if patch[1] > 1e-30]
                weights = np.asarray([patch[1] for patch in patches])
                supports.append({"side": sign, "surface_area": float(weights.sum()), "triangle_count": len(patches), "centroid": np.average([patch[2] for patch in patches], axis=0, weights=weights).tolist() if patches else None, "mean_abs_normal_alignment": float(np.average([alignment[patch[0]] for patch in patches], weights=weights)) if patches else None, "verified_for_grasp": False})
            both = all(item["triangle_count"] > 0 for item in supports)
            output.append({"id": f"{identifier}_grasp_{len(output)}", "center_local": candidate_center.tolist(), "jaw_closing_axis_candidate": jaw.tolist(), "approach_axes_candidates": [basis[:, 3 - transverse].tolist(), (-basis[:, 3 - transverse]).tolist()], "minimum_bounding_width": width, "measurements_unit": unit, "contact_patch_candidates": supports, "status": "opposed_surface_regions_candidate" if both else "bounding_region_no_opposed_patches", "opposing_contact_patches_verified": False, "robot_reach_verified": False, "external_obstacle_clearance_verified": False, "functional_region_clearance_verified": False, "demonstration_orientation_preserved": False, "selection": "requires_demo_and_scene_validation"})
    return output


def _reuse_checks(ctx):
    dependencies = ctx.get("dependencies", {})
    experience = dependencies.get("experience", {})
    if isinstance(experience, dict) and isinstance(experience.get("result"), dict):
        experience = experience["result"]
    checks = experience.get("checklist", []) if isinstance(experience, dict) else []
    reused = []
    relevant = {"geometry_function", "physics_contact", "planning_control", "frames_calibration", "workflow_integrity", "evaluation_metrics"}
    records = {item.get("case_id"): item for item in experience.get("cases", [])} if isinstance(experience, dict) else {}
    for check in checks:
        source = dict(check)
        record = records.get(check.get("source_case"), {})
        applies = record.get("primary_category") in relevant or any(word in str(check.get("text", "")) for word in ("孔", "槽", "几何", "抓", "滚转", "尺度", "单位", "纹理", "依赖", "锁止", "标定"))
        source.update(adoption="adopted_as_review_check" if applies else "not_adopted_in_geometry_stage", adoption_reason="Relevant geometry/contact/frame review procedure; historical parameters are not applied." if applies else "This check belongs to a different stage or lacks enough applicability context; retained for that stage's review.", status="pending_task_validation")
        reused.append(source)
    unresolved = [{"source_id": item.get("case_id"), "status": item.get("status"), "diagnosis_state": item.get("diagnosis", {}).get("state", "unknown")} for item in records.values() if item.get("status") == "open" or item.get("record_type") == "incident_unreviewed"]
    return {"upstream_experience_present": bool(checks), "checks": reused, "unresolved_source_records": unresolved, "automatic_parameter_application": False, "scope": "Review checklist adopted only; unresolved source incidents remain unresolved."}


def _asset_inputs(ctx):
    inputs = ctx.get("input", {})
    assets = inputs.get("assets")
    if assets is None:
        spec = ctx.get("dependencies", {}).get("task_spec", {})
        if isinstance(spec, dict) and spec.get("spec_path"):
            spec = json.loads(_local(Path(ctx["root"]).resolve(), spec["spec_path"]).read_text())
        spec = spec.get("task_spec", spec) if isinstance(spec, dict) else {}
        assets = spec.get("objects") if isinstance(spec, dict) else None
    if assets is None:
        paths = inputs.get("asset_paths", [])
        paths = [paths] if isinstance(paths, str) else paths
        assets = [{"id": f"asset_{index}", "asset_path": path} for index, path in enumerate(paths)]
    if not isinstance(assets, list) or len(assets) > 32:
        raise ValueError("Functional analysis requires at most 32 explicit mesh assets")
    return assets


def run_functional(ctx: dict) -> dict:
    root = Path(ctx["root"]).resolve()
    output = _local(root, ctx["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    inputs = ctx.get("input", {})
    cancel = ctx.get("check_cancel", lambda: None)
    emit = ctx.get("emit", lambda *args: None)
    supplied = _asset_inputs(ctx)
    report_assets, previews, issues = [], [], []
    reuse = _reuse_checks(ctx)
    if not reuse["upstream_experience_present"]:
        issues.append(_issue("experience_review_missing", "No upstream experience checklist supplied. Query applicable cases before functional modeling; this worker does not directly import experience implementation.", category="workflow_integrity"))
    profile_ref = inputs.get("physics_profile_path")
    if not profile_ref:
        references = {item.get("collision", {}).get("profile_path") for item in supplied if isinstance(item, dict) and isinstance(item.get("collision"), dict)} - {None, ""}
        if len(references) == 1:
            profile_ref = next(iter(references))
    profile, provenance = {}, None
    if profile_ref:
        path = _local(root, profile_ref)
        profile = json.loads(path.read_text())
        provenance = {"path": path.relative_to(root).as_posix(), "sha256": _sha(path), "description": profile.get("description"), "validation_scope": "engineering_approximation_unmeasured", "hardware_measured": False, "parameters_changed": False}
        issues.append(_issue("engineering_profile_unmeasured", "Declared profile values are simulation engineering assumptions; visual correspondence cannot establish real spring constants, tolerances, lock stroke or retention.", category="physics_contact"))
    for index, item in enumerate(supplied):
        cancel()
        identifier = str(item.get("id", f"asset_{index}"))
        reference = item.get("mesh_path") or item.get("asset_path") or item.get("path")
        procedural = not reference and item.get("geometry_type") == "box" and item.get("size_m") is not None
        if procedural:
            size = np.asarray(item["size_m"], dtype=float)
            if size.shape != (3,) or not np.isfinite(size).all() or np.any(size <= 0):
                raise ValueError("Procedural box size_m must be positive finite dimensions")
            vertices = np.asarray([[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)]) * size
            faces = np.asarray([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
            path = None
            geometry_source = {"type": "procedural_fixture", "size_m": size.tolist(), "synthetic_test_fixture": True, "scope": "Explicit primitive dimensions for interface testing; not autonomous reconstruction from a new demonstration"}
            unit_default = "m"
        elif not reference:
            issue = _issue("mesh_missing", f"{identifier}: no mesh or explicit primitive geometry supplied", severity="blocking", asset_id=identifier)
            issues.append(issue)
            report_assets.append({"id": identifier, "asset_path": None, "status": "unresolved", "functional_candidates": {}, "issues": [issue], "hardware_verified": False})
            continue
        else:
            path = _local(root, reference)
            if not path.is_file():
                raise ValueError(f"Mesh missing: {reference}")
            vertices, faces = load_mesh(path, cancel)
            geometry_source = {"type": "mesh", "path": path.relative_to(root).as_posix(), "sha256": _sha(path)}
            unit_default = None
        unit = item.get("unit", unit_default)
        if unit is None:
            unit = item.get("units")
        factor = UNITS.get(unit)
        scale = np.asarray(item.get("scale", [1., 1., 1.]), dtype=float)
        if scale.ndim == 0:
            scale = np.repeat(scale, 3)
        if scale.shape != (3,) or not np.isfinite(scale).all() or np.any(scale <= 0):
            raise ValueError("Asset scale must be three positive finite values")
        vertices = vertices * scale * (factor if factor is not None else 1.)
        known_units = factor is not None
        measurements_unit = "m" if known_units else "asset_units_unknown"
        center, values, basis, area, degenerate = _surface_pca(vertices, faces)
        topology = _topology(faces)
        projected = (vertices - center) @ basis
        limits = [projected.min(axis=0), projected.max(axis=0)]
        axial = limits[1][0] - limits[0][0]
        cross_sections = []
        for fraction in np.linspace(.06, .94, 9):
            cancel()
            position = limits[0][0] + axial * fraction
            origin = center + basis[:, 0] * position
            result = section(vertices, faces, origin, basis[:, 0], basis[:, 1], basis[:, 2])
            result.update(label=f"PCA axis s={fraction:.2f}", plane_origin=origin.tolist(), plane_axis=basis[:, 0].tolist(), transverse_basis=[basis[:, 1].tolist(), basis[:, 2].tolist()], axial_fraction=float(fraction))
            cross_sections.append(result)
        voids = [result for result in cross_sections if result.get("center_void_candidate") and result["status"] == "closed_contour_candidates"]
        any_holes = any(result["hole_count"] > 0 for result in cross_sections)
        transverse_ratio = float(values[1] / max(values[2], 1e-30))
        roll_ambiguity = transverse_ratio < 1.2
        collision = item.get("collision", {})
        collision_type = collision.get("type", "unassigned") if isinstance(collision, dict) else str(collision)
        asset_issues = []
        if not known_units:
            asset_issues.append(_issue("metric_scale_unknown", "Mesh numeric bounds do not establish physical units. Supply measured/CAD scale before metric contact or scene placement.", severity="blocking", asset_id=identifier))
        if any_holes:
            asset_issues.append(_issue("single_convex_hull_would_fill_voids", "Closed nested contours reveal cavity candidates. A single convex hull fills such free space; use separately reviewed compound/decomposition geometry preserving functional clearance.", severity="blocking" if collision_type in {"convex_hull", "convexHull", "convex"} else "review", category="physics_contact", asset_id=identifier))
        if roll_ambiguity:
            asset_issues.append(_issue("grasp_roll_ambiguity", "Nearly equal transverse surface PCA moments leave axial roll poorly constrained. Preserve demonstration/table approach and verify opposing jaw patches; do not rotate a down-grasp to a side-grasp from PCA alone.", category="planning_control", asset_id=identifier))
        if not topology["closed_edge_topology"]:
            asset_issues.append(_issue("mesh_topology_open_or_nonmanifold", "Mesh edge topology has gaps/nonmanifold edges; sections and volume/contact claims require separate repair/validation.", asset_id=identifier))
        asset_issues.append(_issue("functional_frame_unverified", "PCA axes, contours and grasp regions are geometric candidates; assembly axis, keyed roll, grasp accessibility and hidden lock remain unverified.", asset_id=identifier))
        grasp_candidates = _grasp_candidates(identifier, vertices, faces, center, basis, limits, measurements_unit)
        probe = _profile_probe(vertices, faces, item, profile) if known_units else None
        if probe:
            if not np.allclose(scale, 1., atol=1e-12, rtol=0.):
                asset_issues.append(_issue("engineering_profile_scale_mismatch", "Mesh has an additional non-unit scale while engineering collision dimensions remain fixed. Revalidate the mapping; functional dimensions are not rescaled automatically.", severity="blocking", asset_id=identifier))
            cross_sections.extend(probe["scan_sections"])
            probe = dict(probe)
            probe.pop("scan_sections")
            if any(abs(value) > 1e-6 for value in probe.get("radius_difference_m", [])):
                difference = max(abs(value) for value in probe["radius_difference_m"])
                issue = _issue("engineering_profile_geometry_difference", f"Mesh section area-equivalent radius differs from the declared engineering radius by up to {difference:.6g}m. This diagnostic is not a CAD tolerance test; preserve both sources and do not resize the functional model automatically.", asset_id=identifier)
                asset_issues.append(issue)
        safe_name = "asset_" + str(index)
        preview = output / f"{safe_name}_sections.svg"
        _svg(preview, identifier + " | functional mesh audit", cross_sections, basis, [vertices.min(axis=0), vertices.max(axis=0)], measurements_unit)
        previews.append(preview.relative_to(root).as_posix())
        numerical_sections = []
        for result in cross_sections:
            result = dict(result)
            result.pop("segments", None)
            result["loops"] = [{key: value for key, value in record.items() if key != "points"} for record in result["loops"]]
            numerical_sections.append(result)
        asset = {"id": identifier, "asset_path": path.relative_to(root).as_posix() if path else None, "status": "analyzed_candidates", "geometry_source": geometry_source, "mesh_sha256": _sha(path) if path else None, "unit_declaration": unit, "measurements_unit": measurements_unit, "metric_scale_verified": False, "unit_source": item.get("unit_source") or item.get("provenance", {}).get("unit") or ("explicit_procedural_size_m" if procedural else "explicit_task_declaration" if known_units else "unknown"), "applied_scale": scale.tolist(), "bounds": [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()], "surface_area": float(area), "surface_centroid": center.tolist(), "vertex_count": len(vertices), "triangle_count": len(faces), "degenerate_triangles": degenerate, "topology": topology, "principal_axes_candidates": [{"axis": basis[:, number].tolist(), "surface_moment": float(values[number]), "extent": float(limits[1][number] - limits[0][number]), "status": "geometric_candidate_not_functional_datum"} for number in range(3)], "roll": {"status": "unresolved", "transverse_moment_ratio": transverse_ratio, "geometric_ambiguity_detected": roll_ambiguity, "keyed_datum_verified": False}, "functional_candidates": {"axial_void": {"status": "candidate" if len(voids) >= 3 else "not_established", "supporting_sections": len(voids), "sampled_axial_span_fraction": [voids[0]["axial_fraction"], voids[-1]["axial_fraction"]] if voids else None, "through_bore_proven": False}, "concave_contact": {"nested_contours_found": any_holes, "single_convex_hull_recommended": False if any_holes else None, "collision_validated": False}, "grasp_regions": grasp_candidates, "profile_feature_correspondence": probe}, "sections": numerical_sections, "preview_path": previews[-1], "issues": asset_issues, "hardware_verified": False, "asset_changed": False}
        report_assets.append(asset)
        issues.extend(asset_issues)
        emit("progress", {"unit": "assets", "completed": index + 1, "total": len(supplied), "asset_id": identifier})
    correspondence = {"status": "not_established", "hardware_verified": False}
    paired = {item["functional_candidates"]["profile_feature_correspondence"]["role"]: item["functional_candidates"]["profile_feature_correspondence"] for item in report_assets if item["functional_candidates"].get("profile_feature_correspondence")}
    if all(role in paired and paired[role]["line_angle_deg_mod180"] is not None for role in ("male", "female")):
        relative = (paired["female"]["line_angle_deg_mod180"] - paired["male"]["line_angle_deg_mod180"]) % 180
        declared = profile.get("roll_candidates_deg", [])
        differences = [abs((relative - float(angle) + 90) % 180 - 90) for angle in declared]
        correspondence = {"status": "visual_line_correspondence_candidate", "relative_line_roll_deg_mod180": relative, "profile_declared_roll_candidates_deg": declared, "closest_declared_line_error_deg": min(differences) if differences else None, "within_5deg_visual_review_band": bool(differences and min(differences) <= 5), "mechanical_180deg_symmetry_verified": False, "lock_stroke_verified": False, "hardware_verified": False, "scope": "Two opposed contour features define an unoriented line only. Agreement with a declared engineering profile does not verify physical key fit, hidden latch or lock stroke."}
    report = {"schema_version": 1, "module_version": VERSION, "status": "completed" if any(item.get("status") == "analyzed_candidates" for item in report_assets) and all(item.get("status") != "unresolved" for item in report_assets) else "needs_assets", "quality_state": "review_required", "assets": report_assets, "physics_profile_provenance": provenance, "pair_feature_correspondence": correspondence, "experience_review": reuse, "issues": issues, "functional_frames_frozen": False, "geometry_or_parameters_modified": False, "hardware_verified": False, "preview_paths": previews}
    dependency = ctx.get("dependencies", {}).get("task_spec", {})
    report["task_spec_hash"] = inputs.get("spec_hash") or dependency.get("spec_hash")
    report["spec_revision"] = inputs.get("spec_revision") or dependency.get("spec_revision")
    if dependency.get("spec_path"):
        snapshot = _local(root, dependency["spec_path"])
        report["task_spec_path"] = snapshot.relative_to(root).as_posix()
        report["task_spec_snapshot_sha256"] = _sha(snapshot)
    target = output / "functional_report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    report["report_path"] = target.relative_to(root).as_posix()
    emit("artifact", {"path": report["report_path"], "kind": "functional_geometry_report"})
    for path in previews:
        emit("artifact", {"path": path, "kind": "functional_section_preview"})
    return report
