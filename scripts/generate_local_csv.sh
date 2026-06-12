#!/usr/bin/env bash
# Generate the local CSV source for the demo using ShadowTraffic.
# Usage: scripts/generate_local_csv.sh [ROWS]   (default 10000)
#
# Produces local_files/aws_cost_report.csv with a single uniform _modified
# value so every row counts as the latest file version (mirrors a single
# Fivetran file export). No Polaris / external source catalog is involved.
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ROWS=${1:-10000}
# These defaults assume `direnv allow` already loaded .env (run scripts/setup_env.sh
# first). Override any of them if you are not using direnv.
DOTFILES_ENV=${DOTFILES_ENV:-$HOME/Developer/dotfiles_env}
LICENSE_ENV=${SHADOWTRAFFIC_LICENSE_ENV:-$DOTFILES_ENV/shadowtraffic/license.env}
DUCKDB_CLI=${DUCKDB_CLI:-${DUCKDB_BUILD_DIR:+$DUCKDB_BUILD_DIR/build/debug/duckdb}}
DUCKDB_HOME=${DUCKDB_HOME:-$ROOT/.tmp/duckdb-home}
MODIFIED_AT=${AWS_CLOUD_COST_MODIFIED_AT:-2026-05-28 00:00:00.000}
CONTAINER=dbt-aws-cloud-cost-localcsv

command -v docker >/dev/null || { echo "missing docker" >&2; exit 1; }
command -v uv >/dev/null || { echo "missing uv" >&2; exit 1; }
[ -f "$LICENSE_ENV" ] || { echo "missing ShadowTraffic license env: $LICENSE_ENV" >&2; exit 1; }
[ -n "$DUCKDB_CLI" ] && [ -x "$DUCKDB_CLI" ] || {
  echo "missing DuckDB CLI: set DUCKDB_CLI (or DUCKDB_BUILD_DIR), or run scripts/setup_env.sh + 'direnv allow' first" >&2
  exit 1
}

PARQUET_DIR="$(mktemp -d "$ROOT/.tmp/localcsv-XXXXXX")"
trap 'rm -rf "$PARQUET_DIR"' EXIT

export SHADOWTRAFFIC_MAX_EVENTS=$ROWS
RENDERED_CONFIG=$(uv run python "$ROOT/scripts/polaris_shadowtraffic.py" render-config)

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
echo "generating $ROWS rows with ShadowTraffic..."
docker run --name "$CONTAINER" --env-file "$LICENSE_ENV" \
  -v "$RENDERED_CONFIG:/home/config.json:ro" \
  shadowtraffic/shadowtraffic:latest --config /home/config.json --sample "$ROWS" --quiet
docker cp "$CONTAINER:/home/output/." "$PARQUET_DIR/" >/dev/null
docker rm -f "$CONTAINER" >/dev/null

mkdir -p "$ROOT/local_files"
DUCKDB_HOME="$DUCKDB_HOME" "$DUCKDB_CLI" -unsigned :memory: -c "
COPY (
  SELECT * REPLACE ('$MODIFIED_AT' AS _modified)
  FROM read_parquet('$PARQUET_DIR/*.parquet')
) TO '$ROOT/local_files/aws_cost_report.csv' (HEADER, DELIMITER ',');"
echo "wrote local_files/aws_cost_report.csv ($ROWS rows, uniform _modified=$MODIFIED_AT)"
