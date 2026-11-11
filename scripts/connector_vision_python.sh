#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$project_dir/.runtime/tmp"
cd "$project_dir"
exec "$project_dir/.runtime/app-venv/bin/python" "$project_dir/scripts/launch_vision.py" "$@"
