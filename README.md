# dbt multi-catalog demo (AWS cloud cost on DuckDB)

A simple dbt project using dbt Core v2 and DuckDB to demonstrate the power of dbt's catalogs v2 spec.

Models can arbitrarily be written to different external Iceberg REST catalogs.

The source data is a mocked version of AWS's Cost & Usage Report pipeline.

The models are adapted from Fivetran's
[`dbt_aws_cloud_cost`](https://github.com/fivetran/dbt_aws_cloud_cost) package;
this repo repurposes them to demonstrate catalogs v2.

The default path (`ducklake`) runs with **zero credentials**.

> Throughout, "dbt" means the locally built dbt Core v2 `dbt` binary
> (see [Build the two local binaries](#build-the-two-local-binaries))

## How it works

Each model picks its output catalog with `+catalog_name`, which dbt resolves to a
block in `catalogs.yml`. Staging stays in the built-in DuckDB catalog; final models
can fan out to external catalogs (DuckLake / Horizon / Unity / …). The pipeline at a
glance:

![How catalogs.yml definitions map to catalogs and route the dbt DAG by catalog](docs/catalog-routing.png)

- The **source** is a committed seed (`seeds/aws_cost_report.csv`), loaded by
  `dbt seed`. No external source catalog to attach, so the demo runs offline.
- The models build into the **output catalog** named by `+catalog_name` in
  `dbt_project.yml`. Switching catalogs = pick a different `+catalog_name` and
  uncomment the matching block in `catalogs.yml` (see
  [Switching catalogs](#switching-catalogs)).

### Output catalogs

| Catalog | Type | Status | Needs |
| --- | --- | --- | --- |
| `ducklake` | DuckLake (local metadata) | ✅ default | nothing |
| `lakekeeper` | Iceberg REST (local) | ✅ | `docker compose up -d` |
| `horizon` | Snowflake Horizon (Polaris REST) | ✅ | Snowflake creds (`SNOWFLAKE_*`) |
| `polaris` | Iceberg REST (Polaris) | ✅ | Polaris creds (`POLARIS_*`) |
| `unity` | Databricks Unity Catalog | ✅ | Databricks creds (`DATABRICKS_*`) |
| `s3_tables` | Amazon S3 Tables | 🧪 driver-only (Fusion adapter support pending) | AWS creds |

`horizon` and `unity` writes need the write-compat attach options from
[duckdb/duckdb-iceberg#1017](https://github.com/duckdb/duckdb-iceberg/pull/1017)
(shipping in DuckDB 1.5.4) — the locally built driver includes them, and the
`catalogs.yml` blocks set `read_only: false` (+ `disable_multi_table_commit`).
`horizon` additionally requires **key-pair auth** (a PAT can read but not write)
and an **uppercase** target schema (`CATALOG_SCHEMA=AWS_CLOUD_COST`) — see
[External-catalog credentials](#external-catalog-credentials). Unity here is
**proprietary Databricks** Unity Catalog; OSS `unitycatalog/unitycatalog` is a
separate target tracked in
[#2](https://github.com/dataders/dbt_aws_cloud_cost/issues/2).

## Quick start

The default `ducklake` path needs **no credentials**.

### Prerequisites

- macOS or Linux
- A Rust toolchain (`cargo`) and a C++ toolchain (`make`, CMake) to build the
  two local binaries below; plus `git` for the vcpkg checkout
- `docker` — only for the `lakekeeper` catalog
- [`uv`](https://docs.astral.sh/uv/) — only for the optional Snowflake helper
  scripts (`scripts/*.sh`)
- Credentials only for whichever external catalog you want to exercise. The
  default `ducklake` path needs none.

### Steps

1. **Build the two local debug binaries** (not published anywhere) and note their
   paths — the Fusion `dbt` binary and the `duckdb-iceberg` driver. This is the
   step newcomers trip on; follow
   [Build the two local binaries](#build-the-two-local-binaries) exactly
   (vcpkg + `DISABLE_SANITIZER=1`).
2. **Write `.env`** by pointing `setup.sh` at those two paths:
   ```bash
   DBT_BIN=/path/to/dbt-fusion/target/debug/dbt \
   DUCKDB_BUILD_DIR=/path/to/duckdb-iceberg \
   scripts/setup.sh
   source .env                            # or: set -a && source .env && set +a
   ```
3. **Run the demo** (builds into the zero-credential `ducklake` catalog):
   ```bash
   "$DBT_BIN" seed                        # load the committed seed
   "$DBT_BIN" run                         # build the models into ducklake
   ```
4. **Inspect the output** — see [Setup and run](#setup-and-run).
5. **(optional) Try another catalog** — pick one of `lakekeeper` / `horizon` /
   `polaris` / `unity`, one at a time, per
   [Switching catalogs](#switching-catalogs). Verified writing: ducklake,
   lakekeeper, polaris, horizon (Snowflake), unity (Databricks).

## Build the two local binaries

This demo depends on two locally built debug binaries that are **not** published
anywhere — the prerequisite a newcomer is most likely to trip on. The absolute
paths below are examples; use wherever you checked the repos out.

1. **Custom Fusion `dbt` binary** — from a Fusion (`dbt-fusion`) checkout that
   includes the **catalogs v2 read-write** work: dbt-core
   [#15239](https://github.com/dbt-labs/dbt-core/pull/15239) ("catalogs.yml v2
   part 2 — Horizon & Unity read-write", stacked on part 1
   [#15238](https://github.com/dbt-labs/dbt-core/pull/15238)). Until that ships
   in a published build, compile it from a branch that has it:

   ```bash
   cd /path/to/your/dbt-fusion    # e.g. ~/Developer/dbt-fusion, on the catalogs-v2 branch
   cargo build --bin dbt          # produces target/debug/dbt
   ```

   Then set `DBT_BIN=/path/to/your/dbt-fusion/target/debug/dbt`.

2. **`duckdb-iceberg` debug build** — from the **`v1.5-variegata` branch** of
   [`duckdb/duckdb-iceberg`](https://github.com/duckdb/duckdb-iceberg/tree/v1.5-variegata),
   the DuckDB 1.5.4 line. It carries duckdb-iceberg
   [#1017](https://github.com/duckdb/duckdb-iceberg/pull/1017) /
   [#1018](https://github.com/duckdb/duckdb-iceberg/pull/1018) /
   [#1020](https://github.com/duckdb/duckdb-iceberg/pull/1020) — the write-compat
   options the v2 Horizon/Unity catalogs need, which aren't in a stable DuckDB
   release yet. A plain `main`/release checkout will not work.

   ```bash
   git clone -b v1.5-variegata https://github.com/duckdb/duckdb-iceberg.git
   ```

   Two more non-obvious requirements; get either wrong and `dbt run` fails in
   confusing ways (`Unhandled options found`, unsigned-extension errors, or
   `AddressSanitizer ... loaded too late`):

   **a. vcpkg.** The `avro`/`httpfs` extensions resolve C deps through it. Use a
   **full (non-shallow)** vcpkg clone at the commit pinned in
   `duckdb-iceberg/vcpkg.json` (`builtin-baseline`) — a shallow clone fails on
   the version-pinned `openssl`/`aws-c-http` ports:

   ```bash
   git clone https://github.com/microsoft/vcpkg.git ~/Developer/vcpkg
   cd ~/Developer/vcpkg && git checkout <builtin-baseline from vcpkg.json> && ./bootstrap-vcpkg.sh
   export VCPKG_TOOLCHAIN_PATH="$HOME/Developer/vcpkg/scripts/buildsystems/vcpkg.cmake"
   ```

   **b. Build WITHOUT sanitizers.** A stock `make debug` enables AddressSanitizer,
   and an ASAN `libduckdb.dylib` cannot be `dlopen`'d by the (non-ASAN) `dbt`
   binary. Always pass `DISABLE_SANITIZER=1`:

   ```bash
   cd /path/to/your/duckdb-iceberg
   DISABLE_SANITIZER=1 make debug     # builds build/debug/{duckdb, src/libduckdb.dylib, repository}
   ```

   Then set `DUCKDB_BUILD_DIR=/path/to/your/duckdb-iceberg`.

This build ships `httpfs`, `iceberg`, and `ducklake` as **statically-linked
built-ins**. dbt loads the driver from `ADBC_REPOSITORY`, which points straight
at `build/debug/src` (that dir already contains `libduckdb.dylib`). The profile
sets `autoinstall_known_extensions: false` / `autoload_known_extensions: false`
so DuckDB uses those built-ins instead of fetching official extensions — without
that, dbt would load a stock DuckDB and catalog writes would fail.

## Setup and run

From the repo root, point at your two binaries and let `setup.sh` write `.env`:

```bash
DBT_BIN=/path/to/your/dbt-fusion/target/debug/dbt \
DUCKDB_BUILD_DIR=/path/to/your/duckdb-iceberg \
scripts/setup.sh

set -a && source .env && set +a        # load it (or: direnv allow)

"$DBT_BIN" seed                        # load the committed seed (built-in catalog)
"$DBT_BIN" run                         # build the models into the ducklake catalog
```

`setup.sh` validates the binaries and writes a credential-free `.env` (it won't
overwrite an existing one). Prefer to fill it in by hand? Copy `.env.example` to
`.env` and edit the two paths instead — `setup.sh` is just a convenience.

Inspect the result (any DuckLake-1.0-capable DuckDB):

```bash
"$DUCKDB_CLI" :memory: -c \
  "ATTACH 'ducklake:./data/ducklake.db' AS dl;
   SELECT * FROM dl.aws_cloud_cost.daily_overview LIMIT 5;"
```

## Switching catalogs

The active catalog is pinned in two places that **must agree**: `+catalog_name`
in `dbt_project.yml`, and the single uncommented block in `catalogs.yml` (Fusion
attaches every catalog in that file, so exactly one stays active). To switch,
e.g. to `lakekeeper`:

1. In `catalogs.yml`: comment the current block and uncomment the `lakekeeper`
   one (strip the leading `# ` from each line).
2. In `dbt_project.yml`: set `+catalog_name: lakekeeper`.
3. For a credentialed catalog only: also uncomment its secret block in
   `profiles.yml` and supply its env vars (see below), then re-`source .env`.
4. `"$DBT_BIN" run`.

`uv run tests/test_demo_configuration.py` enforces the one-active-catalog
== `+catalog_name` invariant.

## External-catalog credentials

For any non-default catalog, copy the matching section from `.env.example` into
`.env`, fill it in, then uncomment that catalog's block in `catalogs.yml` and its
secret block in `profiles.yml`. `.env` is git-ignored — never commit it.

The tables below list the permissions the catalog's principal needs to **write**
from an external engine (reads need a subset). Privilege names follow each
provider's own docs — treat them as the minimum to get the demo writing, not an
exhaustive security policy.

### lakekeeper (local — no credentials)

`docker compose up -d` (lakekeeper + minio + postgres), then run; teardown with
`docker compose down -v`. No cloud permissions: the warehouse attaches with
`AUTHORIZATION_TYPE NONE` and the static minio keys baked into `profiles.yml`.

### horizon (Snowflake)

Set the `SNOWFLAKE_*` key-pair vars, then:

```bash
scripts/configure_horizon_schema.sh  # one-time: catalog-linked schema + writable external volume
scripts/refresh_horizon_token.sh     # mints a KEY-PAIR access token -> HORIZON_ACCESS_TOKEN (~55 min)
scripts/doctor.sh                    # checks SQL-API + Horizon connectivity
```

| Object | Privilege / setting | Why |
| --- | --- | --- |
| user | **key-pair (RSA) auth** | a PAT can *read* but `createTable` 403s on write |
| schema | `CATALOG = 'SNOWFLAKE'` (catalog-linked) + `CREATE ICEBERG TABLE` | models create Iceberg tables here |
| schema name | **UPPERCASE** (`AWS_CLOUD_COST`) | the REST namespace is case-sensitive on write |
| external volume | `ALLOW_WRITES = TRUE` + `USAGE` granted to the role | the external engine writes data files here — not `SNOWFLAKE_MANAGED` (its internal storage can't be written externally) |
| warehouse + database | `USAGE` | resolve and run |
| region | `SNOWFLAKE_DEFAULT_REGION` = the volume's region (`us-east-1`) | must match the external volume |

Keep `+schema: aws_cloud_cost` so the `generate_schema_name` macro substitutes
the uppercase `CATALOG_SCHEMA`. Re-run `refresh_horizon_token.sh` when the token
expires.

### polaris (Iceberg REST)

Set the `POLARIS_*` vars. The principal's catalog role needs namespace + table
write privileges:

| Privilege | Scope |
| --- | --- |
| `TABLE_CREATE`, `TABLE_WRITE_DATA` | catalog / namespace |
| `NAMESPACE_CREATE` | catalog |
| `PRINCIPAL_ROLE:ALL` (or a role granting the above) | principal |

### unity (Databricks)

Set `DATABRICKS_*`; reads **and writes** work. The token's principal needs, on the
target catalog and schema:

| Object | Privilege |
| --- | --- |
| catalog | `USE CATALOG` |
| schema | `USE SCHEMA`, `CREATE TABLE` |
| schema | `EXTERNAL USE SCHEMA` (required for external Iceberg-REST clients) |

The `catalogs.yml` block also sets `read_only: false` + `disable_multi_table_commit`
(without them `createTable` 403s — which is what made it look read-only).
Databricks UC accepts either schema case. (OSS Unity Catalog is a separate
target — see [#2](https://github.com/dataders/dbt_aws_cloud_cost/issues/2).)

### s3_tables (Amazon S3 Tables — experimental)

Set `AWS_S3_TABLES_*`; auth uses the standard AWS credential chain (configure your
AWS SSO/profile). **The Fusion adapter does not accept `type: s3_tables` yet**
(support pending) — the driver already works
(`ATTACH '<bucket-arn>' (TYPE iceberg, ENDPOINT_TYPE 's3_tables')`), and writes
benchmark as the fastest of the remote catalogs (~1.8s/table, vs Unity ~5.5s,
Horizon ~12s). The IAM principal needs:

| Action (or managed policy) | Scope |
| --- | --- |
| `AmazonS3TablesFullAccess` (managed) — or the granular actions below | table bucket |
| `s3tables:CreateNamespace`, `CreateTable`, `GetTable`, `GetTableMetadataLocation`, `UpdateTableMetadataLocation` | table bucket |
| `s3tables:GetTableData`, `PutTableData` | table data |

A table bucket + namespace must already exist
(`aws s3tables create-table-bucket` / `create-namespace`). Note: live AWS creds
and the local `lakekeeper` (minio) target can't be active in the same run.

## Regenerating the seed

The seed is committed (`seeds/aws_cost_report.csv`), so you normally never touch
it. The ShadowTraffic generator that originally produced it has been removed from
this repo; reintroduce a generator separately if you need fresh data.

## Diagnostics

- `scripts/doctor.sh` — checks SQL-API auth + Horizon catalog connectivity.

## Troubleshooting

- **`Unhandled options found` / unsigned-extension errors on a run** — dbt loaded
  a stock DuckDB instead of your local build. Confirm `ADBC_REPOSITORY` points at
  your `duckdb-iceberg/build/debug/src` and that `DISABLE_CDN_DRIVER_CACHE=true`.
- **`AddressSanitizer ... loaded too late`** — rebuild duckdb-iceberg with
  `DISABLE_SANITIZER=1 make debug`.
- **catalog name mismatch** — `+catalog_name` in `dbt_project.yml` must equal the
  single uncommented catalog in `catalogs.yml` (the pytest invariant checks this).
- **OAuth errors on a local run** — you uncommented a secret block in
  `profiles.yml` without supplying its credentials; re-comment it (the default
  `ducklake` path needs none).
- **lakekeeper hangs / can't attach** — confirm `docker compose up -d` is healthy
  at `http://localhost:18181`.
- **Unity writes fail (`403`)** — the unity `catalogs.yml` block is missing
  `read_only: false` / `disable_multi_table_commit: true`. Unity writes *do* work
  with both set; without them `createTable` 403s (which originally looked like
  read-only).
