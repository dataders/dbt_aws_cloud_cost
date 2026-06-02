# dbt Fusion + DuckDB Multi-Catalog Feature Compatibility

Run date: 2026-06-02

This report covers the local debug stack for the AWS cloud cost demo running through dbt Fusion, DuckDB, and the local `duckdb-iceberg` build.

## Runtime Under Test

- Demo repo state: `dataders/dbt_aws_cloud_cost@a8d6ddff06e1fb912f2de6b362e0c2393d15d07e`, plus the compatibility probe added in this report.
- Fusion binary: `/Users/dataders/Developer/fs.codex-duckdb-catalog-stack-combined/target/debug/dbt`
- Fusion branch: `codex/duckdb-catalog-stack-combined`
- Fusion commit: `d8e3266901a201d72b869b71c8e1a58905cd6ace`
- Fusion PR: https://github.com/dbt-labs/fs/pull/10484
- duckdb-iceberg debug build: `/Users/dataders/Developer/duckdb-iceberg.horizon-rest-write-compat-options/build/debug/duckdb`
- duckdb-iceberg branch: `horizon-rest-write-compat-options`
- duckdb-iceberg commit: `05f37a974987a500ad29ef5c5dda834b7c834cdc`
- DuckDB submodule: `14eca11bd9d4a0de2ea0f078be588a9c1c5b279c`, `v1.5.3`
- Extension repository: `/Users/dataders/Developer/duckdb-iceberg.horizon-rest-write-compat-options/build/debug/repository`

Probe command:

```bash
set -a
source ./.env
set +a
uv run python -u scripts/feature_compat_probe.py --run-id full_matrix_20260602_v2 --timeout 240
```

Raw sanitized outputs are ignored by git and available locally at:

```plain text
.tmp/feature-compat/full_matrix_20260602_v2/results.json
.tmp/feature-compat/full_matrix_20260602_v2/summary.md
```

## Summary Matrix

Each target was tested for 26 dbt features: parse, compile, table, view, ephemeral, seed, generic tests, singular tests, source generic tests, store failures, unit tests, snapshots, incremental append, incremental merge, incremental delete+insert, hooks, contracts, source freshness, show, catalog JSON, docs generate, clone, retry, run-operation, build, and analysis compile.

| Target | Passes | Failures |
| --- | ---: | --- |
| `builtin` | 25/26 | `catalog_json` |
| `ducklake` | 23/26 | `source_generic_tests`, `source_freshness`, `catalog_json` |
| `lakekeeper` | 24/26 | `view`, `catalog_json` |
| `horizon_checkedin` | 23/26 | `view`, `store_failures`, `catalog_json` |
| `horizon_scripted` | 23/26 | `view`, `store_failures`, `catalog_json` |
| `unity` | 22/26 | `view`, `incremental_merge`, `incremental_delete_insert`, `catalog_json` |
| `polaris` | 24/26 | `view`, `catalog_json` |

Important caveat: the DuckLake model/seed materializations are currently compiling to the default DuckDB database `compat`, not to the attached `ducklake` catalog. The DuckLake source failures expose that mismatch because source queries point at `"ducklake"."aws_cloud_cost"` while the seed was created under `"compat"."aws_cloud_cost"`.

## Failure Classes

### 1. `compile --write-catalog` fails everywhere

Symptom:

```plain text
Internal Error: expected column of type StringArray not found, available are:
Field { name: "table_owner", data_type: Int32, nullable: true }
```

Why it fails:

- Fusion's DuckDB catalog macro returns `null as table_owner`.
- DuckDB/Arrow types that bare `NULL` as `Int32`.
- Fusion's DuckDB metadata adapter reads `table_owner` as a `StringArray`.

Code pointers:

- `fs/sa/crates/dbt-loader/src/dbt_macro_assets/dbt-duckdb/macros/catalog.sql`
- `fs/sa/crates/dbt-adapter/src/metadata/duckdb/mod.rs`

Likely patch:

- Change the DuckDB catalog macro to `cast(null as varchar) as table_owner`.
- Add a regression for `compile --write-catalog` on DuckDB.
- The ClickHouse macro also has a bare `null as table_owner`, so audit that adapter too.

Workaround:

- `dbt docs generate` passed everywhere in this probe, even though `compile --write-catalog` failed. Use docs generation when the artifact path is enough.

### 2. Iceberg REST `view` materialization fails on Lakekeeper, Horizon, Unity, and Polaris

Symptom:

```plain text
Not implemented Error: Create View
```

Why it fails:

- `duckdb-iceberg` REST schema entry explicitly throws `NotImplementedException("Create View")`.
- Apache Iceberg does define a standard view metadata format, but this local `duckdb-iceberg` path does not wire DuckDB `CREATE VIEW` into Iceberg REST view create/commit behavior yet.

Code pointer:

- `duckdb-iceberg/src/catalog/rest/catalog_entry/schema/iceberg_schema_entry.cpp`

Spec references:

- Apache Iceberg REST catalog spec: https://iceberg.apache.org/rest-catalog-spec/
- Apache Iceberg view spec: https://iceberg.apache.org/view-spec/

Likely patch:

- Implement `IcebergSchemaEntry::CreateView`.
- Build view metadata with SQL representation and dialect.
- Commit/register that view through the REST catalog view APIs where the provider supports them.
- Provider support may differ, so keep provider capability checks explicit.

Workaround:

- Do not use dbt `view` materialization against Iceberg REST catalogs yet. Use `table` or `ephemeral`.

### 3. DuckLake source tests and freshness route sources to `ducklake`, while writes route to `compat`

Symptoms:

```plain text
Catalog Error: Table with name "aws_cloud_cost.<seed>" does not exist because schema "aws_cloud_cost" does not exist.
```

Observed compiled SQL:

```sql
create table "compat"."aws_cloud_cost"."<seed>" ...
from "ducklake"."aws_cloud_cost"."<seed>"
```

Why it fails:

- The catalog relation builder records `catalog_linked_database = ducklake` for DuckLake, but the final relation database used by dbt materializations is still the local DuckDB database `compat`.
- Source YAML sets `database: "ducklake"`, so source tests and freshness correctly look in the attached DuckLake catalog.

Code pointer:

- `fs/sa/crates/dbt-adapter/src/catalog_relation/catalog_relation_v2.rs`

Likely patch:

- Adjust DuckDB/DuckLake relation construction or materialization database selection so `catalog_name: ducklake` produces relations in the attached alias, not the default DuckDB database.
- Add a regression that compiles/runs a seed plus source freshness with `catalog_name: ducklake` and asserts both write and read use the same database.

Workaround:

- Treat DuckLake results in this matrix as provisional. Until routing is fixed, use the actual relation database in source definitions or avoid DuckLake as a write target in this demo.

### 4. Horizon `store_failures` fails because dbt writes failures to an audit schema

Symptom:

```plain text
Failed to create schema 'aws_cloud_cost_dbt_test__audit' in database 'horizon'
Forbidden_403 ... Authorization failed
```

Why it fails:

- dbt stores test failures in `{{ profile.schema }}_dbt_test__audit` by default.
- The Horizon token can write regular demo objects, but it does not have permission to create the derived audit namespace.

Reference:

- dbt `store_failures` docs: https://docs.getdbt.com/reference/resource-configs/store_failures

Likely patch or configuration fix:

- Grant/create the `aws_cloud_cost_dbt_test__audit` namespace for the Horizon principal.
- Or configure the test failure schema to an existing authorized namespace.
- For external catalogs, consider an adapter-level option that keeps failure tables in the model schema.

Workaround:

- Disable `store_failures` on Horizon until the audit namespace permission is granted.

### 5. Unity Catalog incremental merge and delete+insert fail on provider constraints

Merge symptom:

```plain text
Conflict_409: Adding multiple snapshots in a single update is not supported.
```

Delete+insert symptom:

```plain text
BadRequest_400: Iceberg delete files are not supported with format version < 3
```

Why it fails:

- `duckdb-iceberg`'s UPDATE path creates a combined delete+insert snapshot path that Unity rejects as multiple snapshots in one update.
- Databricks documents Iceberg v3 and deletion vectors as the path for row-level deletes. The probe-created Unity tables are not being created as Iceberg format version 3.

Code pointer:

- `duckdb-iceberg/src/execution/operator/iceberg_insert.cpp`

Reference:

- Databricks Iceberg REST external access: https://docs.databricks.com/aws/en/external-access/iceberg
- Databricks Iceberg v3 features: https://docs.databricks.com/aws/en/iceberg/iceberg-v3

Likely patch:

- For merge: rework the Unity-compatible update commit path so the REST commit has one acceptable snapshot update, or split the operation only if atomic semantics can be preserved.
- For delete+insert: allow table creation with Iceberg format version 3 and deletion vectors when targeting Unity, then verify whether DuckDB's delete-file writer matches Databricks' supported v3 semantics.

Workaround:

- Use append-only incremental strategy on Unity for now.
- Use full-refresh for replacements until the v3/delete path is implemented and verified.

## What Works

The important positive result is that core writes work broadly:

- Lakekeeper: tables, seeds, tests, source tests, source freshness, snapshots, all incremental strategies, hooks, contracts, clone, build, and docs generation pass.
- Horizon: tables, seeds, normal tests, source tests, source freshness, snapshots, all incremental strategies, hooks, contracts, clone, build, and docs generation pass.
- Unity: tables, seeds, tests, source tests, source freshness, store failures, snapshots, append incrementals, hooks, contracts, clone, build, and docs generation pass.
- Polaris: tables, seeds, tests, source tests, source freshness, snapshots, all incremental strategies, hooks, contracts, clone, build, and docs generation pass.

The remaining failures are concentrated in catalog artifact typing, view DDL support, DuckLake routing, Horizon audit-schema permission, and Unity row-level mutation semantics.
