#!/usr/bin/env bash
# Stop the Apache Polaris quickstart stack used by local catalog benchmarks.
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_FILE="$ROOT/.tmp/catalog_benchmarks/polaris/docker-compose.yml"
PROJECT_NAME="${POLARIS_LOCAL_COMPOSE_PROJECT:-dbt-aws-cloud-cost-polaris}"

if [ ! -f "$COMPOSE_FILE" ]; then
  printf '%s\n' "no local Polaris compose file at $COMPOSE_FILE"
  exit 0
fi

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down -v
