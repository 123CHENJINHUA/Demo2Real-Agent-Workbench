"""Isolated worker input/output contract; paths point inside the project."""
from typing import TypedDict, Callable, Any
from pathlib import Path

class ModuleContext(TypedDict):
    root: Path
    run_id: str
    output_dir: Path
    input: dict
    config: dict
    emit: Callable[[str, dict], None]
    check_cancel: Callable[[], None]

SCHEMA_VERSION = "workbench-module/v1"
