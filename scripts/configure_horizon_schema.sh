#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

command -v uv >/dev/null 2>&1 || {
  printf '%s\n' "missing required command: uv" >&2
  exit 1
}

uv run "$ROOT/scripts/snowflake_sql_api.py" configure-horizon-schema
