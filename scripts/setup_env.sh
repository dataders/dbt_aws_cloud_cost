#!/usr/bin/env bash
# Generate .env for the dbt multi-catalog demo.
#
# Reads credentials from a developer's private dotfiles_env checkout and the two
# locally built debug binaries this demo depends on, then writes ./.env (consumed
# by .envrc / direnv). Every input is overridable via the env var named below; a
# colleague on a different machine should export the ones that differ before
# running this script. See README.md ("One-time setup") for what each path is.
#
# Required, no safe default (export these or the script will tell you what is
# missing):
#   DBT_BIN            path to the custom Fusion `dbt` debug binary
#   DUCKDB_BUILD_DIR   path to the patched duckdb-iceberg build dir
#
# Credential sources (default to the maintainer's dotfiles_env layout; override
# to point at your own):
#   SNOWFLAKE_CREDENTIALS_JSON, SHADOWTRAFFIC_LICENSE_ENV, POLARIS_ENV
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DOTFILES_ENV=${DOTFILES_ENV:-$HOME/Developer/dotfiles_env}
CREDENTIALS_JSON=${SNOWFLAKE_CREDENTIALS_JSON:-$DOTFILES_ENV/credentials/fusion.env.json}
LICENSE_ENV=${SHADOWTRAFFIC_LICENSE_ENV:-$DOTFILES_ENV/shadowtraffic/license.env}
POLARIS_ENV=${POLARIS_ENV:-$DOTFILES_ENV/secrets.zsh}
FS_DBT_BIN=${DBT_BIN:-}
DUCKDB_BUILD_DIR=${DUCKDB_BUILD_DIR:-}
# DUCKDB_DRIVER_LIB / DUCKDB_CLI / DUCKDB_EXTENSION_REPOSITORY are derived from
# DUCKDB_BUILD_DIR below, after we have a chance to recover both binary paths
# from a previous .env (so re-running this script does not require re-exporting).
DUCKDB_DRIVER_LIB=${DUCKDB_DRIVER_LIB:-}
DUCKDB_CLI=${DUCKDB_CLI:-}
DUCKDB_EXTENSION_REPOSITORY=${DUCKDB_EXTENSION_REPOSITORY:-}
DUCKDB_HOME=${DUCKDB_HOME:-$ROOT/.tmp/duckdb-home}
ADBC_REPOSITORY=${ADBC_REPOSITORY:-$ROOT/.tmp/adbc-lib}
SNOWFLAKE_ADBC_DRIVER_VERSION=${SNOWFLAKE_ADBC_DRIVER_VERSION:-0.21.0.dev+dbt0.21.13}
DISABLE_CDN_DRIVER_CACHE=${DISABLE_CDN_DRIVER_CACHE:-true}
DISABLE_AUTO_DRIVER_REBUILD=${DISABLE_AUTO_DRIVER_REBUILD:-true}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

quote_env() {
  printf "'"
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

load_existing_env_var() {
  local name=$1
  local value
  if [ "${!name+x}" ]; then
    return
  fi
  [ -f "$ROOT/.env" ] || return 0
  value=$(set +u; source "$ROOT/.env"; printf '%s' "${!name-}")
  [ -n "$value" ] || return 0
  printf -v "$name" '%s' "$value"
}

load_private_catalog_env() {
  [ -f "$POLARIS_ENV" ] || return 0

  local line key
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    key=${line%%=*}
    case "$key" in
      AWS_CLOUD_COST_*|DATABRICKS_*)
        [ -z "${!key+x}" ] || continue
        printf -v "$key" '%s' "${line#*=}"
        ;;
      POLARIS_*)
        printf -v "$key" '%s' "${line#*=}"
        ;;
    esac
  done < <(POLARIS_ENV="$POLARIS_ENV" zsh -lc 'source "$POLARIS_ENV"; env | grep -E "^(AWS_CLOUD_COST|DATABRICKS|POLARIS)_" || true')
}

jq_optional() {
  jq -er "$1 // empty" "$CREDENTIALS_JSON" 2>/dev/null || true
}

write_optional_env() {
  local name=$1
  local value=${!name-}
  [ -n "$value" ] || return 0
  printf '%s=%s\n' "$name" "$(quote_env "$value")"
}

require_command jq

# Recover binary paths from a previous .env so re-running does not force the
# colleague to re-export DBT_BIN / DUCKDB_BUILD_DIR every time.
if [ -z "$FS_DBT_BIN" ]; then
  unset DBT_BIN
  load_existing_env_var DBT_BIN
  FS_DBT_BIN=${DBT_BIN:-}
fi
if [ -z "$DUCKDB_BUILD_DIR" ]; then
  unset DUCKDB_BUILD_DIR
  load_existing_env_var DUCKDB_BUILD_DIR
  DUCKDB_BUILD_DIR=${DUCKDB_BUILD_DIR:-}
fi

[ -n "$FS_DBT_BIN" ] || die "DBT_BIN is not set.
  Build the custom Fusion dbt binary, then point DBT_BIN at it, e.g.:
    cd <your fs worktree> && cargo build --bin dbt
    export DBT_BIN=<your fs worktree>/target/debug/dbt
  See README.md > 'Build the two local binaries'."
[ -n "$DUCKDB_BUILD_DIR" ] || die "DUCKDB_BUILD_DIR is not set.
  Build the patched duckdb-iceberg debug build, then point DUCKDB_BUILD_DIR at it, e.g.:
    cd <your duckdb-iceberg worktree> && make debug
    export DUCKDB_BUILD_DIR=<your duckdb-iceberg worktree>
  See README.md > 'Build the two local binaries'."

# Derive the DuckDB artifact paths from the build dir unless overridden.
DUCKDB_DRIVER_LIB=${DUCKDB_DRIVER_LIB:-$DUCKDB_BUILD_DIR/build/debug/src/libduckdb.dylib}
DUCKDB_CLI=${DUCKDB_CLI:-$DUCKDB_BUILD_DIR/build/debug/duckdb}
DUCKDB_EXTENSION_REPOSITORY=${DUCKDB_EXTENSION_REPOSITORY:-$DUCKDB_BUILD_DIR/build/debug/repository}

[ -f "$CREDENTIALS_JSON" ] || die "missing Snowflake credentials json: $CREDENTIALS_JSON
  Override with SNOWFLAKE_CREDENTIALS_JSON=/path/to/creds.json (see README.md > 'Credentials')."
[ -f "$LICENSE_ENV" ] || die "missing ShadowTraffic license env: $LICENSE_ENV
  Override with SHADOWTRAFFIC_LICENSE_ENV=/path/to/license.env (see README.md > 'Credentials')."
[ -x "$FS_DBT_BIN" ] || die "DBT_BIN is not an executable file: $FS_DBT_BIN
  Did you run 'cargo build --bin dbt' in the fs worktree?"
[ -x "$DUCKDB_CLI" ] || die "missing executable DuckDB CLI: $DUCKDB_CLI
  Did you run 'make debug' in the duckdb-iceberg worktree (DUCKDB_BUILD_DIR=$DUCKDB_BUILD_DIR)?"
[ -f "$DUCKDB_DRIVER_LIB" ] || die "missing DuckDB driver library: $DUCKDB_DRIVER_LIB
  Did you run 'make debug' in the duckdb-iceberg worktree (DUCKDB_BUILD_DIR=$DUCKDB_BUILD_DIR)?"
[ -d "$DUCKDB_EXTENSION_REPOSITORY" ] || die "missing DuckDB extension repository: $DUCKDB_EXTENSION_REPOSITORY
  Did you run 'make debug' in the duckdb-iceberg worktree (DUCKDB_BUILD_DIR=$DUCKDB_BUILD_DIR)?"
mkdir -p "$ADBC_REPOSITORY"
ln -sfn "$DUCKDB_DRIVER_LIB" "$ADBC_REPOSITORY/duckdb"
[ -f "$ADBC_REPOSITORY/duckdb" ] || die "missing local DuckDB ADBC driver link: $ADBC_REPOSITORY/duckdb"

link_cached_snowflake_driver() {
  local cache_root driver_path driver_link suffix
  cache_root=${DBT_ADBC_CACHE_ROOT:-$HOME/Library/Caches/com.getdbt/adbc}
  case "$(uname -s)" in
    Darwin) suffix=dylib ;;
    Linux) suffix=so ;;
    *) return 0 ;;
  esac

  driver_path=
  for candidate in "$cache_root"/*/"libadbc_driver_snowflake-$SNOWFLAKE_ADBC_DRIVER_VERSION.$suffix"; do
    [ -f "$candidate" ] || continue
    driver_path=$candidate
    break
  done

  if [ -z "$driver_path" ]; then
    printf 'warning: cached Snowflake ADBC driver %s not found under %s\n' "$SNOWFLAKE_ADBC_DRIVER_VERSION" "$cache_root" >&2
    printf 'warning: run once with DISABLE_CDN_DRIVER_CACHE=false to populate the cache, or set SNOWFLAKE_ADBC_DRIVER_VERSION\n' >&2
    return 0
  fi

  driver_link="$ADBC_REPOSITORY/libadbc_driver_snowflake.$suffix"
  ln -sfn "$driver_path" "$driver_link"
  [ -f "$driver_link" ] || die "missing Snowflake ADBC driver link: $driver_link"
}

link_cached_snowflake_driver

require_duckdb_extension() {
  local extension_name=$1
  local extension_path
  for extension_path in "$DUCKDB_EXTENSION_REPOSITORY"/*/*/"$extension_name.duckdb_extension"; do
    [ -f "$extension_path" ] && return 0
  done
  die "missing $extension_name.duckdb_extension under $DUCKDB_EXTENSION_REPOSITORY"
}

require_duckdb_extension httpfs
require_duckdb_extension iceberg

bootstrap_demo_schemas() {
  mkdir -p "$ROOT/.tmp" "$DUCKDB_HOME"

  DUCKDB_HOME="$DUCKDB_HOME" "$DUCKDB_CLI" -unsigned "$ROOT/.tmp/aws_cloud_cost.duckdb" \
    -c 'create schema if not exists aws_cloud_cost;' >/dev/null
  printf 'ensured builtin schema: aws_cloud_cost\n'

  if ! command -v curl >/dev/null 2>&1; then
    printf 'warning: curl not found; skipped lakekeeper schema bootstrap\n' >&2
    return 0
  fi

  if ! curl -fsS --max-time 2 'http://localhost:18181/catalog/v1/config?warehouse=demo' >/dev/null 2>&1; then
    printf 'warning: lakekeeper not reachable at http://localhost:18181; skipped lakekeeper schema bootstrap\n' >&2
    return 0
  fi

  DUCKDB_HOME="$DUCKDB_HOME" "$DUCKDB_CLI" -unsigned :memory: \
    -c "ATTACH 'demo' AS lakekeeper (TYPE ICEBERG, ENDPOINT 'http://localhost:18181/catalog', AUTHORIZATION_TYPE 'NONE', ACCESS_DELEGATION_MODE 'NONE'); create schema if not exists lakekeeper.aws_cloud_cost;" >/dev/null
  printf 'ensured lakekeeper schema: aws_cloud_cost\n'
}

for existing_env_name in \
  AWS_ACCESS_KEY_ID \
  AWS_CLOUD_COST_SOURCE_CATALOG \
  AWS_CLOUD_COST_SOURCE_SCHEMA \
  AWS_CLOUD_COST_SOURCE_TABLE \
  AWS_CLOUD_COST_TARGET_SCHEMA \
  AWS_DEFAULT_REGION \
  AWS_REGION \
  AWS_S3_TABLES_BUCKET_NAME \
  AWS_S3_TABLES_NAMESPACE \
  AWS_S3_TABLES_WAREHOUSE \
  AWS_SECRET_ACCESS_KEY \
  AWS_SESSION_TOKEN \
  DATABRICKS_CATALOG \
  DATABRICKS_DEFAULT_REGION \
  DATABRICKS_HOST \
  DATABRICKS_SCHEMA \
  DATABRICKS_TOKEN \
  HORIZON_ENDPOINT \
  HORIZON_WAREHOUSE \
  HORIZON_SCHEMA \
  HORIZON_CLIENT_ID \
  HORIZON_CLIENT_SECRET \
  HORIZON_PAT \
  HORIZON_OAUTH2_SERVER_URI \
  HORIZON_OAUTH2_SCOPE \
  POLARIS_ACCESS_DELEGATION_MODE \
  POLARIS_DEFAULT_REGION \
  POLARIS_ID \
  POLARIS_NAMESPACE \
  POLARIS_OAUTH_SCOPE \
  POLARIS_OAUTH_TOKEN_URI \
  POLARIS_SECRET \
  POLARIS_TABLE \
  POLARIS_URL \
  POLARIS_WAREHOUSE
do
  load_existing_env_var "$existing_env_name"
done
load_private_catalog_env

SNOWFLAKE_ACCOUNT=$(jq -er '.snowflakeAccount' "$CREDENTIALS_JSON")
SNOWFLAKE_USER=$(jq -er '.snowflakeUsername' "$CREDENTIALS_JSON")
SNOWFLAKE_ROLE=$(jq -er '.snowflakeRole' "$CREDENTIALS_JSON")
SNOWFLAKE_DATABASE=$(jq -er '.snowflakeDatabase' "$CREDENTIALS_JSON")
SNOWFLAKE_WAREHOUSE=$(jq -er '.snowflakeWarehouse' "$CREDENTIALS_JSON")
SNOWFLAKE_PRIVATE_KEY=$(jq -er '.snowflakePrivateKey' "$CREDENTIALS_JSON")
DATABRICKS_HOST=${DATABRICKS_HOST:-$(jq_optional '.dbtDatabricksHostname')}
DATABRICKS_TOKEN=${DATABRICKS_TOKEN:-$(jq_optional '.dbtDatabricksToken')}
DATABRICKS_CATALOG=${DATABRICKS_CATALOG:-$(jq_optional '.dbtDatabricksCatalog')}
DATABRICKS_SCHEMA=${DATABRICKS_SCHEMA:-aws_cloud_cost}
DATABRICKS_DEFAULT_REGION=${DATABRICKS_DEFAULT_REGION:-us-west-2}
case "$DATABRICKS_HOST" in
  ""|http://*|https://*) ;;
  *) DATABRICKS_HOST="https://$DATABRICKS_HOST" ;;
esac

if [ "${AWS_CLOUD_COST_SNOWFLAKE_ROLE+x}" ]; then
  SNOWFLAKE_ROLE=$AWS_CLOUD_COST_SNOWFLAKE_ROLE
fi
if [ -n "$SNOWFLAKE_ROLE" ]; then
  SNOWFLAKE_ROLE=$(printf '%s' "$SNOWFLAKE_ROLE" | tr '[:lower:]' '[:upper:]')
fi
SNOWFLAKE_DATABASE=$(printf '%s' "$SNOWFLAKE_DATABASE" | tr '[:lower:]' '[:upper:]')
SNOWFLAKE_WAREHOUSE=$(printf '%s' "$SNOWFLAKE_WAREHOUSE" | tr '[:lower:]' '[:upper:]')
SNOWFLAKE_SCHEMA=${AWS_CLOUD_COST_SCHEMA:-AWS_CLOUD_COST}
SNOWFLAKE_TABLE=${AWS_CLOUD_COST_TABLE:-AWS_COST_REPORT}
SNOWFLAKE_SQL_API_HOST=${SNOWFLAKE_SQL_API_HOST:-$SNOWFLAKE_ACCOUNT.snowflakecomputing.com}
HORIZON_ENDPOINT=${HORIZON_ENDPOINT:-https://$SNOWFLAKE_SQL_API_HOST/polaris/api/catalog}
HORIZON_WAREHOUSE=${HORIZON_WAREHOUSE:-$SNOWFLAKE_DATABASE}
HORIZON_SCHEMA=${HORIZON_SCHEMA:-$SNOWFLAKE_SCHEMA}
HORIZON_CLIENT_ID=${HORIZON_CLIENT_ID:-snowflake}
HORIZON_CLIENT_SECRET=${HORIZON_CLIENT_SECRET:-}
HORIZON_OAUTH2_SERVER_URI=${HORIZON_OAUTH2_SERVER_URI:-$HORIZON_ENDPOINT/v1/oauth/tokens}
if [ "${HORIZON_OAUTH2_SCOPE+x}" ]; then
  HORIZON_OAUTH2_SCOPE=$HORIZON_OAUTH2_SCOPE
elif [ -n "$HORIZON_CLIENT_SECRET" ]; then
  HORIZON_OAUTH2_SCOPE=PRINCIPAL_ROLE:ALL
else
  HORIZON_OAUTH2_SCOPE=session:role:$SNOWFLAKE_ROLE
fi

tmp_env="$ROOT/.env.tmp"
umask 077
{
  printf 'SNOWFLAKE_CREDENTIALS_JSON=%s\n' "$(quote_env "$CREDENTIALS_JSON")"
  printf 'SNOWFLAKE_ACCOUNT=%s\n' "$(quote_env "$SNOWFLAKE_ACCOUNT")"
  printf 'SNOWFLAKE_USER=%s\n' "$(quote_env "$SNOWFLAKE_USER")"
  printf 'SNOWFLAKE_ROLE=%s\n' "$(quote_env "$SNOWFLAKE_ROLE")"
  printf 'SNOWFLAKE_DATABASE=%s\n' "$(quote_env "$SNOWFLAKE_DATABASE")"
  printf 'SNOWFLAKE_SCHEMA=%s\n' "$(quote_env "$SNOWFLAKE_SCHEMA")"
  printf 'SNOWFLAKE_TABLE=%s\n' "$(quote_env "$SNOWFLAKE_TABLE")"
  printf 'SNOWFLAKE_WAREHOUSE=%s\n' "$(quote_env "$SNOWFLAKE_WAREHOUSE")"
  printf 'SNOWFLAKE_PRIVATE_KEY=%s\n' "$(quote_env "$SNOWFLAKE_PRIVATE_KEY")"
  printf 'SNOWFLAKE_SQL_API_HOST=%s\n' "$(quote_env "$SNOWFLAKE_SQL_API_HOST")"
  printf 'HORIZON_ENDPOINT=%s\n' "$(quote_env "$HORIZON_ENDPOINT")"
  printf 'HORIZON_WAREHOUSE=%s\n' "$(quote_env "$HORIZON_WAREHOUSE")"
  printf 'HORIZON_SCHEMA=%s\n' "$(quote_env "$HORIZON_SCHEMA")"
  printf 'HORIZON_CLIENT_ID=%s\n' "$(quote_env "$HORIZON_CLIENT_ID")"
  printf 'HORIZON_CLIENT_SECRET=%s\n' "$(quote_env "$HORIZON_CLIENT_SECRET")"
  printf 'HORIZON_PAT=%s\n' "$(quote_env "${HORIZON_PAT:-}")"
  printf 'HORIZON_OAUTH2_SERVER_URI=%s\n' "$(quote_env "$HORIZON_OAUTH2_SERVER_URI")"
  printf 'HORIZON_OAUTH2_SCOPE=%s\n' "$(quote_env "$HORIZON_OAUTH2_SCOPE")"
  printf 'POLARIS_ENV=%s\n' "$(quote_env "$POLARIS_ENV")"
  write_optional_env AWS_ACCESS_KEY_ID
  write_optional_env AWS_CLOUD_COST_SOURCE_CATALOG
  write_optional_env AWS_CLOUD_COST_SOURCE_SCHEMA
  write_optional_env AWS_CLOUD_COST_SOURCE_TABLE
  write_optional_env AWS_CLOUD_COST_TARGET_SCHEMA
  write_optional_env AWS_DEFAULT_REGION
  write_optional_env AWS_REGION
  write_optional_env AWS_S3_TABLES_BUCKET_NAME
  write_optional_env AWS_S3_TABLES_NAMESPACE
  write_optional_env AWS_S3_TABLES_WAREHOUSE
  write_optional_env AWS_SECRET_ACCESS_KEY
  write_optional_env AWS_SESSION_TOKEN
  write_optional_env POLARIS_ACCESS_DELEGATION_MODE
  write_optional_env POLARIS_DEFAULT_REGION
  write_optional_env POLARIS_ID
  write_optional_env POLARIS_NAMESPACE
  write_optional_env POLARIS_OAUTH_SCOPE
  write_optional_env POLARIS_OAUTH_TOKEN_URI
  write_optional_env POLARIS_SECRET
  write_optional_env POLARIS_TABLE
  write_optional_env POLARIS_URL
  write_optional_env POLARIS_WAREHOUSE
  write_optional_env DATABRICKS_CATALOG
  write_optional_env DATABRICKS_DEFAULT_REGION
  write_optional_env DATABRICKS_HOST
  write_optional_env DATABRICKS_SCHEMA
  write_optional_env DATABRICKS_TOKEN
  printf 'SHADOWTRAFFIC_LICENSE_ENV=%s\n' "$(quote_env "$LICENSE_ENV")"
  printf 'DBT_BIN=%s\n' "$(quote_env "$FS_DBT_BIN")"
  printf 'DBT_PROFILES_DIR=%s\n' "$(quote_env "$ROOT")"
  printf 'DUCKDB_BUILD_DIR=%s\n' "$(quote_env "$DUCKDB_BUILD_DIR")"
  printf 'DUCKDB_DRIVER_LIB=%s\n' "$(quote_env "$DUCKDB_DRIVER_LIB")"
  printf 'DUCKDB_CLI=%s\n' "$(quote_env "$DUCKDB_CLI")"
  printf 'DUCKDB_EXTENSION_REPOSITORY=%s\n' "$(quote_env "$DUCKDB_EXTENSION_REPOSITORY")"
  printf 'DUCKDB_HOME=%s\n' "$(quote_env "$DUCKDB_HOME")"
  printf 'ADBC_REPOSITORY=%s\n' "$(quote_env "$ADBC_REPOSITORY")"
  printf 'DISABLE_CDN_DRIVER_CACHE=%s\n' "$(quote_env "$DISABLE_CDN_DRIVER_CACHE")"
  printf 'DISABLE_AUTO_DRIVER_REBUILD=%s\n' "$(quote_env "$DISABLE_AUTO_DRIVER_REBUILD")"
} > "$tmp_env"
mv "$tmp_env" "$ROOT/.env"
bootstrap_demo_schemas

printf 'wrote %s\n' "$ROOT/.env"
printf 'dbt binary: %s\n' "$FS_DBT_BIN"
printf 'duckdb cli: %s\n' "$DUCKDB_CLI"
printf 'duckdb adbc driver: %s\n' "$ADBC_REPOSITORY/duckdb"
printf 'duckdb extension repository: %s\n' "$DUCKDB_EXTENSION_REPOSITORY"
printf 'snowflake account: %s\n' "$SNOWFLAKE_ACCOUNT"
printf 'snowflake source: %s.%s.%s\n' "$SNOWFLAKE_DATABASE" "$SNOWFLAKE_SCHEMA" "$SNOWFLAKE_TABLE"
printf 'horizon endpoint: %s\n' "$HORIZON_ENDPOINT"
printf '\nnext: run `direnv allow`, then `scripts/generate_local_csv.sh` to create the source CSV.\n'
