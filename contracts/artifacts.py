"""Project-relative locations for run and experiment artifacts."""
from pathlib import Path
import re


def run_directory(run):
    run_id = run["id"]
    demo_id = run["input"].get("demo_run_id")
    if demo_id and demo_id != run_id:
        return Path("runs", demo_id, "derived", run_id)
    return Path("runs", run_id)


def run_dependencies(inputs):
    """Read run references from lineage fields and project-relative input paths."""
    lineage = ("demo_run_id", "source_run_id", "scene_run_id", "previous_scene_run_id")
    identifiers = {inputs[key] for key in lineage
                   if isinstance(inputs.get(key), str) and re.fullmatch(r"[0-9a-f]{16}", inputs[key])}
    pending = list(inputs.values())
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            match = re.match(r"^(?:\./)?runs/([0-9a-f]{16})(?:/derived/([0-9a-f]{16}))?(?:/|$)", value)
            if match:
                identifiers.update(identifier for identifier in match.groups() if identifier)
    return identifiers


def batch_directory(root, batch_id):
    """Find a grouped batch or a batch written before demo grouping."""
    if not re.fullmatch(r"placement_[0-9]+", batch_id):
        raise ValueError("Invalid placement batch ID")
    runs = Path(root).resolve() / "runs"
    paths = [runs / batch_id, *runs.glob(f"*/experiments/{batch_id}")]
    matches = [path for path in paths if path.is_dir() and path.resolve() == path]
    if len(matches) != 1:
        raise ValueError("Placement batch is missing or ambiguous: " + batch_id)
    return matches[0]
