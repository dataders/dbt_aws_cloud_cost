# dbt AWS Cloud Cost DuckDB Multi-Catalog Demo

A runnable DuckDB **catalogs v2** demo using AWS Cost & Usage Report
transformations. Source rows are generated locally by ShadowTraffic into a CSV;
staging runs in the built-in DuckDB catalog; the final models are written to a
swappable output catalog — **ducklake**, **lakekeeper**, **horizon** (Snowflake),
or **polaris** (Fivetran) — selected with one env var. (**unity** is read-only;
UC's Iceberg REST catalog has no write endpoint.)

## Architecture

```
local_files/aws_cost_report.csv        (read with read_csv)
        |
        v
stg_aws_cloud_cost__report_base        (catalog: builtin)
        |
        v
stg_aws_cloud_cost__report             (catalog: builtin)
        |
        v
aws_cloud_cost__daily_*                (catalog: $AWS_CLOUD_COST_TARGET_CATALOG)
```

The source is a local CSV read directly via `read_csv()`, so the pipeline never
attaches a large external *source* catalog. Only the **active output catalog**
is attached (see "Switching output catalogs"), so an unused catalog with many
tables never gets enumerated.

## Catalogs

The final models can be written to any of these output catalogs — switch with one
env var (see "Switching output catalogs"). All four are verified working end-to-end
(5/5 models):

| Catalog | Type | Write? | Setup |
| --- | --- | --- | --- |
| `builtin` | DuckDB native (staging only) | — | None |
| `ducklake` | DuckLake local metadata | ✅ | None |
| `lakekeeper` | Iceberg REST | ✅ | `docker compose up -d` |
| `horizon` | Snowflake Horizon REST | ✅ | Snowflake env vars + `scripts/configure_horizon_schema.sh`, `scripts/create_horizon_pat.sh` |
| `polaris` | Iceberg REST (Fivetran) | ✅ | `POLARIS_*` env vars |
| `unity` | Databricks Unity Catalog | ❌ read-only | UC Iceberg REST has no write endpoint ([unitycatalog#3](https://github.com/unitycatalog/unitycatalog/issues/3)) |

`scripts/setup_env.sh` configures the local fs dbt binary to load the patched
DuckDB ADBC driver from the duckdb-iceberg build. Without that local driver
path, `dbt debug` may load the CDN DuckDB driver and reject the unsigned local
Iceberg extension. The profile does not explicitly `INSTALL`/`LOAD` extensions
because the patched driver build carries the catalog extensions as built-ins.

## Setup

This demo expects local builds of the catalog-enabled Fusion runtime and
patched DuckDB Iceberg extension.

```bash
cd ~/Developer/fs            && cargo build --bin dbt   # debug binary at target/debug/dbt
cd ~/Developer/duckdb-iceberg && make

cd ~/Developer/dbt_aws_cloud_cost
scripts/setup_env.sh
direnv allow
docker compose up -d                 # lakekeeper + minio + postgres
scripts/generate_local_csv.sh 10000  # writes local_files/aws_cost_report.csv
```

`scripts/generate_local_csv.sh [ROWS]` runs ShadowTraffic to generate the rows,
then converts them to `local_files/aws_cost_report.csv` with a single uniform
`_modified` value so every row counts as the latest file version (mirrors one
Fivetran file export). Override the path with `AWS_CLOUD_COST_CSV_PATH`.

## Switching output catalogs

`dbt-fusion` attaches every catalog listed in `catalogs.yml`, and an unused
external catalog with many tables stalls existence checks. So switching is done
by writing `catalogs.yml` with **only the active output catalog**, plus setting
the matching env var that `dbt_project.yml` reads for `+catalog`:

```bash
# ducklake (local DuckLake metadata, fastest)
scripts/use_catalog.sh ducklake   && export AWS_CLOUD_COST_TARGET_CATALOG=ducklake   && dbt run

# lakekeeper (local Iceberg REST via docker)
scripts/use_catalog.sh lakekeeper && export AWS_CLOUD_COST_TARGET_CATALOG=lakekeeper && dbt run

# horizon (Snowflake-managed Iceberg)
scripts/use_catalog.sh horizon    && export AWS_CLOUD_COST_TARGET_CATALOG=horizon    && dbt run

# polaris (Fivetran-managed Iceberg REST)
scripts/use_catalog.sh polaris    && export AWS_CLOUD_COST_TARGET_CATALOG=polaris    && dbt run
```

`scripts/use_catalog.sh <ducklake|lakekeeper|horizon|polaris>` writes `catalogs.yml`
with `local_files` (CSV source) + the chosen output catalog, and
`dbt_project.yml` reads `AWS_CLOUD_COST_TARGET_CATALOG` for the final models'
`+catalog`. Only the active catalog is attached. (Unity is not a write target —
see below.)

## External Catalog Env Vars

Snowflake Horizon — keep the Snowflake SQL API values in `.env`, then run
`scripts/configure_horizon_schema.sh`, `scripts/create_horizon_pat.sh`, and
`scripts/doctor.sh`. The schema configuration sets `CATALOG = 'SNOWFLAKE'` and an
`EXTERNAL_VOLUME` so Horizon REST table creation can write Snowflake-managed
Iceberg tables.

Databricks Unity Catalog:

```bash
export DATABRICKS_HOST='https://<workspace>.cloud.databricks.com'
export DATABRICKS_TOKEN='<personal-access-token>'   # must be a PAT for THIS workspace
export DATABRICKS_CATALOG='<your-managed-catalog>'  # e.g. dbt_dataders
export DATABRICKS_SCHEMA='aws_cloud_cost'
```

**Unity write requires `EXTERNAL USE SCHEMA`.** Databricks excludes this
privilege from `ALL PRIVILEGES` even for the schema owner, so external engines
(DuckDB) writing Iceberg tables get HTTP 403 "Not authorized" without it. The
acting principal is whoever the PAT authenticates as — verify with
`GET /api/2.0/preview/scim/v2/Me`. Grant it to that principal:

```sql
GRANT EXTERNAL USE SCHEMA ON SCHEMA <catalog>.aws_cloud_cost TO `<principal>`;
```

or via the REST API:

```bash
curl -s -X PATCH -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"changes":[{"principal":"<principal>","add":["EXTERNAL USE SCHEMA"]}]}' \
  "$DATABRICKS_HOST/api/2.1/unity-catalog/permissions/schema/<catalog>.aws_cloud_cost"
```

The metastore must also have external data access enabled
(`GET /api/2.1/unity-catalog/metastore_summary` → `external_access_enabled: true`).

### Unity write is not supported (root cause)

**Unity Catalog's Iceberg REST catalog is read-only — it does not implement the
`createTable` write endpoint.** This is the actual root cause, confirmed by
[unitycatalog/unitycatalog#3](https://github.com/unitycatalog/unitycatalog/issues/3)
("Currently only read endpoints are supported"; `POST .../namespaces/{ns}/tables`
is an open, unimplemented item). So any external engine's `CREATE TABLE` over the
UC Iceberg REST API returns `403 "Not authorized to make this request"`.

Verified locally: a raw `duckdb` `CREATE TABLE` (no dbt) against the UC Iceberg
REST API returns the identical `403` at `POST .../namespaces/aws_cloud_cost/tables`
— so this is **not** dbt/Fusion, **not** a grant/`EXTERNAL USE SCHEMA` gap, **not**
an external-location issue, and **not** the DuckDB manifest bug
([#799](https://github.com/duckdb/duckdb-iceberg/issues/799) /
[PR #801](https://github.com/duckdb/duckdb-iceberg/pull/801), which only affects
manifest writing *after* a table exists). UC simply does not serve the write
endpoint. Writing Iceberg tables to Unity Catalog requires a Databricks-native
engine (Spark / Databricks SQL).

(Two later-stage DuckDB-iceberg bugs would also need the fixes in your branch once
UC adds write endpoints: manifest Avro schema #799 and vended-cred data-path scope
[#792](https://github.com/duckdb/duckdb-iceberg/issues/792). Lakekeeper's Rust REST
catalog implements writes and is lenient, which is why `lakekeeper` writes work.)

So this demo's working **write** targets are **lakekeeper** and **horizon**;
**unity** is usable only as a cross-engine **read** source until UC ships the
Iceberg REST write endpoints. The unity catalog config (incl. write-compat attach
options) is left in place for that future.

## Verification

Invoke dbt directly with your dbt binary and these project files. `.envrc` puts
the configured `DBT_BIN` and `DUCKDB_CLI` directories first on PATH after
`direnv allow`. After a run, inspect `aws_cloud_cost__daily_overview` in the
active catalog to confirm the AWS cost aggregations are populated.

Backwards compatibility with the original Fivetran package API is out of scope.
