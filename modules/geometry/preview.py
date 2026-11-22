"""Review scene proxies with the same geometry checks as explicit primitives."""
import json
from pathlib import Path

from .functional import run_functional


def run(ctx):
    result = run_functional(ctx)
    proxy_ids = set(ctx["input"]["preview_proxy_ids"])
    for asset in result["assets"]:
        if asset["id"] in proxy_ids and "geometry_source" in asset:
            asset["geometry_source"] = {
                "type": "multi_view_preview_proxy",
                "size_m": asset["geometry_source"]["size_m"],
                "metric_geometry_verified": False,
                "scope": "approximate_scene_preview_only",
            }
    path = Path(ctx["root"]) / result["report_path"]
    report = {key: value for key, value in result.items() if key != "report_path"}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    temporary.replace(path)
    return result
