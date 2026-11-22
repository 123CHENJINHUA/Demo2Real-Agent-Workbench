"""Conservative mesh inventory: bounds are evidence, functional features are unknown."""
from __future__ import annotations
import json
import hashlib
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

VERSION = "geometry.v1"


def _mesh(path: Path) -> dict:
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    count = 0
    triangles = 0
    groups = set()
    def vertex(point):
        nonlocal count
        if len(point) != 3 or not all(math.isfinite(x) for x in point):
            raise ValueError("Non-finite or malformed vertex")
        count += 1
        for index, value in enumerate(point):
            mins[index] = min(mins[index], value)
            maxs[index] = max(maxs[index], value)
    if path.suffix.lower() == ".obj":
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "v":
                    vertex([float(x) for x in parts[1:4]])
                elif parts[0] == "f":
                    triangles += max(0, len(parts) - 3)
                elif parts[0] in {"g", "o", "usemtl"}:
                    groups.add(" ".join(parts[1:]))
    elif path.suffix.lower() == ".stl":
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(84)
            number = struct.unpack("<I", header[80:84])[0] if len(header) == 84 else -1
            if 84 + number * 50 == size:
                triangles = number
                for _ in range(number):
                    values = struct.unpack("<12fH", handle.read(50))
                    for offset in (3, 6, 9):
                        vertex(values[offset:offset+3])
            else:
                handle.seek(0)
                for line in handle:
                    parts = line.decode("ascii", "replace").split()
                    if parts and parts[0].lower() == "vertex":
                        vertex([float(x) for x in parts[1:4]])
                triangles = count // 3
    if not count:
        raise ValueError("No readable vertices")
    extent = [b - a for a, b in zip(mins, maxs)]
    return {"vertices_read": count, "triangles": triangles, "bounds": [mins, maxs], "extent_in_asset_units": extent, "groups": sorted(groups)[:100], "unit": "unknown", "functional_axes": "not_inferred", "collision_suitability": "unverified", "warnings": ["AABB does not establish functional axes, grasp frames, holes, keyed orientation or fit.", "Asset units require CAD/measurement/scene metadata; no automatic rescaling or functional repair applied."]}


def run(ctx: dict) -> dict:
    root = Path(ctx["root"]).resolve()
    output = Path(ctx["output_dir"]).resolve()
    if not output.is_relative_to(root):
        raise ValueError("Geometry output outside project")
    output.mkdir(parents=True, exist_ok=True)
    inputs = ctx.get("input", {})
    supplied = inputs.get("asset_paths") or []
    if isinstance(supplied, str):
        supplied = [supplied]
    selected_models = {item["path"]:item["sha256"] for item in inputs.get("object_models", [])}
    supplied = [*supplied, *selected_models]
    selection_scope = "explicit_task_assets" if supplied else "no_task_assets" if "object_models" in inputs else "project_inventory_unbound"
    if selection_scope == "project_inventory_unbound" and (root / "assets").is_dir():
        supplied = ["assets"]
    paths = []
    for value in supplied:
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Geometry assets must be copied inside project; symlinks rejected")
        if not path.exists():
            raise ValueError(f"Asset not found: {value}")
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in candidates:
            if item.is_symlink() or not item.resolve().is_relative_to(root):
                raise ValueError("Symlink or escaped asset rejected")
            if item.is_file() and item.suffix.lower() in {".obj", ".stl", ".urdf", ".usd", ".usda", ".usdc"}:
                paths.append(item)
    paths = list(dict.fromkeys(paths))
    max_files = min(1000, int(ctx.get("input", {}).get("max_assets", 200)))
    if len(paths) > max_files:
        raise ValueError("Asset audit exceeds max_assets budget; select object assets explicitly")
    results = []
    for index, path in enumerate(paths):
        ctx.get("check_cancel", lambda: None)()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                ctx.get("check_cancel", lambda: None)()
                digest.update(chunk)
        item = {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": digest.hexdigest(), "status": "audited"}
        if item["path"] in selected_models and selected_models[item["path"]]!=item["sha256"]:
            raise ValueError("Copied model changed after import: "+item["path"])
        try:
            if path.stat().st_size > 512 * 1024**2:
                raise ValueError("Asset exceeds 512 MiB lightweight audit budget")
            if path.suffix.lower() in {".obj", ".stl"}:
                item.update(_mesh(path))
            elif path.suffix.lower() == ".urdf":
                tree = ET.parse(path)
                refs = []
                for node in tree.findall(".//mesh"):
                    reference = node.attrib.get("filename", "")
                    resolved = (path.parent / reference).resolve()
                    refs.append({"reference": reference, "scale": node.attrib.get("scale"), "exists_inside_project": not reference.startswith("package:") and resolved.is_relative_to(root) and resolved.is_file()})
                item["mesh_references"] = refs
                item["warnings"] = ["URDF geometry origin/scale is recorded, not hardware frame calibration."]
            else:
                item.update(status="needs_runtime_inspection", warnings=["USD composition and physics require configured Isaac runtime; not loaded by lightweight API."])
        except (ValueError, OSError, struct.error, ET.ParseError) as exc:
            item.update(status="failed", error=str(exc))
        results.append(item)
        ctx.get("emit", lambda *args: None)("progress", {"unit": "assets", "completed": index + 1, "total": len(paths), "current_file": item["path"]})
    report = {"schema_version": 1, "module_version": VERSION, "status": "completed" if paths or selection_scope=="no_task_assets" else "needs_assets", "asset_selection_scope": selection_scope, "task_identity_verified": False, "assets": results, "assets_changed": False, "functional_geometry_verified": False, "required_review": ["Record unit/scale and measured mating features", "Separate visual/collision/functional representations", "Validate positive and negative contact examples", "Consult related experience before assigning functional frames"]}
    if "object_models" in inputs:
        report["object_models"] = inputs["object_models"]
    target = output / "geometry_audit.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if ctx.get("input", {}).get("functional_analysis"):
        from .functional import run_functional
        functional_ctx = dict(ctx) | {"output_dir": output / "functional"}
        functional = run_functional(functional_ctx)
        report["functional_report_path"] = functional["report_path"]
        report["preview_paths"] = functional["preview_paths"]
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report | {"report_path": target.relative_to(root).as_posix()}
