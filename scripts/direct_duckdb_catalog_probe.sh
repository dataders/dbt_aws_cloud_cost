#!/usr/bin/env zsh
set -euo pipefail

ROOT=${0:A:h:h}
DUCK=${DUCKDB_CLI:-/Users/dataders/Developer/duckdb-iceberg.codex-catalog-write-compat-stack/build/release/duckdb}
ICEBERG_EXT=${DUCKDB_ICEBERG_EXTENSION:-/Users/dataders/Developer/duckdb-iceberg.codex-catalog-write-compat-stack/build/release/extension/iceberg/iceberg.duckdb_extension}

set -a
source "$ROOT/.env"
set +a

quote_sql() {
  printf "%s" "$1" | sed "s/'/''/g"
}

polaris_scope=${POLARIS_OAUTH_SCOPE:-PRINCIPAL_ROLE:ALL}
polaris_token_uri=${POLARIS_OAUTH_TOKEN_URI:-${POLARIS_URL%/}/v1/oauth/tokens}
polaris_region=${POLARIS_DEFAULT_REGION:-us-east-1}
snowflake_region=${SNOWFLAKE_DEFAULT_REGION:-us-west-2}

{
  printf "LOAD '%s';\n" "$(quote_sql "$ICEBERG_EXT")"
  printf "LOAD httpfs;\n"
  printf "CREATE OR REPLACE SECRET polaris_oauth (TYPE ICEBERG, CLIENT_ID '%s', CLIENT_SECRET '%s', OAUTH2_SERVER_URI '%s', OAUTH2_SCOPE '%s');\n" \
    "$(quote_sql "$POLARIS_ID")" \
    "$(quote_sql "$POLARIS_SECRET")" \
    "$(quote_sql "$polaris_token_uri")" \
    "$(quote_sql "$polaris_scope")"
  printf "CREATE OR REPLACE SECRET snowflake_oauth (TYPE ICEBERG, CLIENT_ID '', CLIENT_SECRET '%s', OAUTH2_SERVER_URI '%s', OAUTH2_SCOPE '%s', OAUTH2_GRANT_TYPE 'client_credentials');\n" \
    "$(quote_sql "$HORIZON_PAT")" \
    "$(quote_sql "$HORIZON_OAUTH2_SERVER_URI")" \
    "$(quote_sql "$HORIZON_OAUTH2_SCOPE")"
  printf "ATTACH '%s' AS polaris (TYPE ICEBERG, ENDPOINT '%s', SECRET 'polaris_oauth', AUTHORIZATION_TYPE 'OAUTH2', ACCESS_DELEGATION_MODE 'VENDED_CREDENTIALS', DEFAULT_REGION '%s');\n" \
    "$(quote_sql "$POLARIS_WAREHOUSE")" \
    "$(quote_sql "$POLARIS_URL")" \
    "$(quote_sql "$polaris_region")"
  printf "ATTACH '%s' AS horizon (TYPE ICEBERG, ENDPOINT '%s', SECRET 'snowflake_oauth', AUTHORIZATION_TYPE 'OAUTH2', ACCESS_DELEGATION_MODE 'VENDED_CREDENTIALS', DEFAULT_REGION '%s', SUPPORT_STAGE_CREATE false, USE_TRANSACTION_COMMIT false, SKIP_CREATE_TABLE_METADATA_UPDATES true, ALLOW_DELETES false);\n" \
    "$(quote_sql "$HORIZON_WAREHOUSE")" \
    "$(quote_sql "$HORIZON_ENDPOINT")" \
    "$(quote_sql "$snowflake_region")"
  printf "SELECT 'attached' AS step, count(*) AS catalog_count FROM duckdb_databases() WHERE database_name IN ('polaris', 'horizon');\n"
  printf "SELECT 'polaris_limit' AS step, _file, _line FROM polaris.aws_cloud_cost.aws_cost_report LIMIT 1;\n"
  printf "CREATE TABLE local_stage_probe AS SELECT _file, _line FROM polaris.aws_cloud_cost.aws_cost_report LIMIT 10;\n"
  printf "SELECT 'local_stage' AS step, count(*) AS row_count FROM local_stage_probe;\n"
  printf "DROP TABLE IF EXISTS horizon.aws_cloud_cost.codex_direct_duckdb_probe;\n"
  printf "CREATE TABLE horizon.aws_cloud_cost.codex_direct_duckdb_probe (id integer, writer varchar);\n"
  printf "INSERT INTO horizon.aws_cloud_cost.codex_direct_duckdb_probe SELECT 1 AS id, 'direct-duckdb' AS writer;\n"
  printf "SELECT 'horizon_readback' AS step, id, writer FROM horizon.aws_cloud_cost.codex_direct_duckdb_probe;\n"
} | "$DUCK" -unsigned -csv -header :memory:
