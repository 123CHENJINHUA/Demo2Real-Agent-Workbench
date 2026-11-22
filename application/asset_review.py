"""Connect explicitly selected local models to the project-only geometry audit."""
from pathlib import Path

from modules.demo_import import copy_object_models
from modules.geometry import run as audit


def run(ctx):
    inputs = ctx["input"]
    if "model_sources" not in inputs:
        return audit(ctx)
    models = copy_object_models(Path(ctx["root"]).resolve(),Path(ctx["output_dir"]),
                                inputs["model_sources"],ctx["check_cancel"],ctx["emit"])
    return audit(ctx | {"input":inputs | {"object_models":models}})
