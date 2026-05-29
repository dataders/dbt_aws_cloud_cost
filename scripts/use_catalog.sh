#!/usr/bin/env bash
# Select the active output catalog for the demo's models.
# Usage: scripts/use_catalog.sh <ducklake|lakekeeper|horizon|polaris>
# Writes catalogs.yml with local_files (CSV source) + the chosen output catalog,
# so only the active catalog is attached. Also prints the env var to export.
#
# Note: unity (Databricks UC) is intentionally NOT a write target — UC's Iceberg
# REST catalog implements only read endpoints (no createTable), see
# https://github.com/unitycatalog/unitycatalog/issues/3
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=${1:-lakekeeper}

base() {
  cat <<'YAML'
catalogs:
  - name: local_files
    type: local_filesystem
    table_format: default
    config:
      duckdb:
        root_path: "./local_files"
        file_format: csv
YAML
}

ducklake() {
  cat <<'YAML'

  - name: ducklake
    type: ducklake
    table_format: default
    config:
      duckdb:
        metadata_path: "./.tmp/ducklake.db"
        attach_as: "ducklake"
        create_if_not_exists: true
YAML
}

lakekeeper() {
  cat <<'YAML'

  - name: lakekeeper
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: "http://localhost:18181/catalog"
        warehouse: "demo"
        authorization_type: "NONE"
        access_delegation_mode: "NONE"
        attach_as: "lakekeeper"
        default_schema: "default"
YAML
}

horizon() {
  cat <<'YAML'

  - name: horizon
    type: horizon
    table_format: iceberg
    config:
      duckdb:
        endpoint: "{{ env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog') }}"
        warehouse: "{{ env_var('HORIZON_WAREHOUSE', '') or env_var('SNOWFLAKE_CATALOG_WAREHOUSE', '') or env_var('SNOWFLAKE_DATABASE', 'DEVELOPMENT') }}"
        secret: snowflake_oauth
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('SNOWFLAKE_DEFAULT_REGION', 'us-west-2') }}"
        support_stage_create: false
        use_transaction_commit: false
        skip_create_table_metadata_updates: true
        allow_deletes: false
        attach_as: "horizon"
        default_schema: "{{ env_var('HORIZON_SCHEMA', '') or env_var('SNOWFLAKE_SCHEMA', 'AWS_CLOUD_COST') }}"
YAML
}

polaris() {
  cat <<'YAML'

  - name: polaris
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: "{{ env_var('POLARIS_URL', 'https://example.polaris.catalog') }}"
        warehouse: "{{ env_var('POLARIS_WAREHOUSE', 'aws_cloud_cost') }}"
        secret: polaris_oauth
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('POLARIS_DEFAULT_REGION', 'us-east-1') }}"
        attach_as: "polaris"
        default_schema: "{{ env_var('POLARIS_NAMESPACE', 'aws_cloud_cost') }}"
YAML
}

case "$TARGET" in
  ducklake|lakekeeper|horizon|polaris) ;;
  *) echo "usage: $0 <ducklake|lakekeeper|horizon|polaris>" >&2; exit 1 ;;
esac

{ base; "$TARGET"; } > "$ROOT/catalogs.yml"
echo "wrote catalogs.yml with output catalog: $TARGET"
echo "now run with:  export AWS_CLOUD_COST_TARGET_CATALOG=$TARGET"
