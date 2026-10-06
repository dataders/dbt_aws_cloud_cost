# Debug record: Databricks and Lake Compute staging

Original investigation: 2026-09-28. Fresh retest: 2026-10-06.

Current status: the full demo succeeds against Databricks and staging Lake Compute. This record keeps the original debugging history and adds the fresh run results. Treat version-specific findings below as dated evidence.

## Goal

Run the `dbt_aws_cloud_cost` project across Databricks and Lake Compute:

1. Load the project seed into Databricks Unity Catalog.
2. Build `stg_report` natively on Databricks.
3. Run `daily_overview` on Lake Compute and write its data to MDLS.
4. Make the Lake Compute result readable in Unity Catalog by Databricks downstream models.

## Original incident, 2026-09-28

The initial run against Lake Compute failed on `daily_overview` with a DuckDB parser error while the service mirrored a Databricks attach:

```text
Parser Error: zero-length delimited identifier
CREATE VIEW "quack_demo"."aws_cloud_cost"."" ...
```

### Quack #715: empty view name during attach mirroring

Each Lake Compute materialization includes a `create schema if not exists <database>.<schema>` prelude. sqlglot represented the schema reference as a table node with an empty name. The worker's Databricks attach logic turned it into an invalid empty-name view.

Quack #715 added a guard for empty table names in the attach path. Staging received that fix before production. This was the reason the demo first had to run against staging. Current staging succeeds; production was not retested in the 2026-10-06 run.

### Staging authentication

The initial staging attempt had 2 separate configuration issues:

- A staging `dct_` PAT must exchange at `https://api-staging.fivetran.com`. The production exchange returned 401 for the staging PAT.
- Staging expects the JWT returned by that exchange. The profile must use `method: fivetran` and `fivetran_credential`, rather than sending the raw PAT with `method: token`.

The staging Lake Compute API URL is `https://api.dbt-compute.staging.fivetran.com/`. The auth exchange host and the Lake Compute API host are separate settings.

The original notes confused `LAKE_COMPUTE_BASE_URL` with the fs variable `DBT_COMPUTE_BASE_URL`. The profile reads its `base_url` field; this project's profile supplies that field from `LAKE_COMPUTE_BASE_URL`.

### Unity Catalog schema privilege

Propagation's temporary-table-credentials request failed until the Databricks principal had `EXTERNAL USE SCHEMA` on `quack_demo.aws_cloud_cost`. Grant this privilege to the principal used for the run. The exact principal varies by environment.

### External location and IAM

The staging MDLS destination `unrealistic_stunned` wrote under this prefix:

```text
s3://dbt-compute-mdls-bucket/lake_compute_staging/quack_demo_aws_cloud_cost/
```

Unity Catalog rejected external-table creation until an external location covered the prefix. The test environment used a project-scoped location over:

```text
s3://dbt-compute-mdls-bucket/lake_compute_staging/quack_demo_aws_cloud_cost
```

The location's storage credential needed to cover the matching S3 prefix. UC also required `READ FILES`, `CREATE EXTERNAL TABLE`, and `EXTERNAL USE LOCATION` on the external location.

A location ending at `lake_compute_staging` can be validated as an S3 prefix without a trailing slash. An IAM condition containing only `lake_compute_staging/*` may not cover that request. Use a deeper project prefix or have the administrator adjust the condition.

## Fresh retest, 2026-10-06

The project was reset to the Snowflake branch base (`13c690a`) and converted one step at a time against the current fs and staging service. No model SQL or macro changes were made in the project.

### Changes forced by observed errors

- Swapped the default profile connection from Snowflake to Databricks.
- Set the staging Lake Compute API and staging PAT-exchange URLs. Keeping the Snowflake profile's production defaults caused the staging PAT exchange to return 401. A direct call to the staging exchange endpoint with the correct request body returned HTTP 200.
- Removed the seed's `+database: SEEDS` setting. Databricks reported `NO_SUCH_CATALOG_EXCEPTION` for catalog `seeds`.
- Changed bare `varchar` seed column types to `string`. Databricks reported `Invalid column description: varchar`.
- Set quoting false for database, schema, and identifier on `stg_report`. Without it, the `daily_overview` SQL contained backtick-quoted identifiers, which DuckDB rejected.
- Added a `type: unity` catalog entry for Lake Compute to attach `quack_demo`. Without it, DuckDB reported `Catalog "quack_demo" does not exist`.

The catalog entry only needs the Lake Compute configuration for this run. A Databricks routing block, `+catalog_name` on `stg_report`, `use_uniform`, and project-wide quoting settings were not needed.

### Findings that differ from the original config

- `stg_report` built as a managed Delta table using its existing `table_format='iceberg'` SQL config. It needed no `+catalog_name` or `delta.feature.timestampNtz` property in the fresh run.
- The serverless warehouse accepted the `TIMESTAMP_NTZ` columns without the explicit Delta feature property. An earlier run on this workspace failed with `DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT`; behavior may differ by runtime.
- `daily_overview` was propagated to Unity Catalog without an explicit `propagate: databricks` setting. Its Unity Catalog table was `EXTERNAL`, with location under the MDLS staging prefix. Both Databricks downstream models read it.
- The current staging worker read the `TIMESTAMP_NTZ` columns through the Unity Catalog attach without the earlier unknown-type failure.

### Verified result

Using the dbt binary built with the fs seed-loader fix:

```text
dbt seed: 1 seed succeeded
dbt run: 4 models succeeded
dbt test: 6 tests succeeded
```

The final Unity Catalog tables in `quack_demo.aws_cloud_cost` were:

| Table | Type | Rows or role |
| --- | --- | --- |
| `aws_cost_report` | MANAGED | Databricks seed |
| `stg_report` | MANAGED | Databricks staging model |
| `daily_overview` | EXTERNAL | MDLS-backed, 9,999 rows |
| `daily_instance_report` | MANAGED | 24 rows |
| `daily_product_report` | MANAGED | 96 rows |

The `daily_overview` external table location was:

```text
s3://dbt-compute-mdls-bucket/lake_compute_staging/quack_demo_aws_cloud_cost/daily_overview
```

The Lake Compute destination was `unrealistic_stunned`, schema `aws_cloud_cost`. The Databricks UC catalog was `quack_demo`.

## Current caveats

- **Seed loading:** the verified run used dbt binary commit `d72467a752`, which inlines seed values rather than binding every cell as an ADBC parameter. The stock Databricks seed loader still stalled on the wide seed at the time of the retest. Check whether the fix has since landed in fs before reusing that binary.
- **Cross-adapter quoting:** the `stg_report` quoting config remains a workaround for fs rendering a referenced node with the node's own adapter. A Lake Compute ref to a Databricks node therefore contains backticks, which DuckDB does not parse. See `~/Documents/follow-ups/2026-10-06-lakecompute-cross-adapter-ref-quoting.md` for the proposed fs change.
- **Timestamp feature:** omit `delta.feature.timestampNtz` on the verified warehouse. Add it if a target warehouse reports `DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT`.
- **Production:** this retest used staging only. It does not establish that the production Lake Compute service has the same worker build or configuration.
- **Credentials:** this file contains no PATs. Do not put Databricks or MDLS credentials in the repository.

## Reproduction environment

The 2026-10-06 retest used:

- fs main at `c5f60d0aa9`, plus the seed-loader fix in `d72467a752`.
- Databricks workspace host `dbc-ef441400-292f.cloud.databricks.com`, SQL warehouse path `/sql/1.0/warehouses/f8825ec25797fb4b`, and UC catalog `quack_demo`.
- Staging Lake Compute API `https://api.dbt-compute.staging.fivetran.com/` and token exchange host `https://api-staging.fivetran.com`.
- MDLS destination `unrealistic_stunned`, schema `aws_cloud_cost`.
- `DBT_ENGINE_EXPERIMENTAL_MULTI_ADAPTER=true`.

Supply credentials through the environment. The original debug session's credential files and token lifetimes are not part of this record.
