#!/usr/bin/env bash
# Select the active output catalog for the demo's final models.
#
# Usage: scripts/use_catalog.sh <all|ducklake|lakekeeper|horizon|polaris|unity|s3_tables>
#
# Writes catalogs.yml with the local_files (CSV source) catalog plus either all
# known catalogs ("all") or the single chosen output catalog. dbt-fusion attaches
# every catalog listed in catalogs.yml, so scoping to one keeps existence checks
# fast and avoids enumerating an unused external catalog.
#
# This script only edits catalogs.yml. The ACTIVE output catalog is chosen by
# `+catalog_name` in dbt_project.yml — it must match a catalog name written here.
# After regenerating catalogs.yml, run the demo with a plain `dbt run`.
#
# Write targets verified working: ducklake, lakekeeper, horizon, unity.
# unity writes require the duckdb-iceberg#1017 build (duckdb 1.5.4): verified
# 2026-06-10 — create/insert/select round-trip works with
# DISABLE_MULTI_TABLE_COMMIT true. On official duckdb 1.5.3 unity is read-only
# in practice: createTable succeeds but the parquet upload to the
# Databricks-managed location fails (HTTP 400). Views, incremental merge, and
# delete+insert remain unsupported on unity (see reports/feature_compatibility).
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=${1:-all}

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
      snowflake:
        external_volume: "{{ env_var('SNOWFLAKE_EXTERNAL_VOLUME', 'FUSION_ADAPTERS_CI_TEMP') }}"
        base_location_root: "{{ env_var('SNOWFLAKE_BASE_LOCATION_ROOT', 'dbt_aws_cloud_cost/horizon') }}"
      duckdb:
        endpoint: "{{ env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog') }}"
        warehouse: "{{ env_var('HORIZON_WAREHOUSE', '') or env_var('SNOWFLAKE_CATALOG_WAREHOUSE', '') or env_var('SNOWFLAKE_DATABASE', 'DEVELOPMENT') }}"
        secret: snowflake_oauth
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('SNOWFLAKE_DEFAULT_REGION', 'us-west-2') }}"
        stage_create_tables: false
        disable_multi_table_commit: true
        skip_create_table_metadata_updates: true
        remove_files_on_delete: false
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
      snowflake:
        catalog_database: "{{ env_var('SNOWFLAKE_POLARIS_CATALOG_DATABASE', 'CODEX_AWS_CC_POLARIS') }}"
      duckdb:
        endpoint: "{{ env_var('POLARIS_URL', 'https://example.polaris.catalog') }}"
        warehouse: "{{ env_var('POLARIS_WAREHOUSE', 'aws_cloud_cost') }}"
        secret: polaris_oauth
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('POLARIS_DEFAULT_REGION', 'us-east-1') }}"
        attach_as: "polaris"
        default_schema: "{{ env_var('AWS_CLOUD_COST_SOURCE_SCHEMA', env_var('POLARIS_NAMESPACE', 'aws_cloud_cost')) }}"
YAML
}

unity() {
  cat <<'YAML'

  - name: unity
    type: iceberg_rest
    table_format: iceberg
    config:
      snowflake:
        catalog_database: "{{ env_var('SNOWFLAKE_UNITY_CATALOG_DATABASE', 'CODEX_AWS_CC_UNITY') }}"
      duckdb:
        endpoint: "{{ env_var('DATABRICKS_HOST', 'https://example.cloud.databricks.com') }}/api/2.1/unity-catalog/iceberg-rest"
        warehouse: "{{ env_var('DATABRICKS_CATALOG', 'dbt_dataders') }}"
        secret: databricks_token
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('DATABRICKS_DEFAULT_REGION', 'us-west-2') }}"
        attach_as: "unity"
        default_schema: "{{ env_var('DATABRICKS_SCHEMA', 'aws_cloud_cost') }}"
YAML
}

s3_tables() {
  cat <<'YAML'

  - name: s3_tables
    type: s3_tables
    table_format: iceberg
    config:
      duckdb:
        warehouse: "{{ env_var('AWS_S3_TABLES_WAREHOUSE', 'arn:aws:s3tables:us-west-2:486758181003:bucket/dbt-aws-cloud-cost-demo') }}"
        secret: aws_s3_tables
        attach_as: "s3_tables"
        default_schema: "{{ env_var('AWS_CLOUD_COST_TARGET_SCHEMA', env_var('AWS_S3_TABLES_NAMESPACE', 'cloud_cost')) }}"
YAML
}

case "$TARGET" in
  all|ducklake|lakekeeper|horizon|polaris|unity|s3_tables) ;;
  *) echo "usage: $0 <all|ducklake|lakekeeper|horizon|polaris|unity|s3_tables>" >&2; exit 1 ;;
esac

if [ "$TARGET" = all ]; then
  { base; polaris; ducklake; lakekeeper; horizon; unity; s3_tables; } > "$ROOT/catalogs.yml"
  echo "wrote catalogs.yml with all catalog definitions"
  echo "set the active write target via +catalog_name in dbt_project.yml (e.g. horizon|lakekeeper|ducklake)"
else
  { base; "$TARGET"; } > "$ROOT/catalogs.yml"
  echo "wrote catalogs.yml with output catalog: $TARGET"
  if [ "$TARGET" = unity ]; then
    echo "note: unity writes require the duckdb-iceberg#1017 build (duckdb >= 1.5.4); on official 1.5.3 the data upload fails (HTTP 400)."
  fi
  echo "ensure dbt_project.yml has '+catalog_name: $TARGET', then run:  dbt run"
fi
