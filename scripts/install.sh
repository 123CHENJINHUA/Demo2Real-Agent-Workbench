#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
mkdir -p .runtime/tmp .runtime/pip-cache .runtime/npm-cache
export TMPDIR="$project_dir/.runtime/tmp"
export PYTHONDONTWRITEBYTECODE=1
export PIP_CACHE_DIR="$project_dir/.runtime/pip-cache"
export npm_config_cache="$project_dir/.runtime/npm-cache"
if [[ ! -x .runtime/app-venv/bin/python ]]; then
  python3 -m venv --system-site-packages .runtime/app-venv
fi
.runtime/app-venv/bin/python -m pip install -r requirements-app.txt
node_path="$(.runtime/app-venv/bin/python -c 'from pathlib import Path; from modules.environment.service import resolve; print(resolve(Path.cwd(), "node_executable"))')"
export PATH="$(dirname -- "$node_path"):$PATH"
npm ci --prefix frontend
npm run build --prefix frontend
.runtime/app-venv/bin/python scripts/doctor.py
