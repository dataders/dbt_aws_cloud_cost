#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
POLARIS_ENV=${POLARIS_ENV:-/Users/dataders/Developer/dotfiles_env/secrets.zsh}
DROP_TABLE=false

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'MSG'
usage: scripts/stop.sh [--drop-table]

Cleans local ShadowTraffic batch files. With --drop-table, also drops the
configured Polaris source table.
MSG
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

while [ "$#" -gt 0 ]; do
  case "$1" in
    --drop-table)
      DROP_TABLE=true
      shift
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

load_dotenv
load_polaris_env

rm -rf "$ROOT/.tmp/shadowtraffic-polaris-config.json" "$ROOT/.tmp/shadowtraffic-events.json" "$ROOT/.tmp/shadowtraffic-generation.log" "$ROOT/.tmp/shadowtraffic-parquet"
printf 'cleaned local ShadowTraffic batch files\n'
if [ "$DROP_TABLE" = "true" ]; then
  command -v uv >/dev/null 2>&1 || die "missing required command: uv"
  uv run --with 'pyiceberg[s3fs]' --with pyarrow python "$ROOT/scripts/polaris_shadowtraffic.py" drop-table
else
  printf 'left source table in place: %s.%s.%s\n' "${POLARIS_WAREHOUSE:-polaris}" "${AWS_CLOUD_COST_SOURCE_SCHEMA:-${POLARIS_NAMESPACE:-aws_cloud_cost}}" "${AWS_CLOUD_COST_SOURCE_TABLE:-${POLARIS_TABLE:-aws_cost_report}}"
fi
