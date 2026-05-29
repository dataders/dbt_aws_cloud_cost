#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CREDENTIALS_JSON=${SNOWFLAKE_CREDENTIALS_JSON:-/Users/dataders/Developer/dotfiles_env/credentials/fusion.env.json}
LICENSE_ENV=${SHADOWTRAFFIC_LICENSE_ENV:-/Users/dataders/Developer/dotfiles_env/shadowtraffic/license.env}
POLARIS_ENV=${POLARIS_ENV:-/Users/dataders/Developer/dotfiles_env/secrets.zsh}
FS_DBT_BIN=${DBT_BIN:-/Users/dataders/Developer/fs.codex-duckdb-catalog-stack-combined/target/debug/dbt}
DUCKDB_BUILD_DIR=${DUCKDB_BUILD_DIR:-/Users/dataders/Developer/duckdb-iceberg.codex-catalog-write-compat-stack}
DUCKDB_DRIVER_LIB=${DUCKDB_DRIVER_LIB:-$DUCKDB_BUILD_DIR/build/release/src/libduckdb.dylib}
DUCKDB_CLI=${DUCKDB_CLI:-$DUCKDB_BUILD_DIR/build/release/duckdb}
DUCKDB_EXTENSION_REPOSITORY=${DUCKDB_EXTENSION_REPOSITORY:-$DUCKDB_BUILD_DIR/build/release/repository}
DUCKDB_HOME=${DUCKDB_HOME:-$ROOT/.tmp/duckdb-home}
ADBC_REPOSITORY=${ADBC_REPOSITORY:-$ROOT/.tmp/adbc-lib}
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

[ -f "$CREDENTIALS_JSON" ] || die "missing Snowflake credentials json: $CREDENTIALS_JSON"
[ -f "$LICENSE_ENV" ] || die "missing ShadowTraffic license env: $LICENSE_ENV"
[ -x "$FS_DBT_BIN" ] || die "missing executable Fusion dbt binary: $FS_DBT_BIN"
[ -x "$DUCKDB_CLI" ] || die "missing executable DuckDB CLI: $DUCKDB_CLI"
[ -f "$DUCKDB_DRIVER_LIB" ] || die "missing DuckDB driver library: $DUCKDB_DRIVER_LIB"
[ -d "$DUCKDB_EXTENSION_REPOSITORY" ] || die "missing DuckDB extension repository: $DUCKDB_EXTENSION_REPOSITORY"
mkdir -p "$ADBC_REPOSITORY"
ln -sfn "$DUCKDB_DRIVER_LIB" "$ADBC_REPOSITORY/duckdb"
[ -f "$ADBC_REPOSITORY/duckdb" ] || die "missing local DuckDB ADBC driver link: $ADBC_REPOSITORY/duckdb"

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
  AWS_CLOUD_COST_SOURCE_CATALOG \
  AWS_CLOUD_COST_SOURCE_SCHEMA \
  AWS_CLOUD_COST_SOURCE_TABLE \
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
  write_optional_env AWS_CLOUD_COST_SOURCE_CATALOG
  write_optional_env AWS_CLOUD_COST_SOURCE_SCHEMA
  write_optional_env AWS_CLOUD_COST_SOURCE_TABLE
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
