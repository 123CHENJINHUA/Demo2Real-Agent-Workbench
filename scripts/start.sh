#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
export TMPDIR="$project_dir/.runtime/tmp"
export PYTHONDONTWRITEBYTECODE=1
exec "$project_dir/.runtime/app-venv/bin/python" "$project_dir/scripts/serve.py" "$@"
