#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

export NEXIS_ENV=development
export NEXIS_DATA_ROOT="${NEXIS_DATA_ROOT:-$project_dir/.nexis-data}"
export NEXIS_SESSION_SECRET="${NEXIS_SESSION_SECRET:-local-development-only-change-me}"
export PYTHONPATH="$project_dir/src:$project_dir/web/backend"

python -m uvicorn standalonecad_web.main:app --host 127.0.0.1 --port 8000 --reload &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT

cd web/frontend
npm run dev
