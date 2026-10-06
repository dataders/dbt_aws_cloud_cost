# Run the AWS cost demo on Databricks and Lake Compute

This guide describes the configuration verified on 2026-10-06 against Databricks Unity Catalog and staging Lake Compute. It is for users who know the Snowflake version of `dbt_aws_cloud_cost` and want to run the same project across Databricks and Lake Compute.

## What the project runs

The project uses a Databricks SQL warehouse for the seed and native models. `stg_report` reads the seed and writes a managed Delta table in Unity Catalog. `daily_overview` runs on Lake Compute, writes to the MDLS destination, and appears in Unity Catalog as an external table. The remaining report models run on Databricks and read `daily_overview` there.

| Node | Execution | Result |
| --- | --- | --- |
| `aws_cost_report` | Databricks, through `dbt seed` | Managed table in `quack_demo.aws_cloud_cost` |
| `stg_report` | Databricks | Managed Delta table in `quack_demo.aws_cloud_cost` |
| `daily_overview` | Staging Lake Compute | MDLS data with an external table in Unity Catalog |
| `daily_instance_report` | Databricks | Managed table in Unity Catalog |
| `daily_product_report` | Databricks | Managed table in Unity Catalog |

The verified run produced 9,999 rows in `daily_overview`, 24 in `daily_instance_report`, and 96 in `daily_product_report`. The `daily_overview` table was readable in Databricks and pointed to MDLS storage.

## Prerequisites

- A Databricks workspace with Unity Catalog and a running SQL warehouse.
- A Databricks PAT with permission to create or replace the seed and model tables.
- A staging MDLS destination and its `dct_` PAT.
- Unity Catalog permissions for Lake Compute propagation: `EXTERNAL USE SCHEMA` on `quack_demo.aws_cloud_cost`, plus the permissions required by the registered external location.
- A `dbt` binary with the Databricks seed-loader fix. On 2026-10-06, fs main still bound each seed cell as an ADBC parameter; the 10,000-row, wide seed stalled with that path. The verified run used the binary built from fs worktree commit `d72467a752`.
- `DBT_ENGINE_EXPERIMENTAL_MULTI_ADAPTER=true` for current fs multi-adapter targets.

Do not put either PAT in a tracked file. Supply credentials through the shell environment or a private local environment file.

## Databricks and Lake Compute settings

The verified workspace values were:

```bash
export DATABRICKS_HOST=https://dbc-ef441400-292f.cloud.databricks.com
export DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/f8825ec25797fb4b
export DATABRICKS_TOKEN=<databricks-pat>
export DATABRICKS_CATALOG=quack_demo
export LAKE_COMPUTE_AUTH_TOKEN=<staging-mdls-dct-pat>
export LAKE_COMPUTE_DATABASE=unrealistic_stunned
export LAKE_COMPUTE_SCHEMA=aws_cloud_cost
export LAKE_COMPUTE_BASE_URL=https://api.dbt-compute.staging.fivetran.com/
export LAKE_COMPUTE_AUTH_URL=https://api-staging.fivetran.com
export DBT_ENGINE_EXPERIMENTAL_MULTI_ADAPTER=true
```

The `dct_` PAT must use the staging token exchange host. The profile uses `method: fivetran`, which exchanges the PAT for the short-lived JWT accepted by staging. A staging PAT sent to the production exchange returns 401.

## Project configuration

Keep the Snowflake model SQL and macros unchanged. The Databricks variant needs these config changes:

1. In `profiles.yml`, make Databricks the default adapter and keep Lake Compute as the second connection. Point the Lake Compute connection at the staging API and staging token exchange host.
2. In `dbt_project.yml`, remove the Snowflake-only seed `+database: SEEDS` override. Databricks loads seeds into the default catalog. Keep the project schema and model materialization settings.
3. In `seeds/properties.yml`, use `string` instead of bare `varchar` for the seed column types. Databricks rejected the bare type with `Invalid column description: varchar`.
4. In `models/staging/stg_aws_cloud_cost.yml`, set database, schema, and identifier quoting to false for `stg_report`. Refs are rendered using the referenced node's adapter. Databricks backticks fail in the DuckDB parser used by Lake Compute. The demo identifiers are lowercase, so unquoted rendering resolves them correctly.
5. Add a Unity Catalog entry in `catalogs.yml` so Lake Compute can attach `quack_demo` when it reads `stg_report`:

```yaml
catalogs:
  - name: quack_demo_cat
    type: unity
    table_format: iceberg
    config:
      lakecompute:
        catalog_database: quack_demo
        region: us-east-1
        host: dbc-ef441400-292f.cloud.databricks.com
```

The Databricks profile credentials provide authentication for the attach. The verified run did not need a `databricks` block in this catalog entry, a `+catalog_name` on `stg_report`, a project-wide quoting override, or an explicit `propagate` setting on `daily_overview`.

The staging warehouse accepted `TIMESTAMP_NTZ` columns without `delta.feature.timestampNtz` in the project config on `dbsql_version 2026.36`. An earlier run on this workspace required manual feature enablement. If table creation fails with `DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT`, add the `delta.feature.timestampNtz: supported` table property under the staging model config and rerun.

## Run the project

From the project root, with the environment variables above set:

```bash
/path/to/dbt debug
/path/to/dbt seed
/path/to/dbt run
/path/to/dbt test
```

`dbt debug` may report that the Lake Compute schema does not exist. It does not create namespaces. The run prelude creates the schema, so this message is expected before the first run when authentication and the remote query succeeded.

Expected results from the verified staging run:

```text
dbt seed: 1 seed succeeded
dbt run: 4 models succeeded
dbt test: 6 tests succeeded
```

On current fs, `daily_overview` appeared in Unity Catalog as `EXTERNAL` without an explicit `propagate: databricks` setting. Its location was under the MDLS staging prefix. Confirm this behavior when changing the dbt binary; older versions may require an explicit propagation config.

## Unity Catalog permissions and external location

Propagation registers a UC external table pointing at files stored by MDLS. Databricks does not copy those files into managed storage.

The verified staging destination stored data below:

```text
s3://dbt-compute-mdls-bucket/lake_compute_staging/quack_demo_aws_cloud_cost/
```

The Unity Catalog metastore must have an external location covering this prefix, backed by a storage credential whose IAM policy permits the path. The test environment used a location scoped to the project prefix. The caller also needed `READ FILES`, `CREATE EXTERNAL TABLE`, and `EXTERNAL USE LOCATION` on that location, and `EXTERNAL USE SCHEMA` on `quack_demo.aws_cloud_cost`.

For an external location whose URL ends at `lake_compute_staging`, Databricks may validate the prefix without a trailing slash. An IAM condition matching only `lake_compute_staging/*` may not cover that listing request. Use a project-level prefix containing a slash, or adjust the IAM condition with the account administrator.

The external table depends on MDLS retaining its files. Dropping the MDLS table or removing its files leaves the Unity Catalog table stale.

## Troubleshooting

| Error | Meaning and next step |
| --- | --- |
| `Invalid column description: varchar` | Change the seed `column_types` values from bare `varchar` to `string`. |
| `NO_SUCH_CATALOG_EXCEPTION` for `seeds` | Remove the Snowflake seed database override so Databricks uses the profile's default catalog. |
| Parser error at a backtick in a Lake Compute query | Set quoting false on the referenced Databricks node. This is a current fs cross-adapter rendering limitation. |
| `Catalog "quack_demo" does not exist` from Lake Compute | Add the Unity Catalog attach entry in `catalogs.yml` and check `catalog_database`, region, host, and profile credentials. |
| `DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT` for `timestampNtz` | Add `delta.feature.timestampNtz: supported` to the staging table properties. The verified warehouse did not need it, but the requirement varies by runtime. |
| `Fivetran token exchange failed (401)` | Check that a staging PAT uses `https://api-staging.fivetran.com` and the staging dbt-compute API URL. |
| `Schema with name "X" does not exist` from `dbt debug` | `dbt debug` does not create the MDLS namespace. Run the seed or model command to create it. |
| `EXTERNAL USE SCHEMA` permission error | Ask the UC administrator to grant that privilege on the destination schema. |
| `EXTERNAL_LOCATION_DOES_NOT_EXIST` | Register an external location covering the MDLS S3 prefix and grant the required location privileges. |
| IAM `AccessDenied` while validating an external location | Check the storage credential policy and whether its S3 prefix condition covers the exact location validation request. |

## Version notes

The verified run used fs main at `c5f60d0aa9`, with the seed-loader fix from `d72467a752`. The staging worker read `TIMESTAMP_NTZ` through the Unity Catalog attach; its deployed build ID was not inspected. Check current fs and staging versions before treating the seed fix, automatic propagation, or timestamp feature behavior as permanent requirements.
