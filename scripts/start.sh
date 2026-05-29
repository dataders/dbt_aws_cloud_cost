#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CONTAINER_NAME=${SHADOWTRAFFIC_CONTAINER_NAME:-dbt-aws-cloud-cost-shadowtraffic}
LICENSE_ENV=${SHADOWTRAFFIC_LICENSE_ENV:-/Users/dataders/Developer/dotfiles_env/shadowtraffic/license.env}
POLARIS_ENV=${POLARIS_ENV:-/Users/dataders/Developer/dotfiles_env/secrets.zsh}
RUN_DIR="$ROOT/.tmp"
PARQUET_DIR="$RUN_DIR/shadowtraffic-parquet"
GENERATION_LOG="$RUN_DIR/shadowtraffic-generation.log"
SAMPLE_EVENTS=
SAMPLE_BATCH_SIZE=${SHADOWTRAFFIC_SAMPLE_BATCH_SIZE:-10000}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

require_positive_int() {
  [[ "$2" =~ ^[1-9][0-9]*$ ]] || die "$1 must be a positive integer"
}

redact_file() {
  [ -s "$1" ] || return
  sed -E \
    -e 's/("credential"[[:space:]]*:[[:space:]]*")[^"]+/\1<redacted>/g' \
    -e 's/("client_secret"[[:space:]]*:[[:space:]]*")[^"]+/\1<redacted>/g' \
    -e 's/(POLARIS_SECRET=)[^[:space:]]+/\1<redacted>/g' \
    "$1" >&2
}

load_dotenv() {
  [ -f "$ROOT/.env" ] || return

  local line key
  while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
    line=${line#export }
    key=${line%%=*}
    key=${key%%[[:space:]]*}
    export "$key=$(bash -c 'set -a; source "$1"; printf "%s" "${!2-}"' _ "$ROOT/.env" "$key")"
  done < "$ROOT/.env"
}

load_polaris_env() {
  [ -f "$POLARIS_ENV" ] || return

  local line
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    local key=${line%%=*}
    case "$key" in
      AWS_CLOUD_COST_*|SHADOWTRAFFIC_*)
        if [ -z "${!key+x}" ]; then
          export "$line"
        fi
        ;;
      POLARIS_ENV) ;;
      POLARIS_*)
        if [ -z "${!key+x}" ]; then
          export "$line"
        fi
        ;;
    esac
  done < <(POLARIS_ENV="$POLARIS_ENV" zsh -lc 'source "$POLARIS_ENV"; env | grep -E "^(POLARIS|AWS_CLOUD_COST|SHADOWTRAFFIC)_"')
}

usage() {
  cat <<'MSG'
usage: scripts/start.sh [--sample EVENTS]

Starts ShadowTraffic writing AWS Cost rows into the configured Polaris Iceberg
source table through local Parquet batches and PyIceberg. With --sample, writes
a bounded number of events and exits.

Options:
  --sample EVENTS       Generate and append EVENTS rows, then exit.
  --batch-size EVENTS   Max rows per local ShadowTraffic/PyIceberg batch.
MSG
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sample)
      [ "$#" -ge 2 ] || die "--sample requires an event count"
      SAMPLE_EVENTS=$2
      shift 2
      ;;
    --batch-size)
      [ "$#" -ge 2 ] || die "--batch-size requires an event count"
      SAMPLE_BATCH_SIZE=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_command docker
require_command uv
require_command zsh
load_dotenv
load_polaris_env

[ -f "$LICENSE_ENV" ] || die "missing ShadowTraffic license env: $LICENSE_ENV"

run_batch() {
  local event_count=$1
  rm -rf "$PARQUET_DIR"
  mkdir -p "$PARQUET_DIR"
  export SHADOWTRAFFIC_MAX_EVENTS=$event_count

  printf 'generating %s ShadowTraffic events as local Parquet\n' "$event_count"
  RENDERED_CONFIG=$(uv run python "$ROOT/scripts/polaris_shadowtraffic.py" render-config)

  sample_status=0
  docker rm -f "$CONTAINER_NAME-sample" >/dev/null 2>&1 || true
  docker run \
    --name "$CONTAINER_NAME-sample" \
    --env-file "$LICENSE_ENV" \
    -v "$RENDERED_CONFIG:/home/config.json:ro" \
    shadowtraffic/shadowtraffic:latest \
    --config /home/config.json \
    --sample "$event_count" \
    --quiet \
    > "$GENERATION_LOG" \
    2>&1 || sample_status=$?

  if [ "$sample_status" -ne 0 ] || grep -qE 'Exception|✘' "$GENERATION_LOG"; then
    redact_file "$GENERATION_LOG"
    die "ShadowTraffic local Parquet generation failed"
  fi

  docker cp "$CONTAINER_NAME-sample:/home/output/." "$PARQUET_DIR/"
  docker rm -f "$CONTAINER_NAME-sample" >/dev/null

  uv run --with 'pyiceberg[s3fs]' --with pyarrow python "$ROOT/scripts/polaris_shadowtraffic.py" append-parquet "$PARQUET_DIR"
}

run_sample() {
  local remaining=$1
  local total=$1
  local batch_size=$2
  local current_batch
  local completed=0

  require_positive_int "--sample" "$total"
  require_positive_int "--batch-size" "$batch_size"

  while [ "$remaining" -gt 0 ]; do
    current_batch=$batch_size
    if [ "$remaining" -lt "$current_batch" ]; then
      current_batch=$remaining
    fi
    run_batch "$current_batch"
    completed=$((completed + current_batch))
    remaining=$((remaining - current_batch))
    printf 'sample progress: %s/%s events appended\n' "$completed" "$total"
  done
}

printf 'source: polaris.%s.%s\n' "${AWS_CLOUD_COST_SOURCE_SCHEMA:-${POLARIS_NAMESPACE:-aws_cloud_cost}}" "${AWS_CLOUD_COST_SOURCE_TABLE:-${POLARIS_TABLE:-aws_cost_report}}"

if [ -n "$SAMPLE_EVENTS" ]; then
  run_sample "$SAMPLE_EVENTS" "$SAMPLE_BATCH_SIZE"
  printf 'sampled %s events into polaris.%s.%s\n' "$SAMPLE_EVENTS" "${AWS_CLOUD_COST_SOURCE_SCHEMA:-${POLARIS_NAMESPACE:-aws_cloud_cost}}" "${AWS_CLOUD_COST_SOURCE_TABLE:-${POLARIS_TABLE:-aws_cost_report}}"
else
  batch_events=${SHADOWTRAFFIC_BATCH_EVENTS:-100}
  batch_interval=${SHADOWTRAFFIC_BATCH_INTERVAL_SECONDS:-30}
  printf 'streaming local Parquet batches into polaris.%s.%s every %s seconds\n' "${AWS_CLOUD_COST_SOURCE_SCHEMA:-${POLARIS_NAMESPACE:-aws_cloud_cost}}" "${AWS_CLOUD_COST_SOURCE_TABLE:-${POLARIS_TABLE:-aws_cost_report}}" "$batch_interval"
  while true; do
    run_batch "$batch_events"
    sleep "$batch_interval"
  done
fi
