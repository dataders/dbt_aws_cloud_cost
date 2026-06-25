#!/usr/bin/env bash
# Start the Apache Polaris quickstart stack for local REST-catalog benchmarking.
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_DIR="$ROOT/.tmp/catalog_benchmarks/polaris"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
COMPOSE_URL="${POLARIS_QUICKSTART_COMPOSE_URL:-https://raw.githubusercontent.com/apache/polaris/refs/heads/main/site/content/guides/quickstart/docker-compose.yml}"
PROJECT_NAME="${POLARIS_LOCAL_COMPOSE_PROJECT:-dbt-aws-cloud-cost-polaris}"

command -v docker >/dev/null 2>&1 || {
  printf '%s\n' "missing required command: docker" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || {
  printf '%s\n' "missing required command: curl" >&2
  exit 1
}

mkdir -p "$COMPOSE_DIR"
curl -fsSL "$COMPOSE_URL" -o "$COMPOSE_FILE"

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps

cat <<EOF

Polaris quickstart stack requested.

Benchmark target defaults:
  endpoint:  http://localhost:8181/api/catalog
  warehouse: quickstart_catalog

Required benchmark env:
  POLARIS_LOCAL_ID
  POLARIS_LOCAL_SECRET

Run:
  uv run scripts/catalog_benchmark.py --target polaris_local --sizes tiny,small

Stop:
  scripts/stop_local_polaris.sh
EOF
