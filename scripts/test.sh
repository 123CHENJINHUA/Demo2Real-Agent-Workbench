#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
export TMPDIR="$project_dir/.runtime/tmp"
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
exec "$project_dir/.runtime/app-venv/bin/python" -m pytest tests --basetemp="$project_dir/.runtime/tests/full" "$@"
