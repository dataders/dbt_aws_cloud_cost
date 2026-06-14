#!/usr/bin/env bash
# Mint a short-lived Horizon access token from your Snowflake KEY PAIR and write
# it to HORIZON_ACCESS_TOKEN in .env. The DuckDB horizon catalog uses it as a
# static bearer token. NOTE: a Snowflake PAT can READ via the Horizon Iceberg
# REST catalog but cannot WRITE (createTable 403) — key-pair auth works for both.
# Re-run before `dbt run` if the token has expired (~55 min lifetime).
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

command -v uv >/dev/null 2>&1 || {
  printf '%s\n' "missing required command: uv" >&2
  exit 1
}

uv run "$ROOT/scripts/snowflake_sql_api.py" refresh-horizon-token
