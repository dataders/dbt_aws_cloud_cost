# dbt multi-catalog demo (AWS cloud cost on DuckDB)

A clone-and-run dbt project that shows the same models being written to
**different output catalogs** using dbt's catalogs v2. The transformation is a
small AWS Cost & Usage Report pipeline; the interesting part is that you can
point the final models at any of several Iceberg / lakehouse catalogs by
flipping one env var.

> Throughout this README, "dbt" refers to the locally built Fusion `dbt` binary
> (see [Build the two local binaries](#build-the-two-local-binaries)), not a
> `pip`-installed dbt Core.

## How it works

```
local_files/aws_cost_report.csv         (read with read_csv)
        |
        v
stg_aws_cloud_cost__report_base         (built-in DuckDB catalog)
        |
        v
stg_aws_cloud_cost__report              (built-in DuckDB catalog)
        |
        v
aws_cloud_cost__daily_*                 (output catalog = +catalog_name in dbt_project.yml)
```

- The **source** is a local CSV read directly via `read_csv()`. There is no large
  external source catalog to attach, so existence checks stay fast.
- **Staging** models materialize in the built-in DuckDB catalog.
- The **final** models are written to the output catalog named by `+catalog_name`
  in `dbt_project.yml` (currently `horizon`). `scripts/use_catalog.sh <name>`
  rewrites `catalogs.yml` to define that catalog (plus the CSV source); the name
  you pass must match `+catalog_name`. To switch catalogs, edit `+catalog_name`
  and regenerate `catalogs.yml` for the same name.

### Output catalogs

| Catalog      | Type                         | Write? | Setup |
| ------------ | ---------------------------- | ------ | ----- |
| `ducklake`   | DuckLake local metadata      | yes    | none (fastest path) |
| `lakekeeper` | Iceberg REST (local)         | yes    | `docker compose up -d` |
| `horizon`    | Snowflake Horizon / Polaris REST | yes | Snowflake env vars (see below) |
| `unity`      | Databricks Unity Catalog     | yes    | Databricks env vars (see below) |
| `s3_tables`  | Amazon S3 Tables REST        | experimental | AWS credentials + table bucket |

**`unity` and `horizon` writes need the write-compat attach options** from
[duckdb/duckdb-iceberg#1017](https://github.com/duckdb/duckdb-iceberg/pull/1017)
(shipping with DuckDB 1.5.4). The locally built binaries this repo uses include
them, and `scripts/use_catalog.sh` renders the right options per catalog (e.g.
`disable_multi_table_commit` for Unity). On older official extension builds the
write fails partway (Unity returns HTTP 400 on the data upload; Horizon rejects
the create), so reads work everywhere but treat writes as requiring 1.5.4+.

`polaris` is also defined in `catalogs.yml` but is no longer the demo's source —
it was replaced by the local CSV. The optional `scripts/start.sh` / `stop.sh`
still stream into a Polaris source table if you want a live-appended source.

## Prerequisites

- macOS or Linux
- `docker` (for the local lakekeeper stack and the ShadowTraffic generator)
- [`uv`](https://docs.astral.sh/uv/) for the helper Python scripts
- `jq`, `curl`, `zsh`
- A Rust toolchain (`cargo`) and a C++ build toolchain (`make`, CMake) to build
  the two local binaries below
- Credentials for any external catalog you want to exercise (Snowflake for
  `horizon`; see [Credentials](#credentials)). The local `ducklake` and
  `lakekeeper` paths need no external accounts.

## Build the two local binaries

This demo depends on two locally built debug binaries that are **not** published
anywhere — this is the prerequisite a newcomer is most likely to trip on. Build
both, then point the env vars at them (the absolute paths below are examples;
use wherever you checked the repos out).

1. **Custom Fusion `dbt` binary** — from an `fs` checkout/worktree:

   ```bash
   cd /path/to/your/fs            # e.g. ~/Developer/fs
   cargo build --bin dbt          # produces target/debug/dbt
   ```

   Then set `DBT_BIN=/path/to/your/fs/target/debug/dbt`.

2. **Patched `duckdb-iceberg` debug build** — from a `duckdb-iceberg` checkout.

   This build has two non-obvious requirements. Get either wrong and `dbt run`
   fails in confusing ways (`Unhandled options found`, unsigned-extension
   errors, or `AddressSanitizer ... loaded too late`). Both are necessary:

   **a. vcpkg** (the `avro`/`httpfs` extensions resolve their C deps through it).
   You need a **full** (non-shallow) vcpkg clone at the commit pinned in
   `duckdb-iceberg/vcpkg.json` (`builtin-baseline`) — a shallow clone fails on
   the version-pinned `openssl`/`aws-c-http` ports:

   ```bash
   git clone https://github.com/microsoft/vcpkg.git ~/Developer/vcpkg
   cd ~/Developer/vcpkg && git checkout <builtin-baseline from vcpkg.json> && ./bootstrap-vcpkg.sh
   export VCPKG_TOOLCHAIN_PATH="$HOME/Developer/vcpkg/scripts/buildsystems/vcpkg.cmake"
   ```

   **b. Build debug WITHOUT sanitizers.** A stock `make debug` enables
   AddressSanitizer, and an ASAN-instrumented `libduckdb.dylib` cannot be
   `dlopen`'d by the (non-ASAN) `dbt` binary. Always pass `DISABLE_SANITIZER=1`:

   ```bash
   cd /path/to/your/duckdb-iceberg          # e.g. ~/Developer/duckdb-iceberg
   DISABLE_SANITIZER=1 make debug           # builds build/debug/{duckdb, src/libduckdb.dylib, repository}
   ```

   Then set `DUCKDB_BUILD_DIR=/path/to/your/duckdb-iceberg`. `setup_env.sh`
   derives the CLI, driver library, and extension repository paths from it
   (`build/debug/duckdb`, `build/debug/src/libduckdb.dylib`,
   `build/debug/repository`) — each is individually overridable via
   `DUCKDB_CLI`, `DUCKDB_DRIVER_LIB`, and `DUCKDB_EXTENSION_REPOSITORY`.

This build ships `httpfs`, `iceberg`, and `ducklake` as **statically-linked
built-ins**. `setup_env.sh` symlinks the driver into `.tmp/adbc-lib/` as
`libduckdb.<dylib|so>` — the exact name dbt-fusion's loader resolves for the
DuckDB backend. If that link is missing or misnamed, dbt silently falls back to
a system/Homebrew `libduckdb` (stock DuckDB without the write-compat
Iceberg/DuckLake extensions) and catalog writes fail. The profile sets
`autoinstall_known_extensions: false` / `autoload_known_extensions: false` so
DuckDB uses these built-ins rather than fetching official extensions.

## One-time setup

From the repo root:

```bash
export DBT_BIN=/path/to/your/fs/target/debug/dbt
export DUCKDB_BUILD_DIR=/path/to/your/duckdb-iceberg

scripts/setup_env.sh        # writes ./.env (see Credentials below)
direnv allow                # loads .env and puts DBT_BIN/DUCKDB_CLI on PATH
```

`scripts/setup_env.sh` validates the two binaries, links the DuckDB ADBC driver
into `.tmp/adbc-lib`, bootstraps the local schemas, and writes `.env`. It records
`DBT_BIN` and `DUCKDB_BUILD_DIR`, so on later runs you only need to re-export them
if they change. `.env` is git-ignored and may contain live secrets — never commit
it or copy its values elsewhere.

Then generate the source data and start the local catalog infra:

```bash
docker compose up -d                 # lakekeeper + minio + postgres (only needed for the lakekeeper target)
scripts/generate_local_csv.sh 10000  # writes local_files/aws_cost_report.csv
```

`scripts/generate_local_csv.sh [ROWS]` runs ShadowTraffic to generate rows, then
writes them to `local_files/aws_cost_report.csv` with a single uniform `_modified`
timestamp (so every row counts as the latest file version, mirroring one file
export). It needs `docker`, `uv`, a ShadowTraffic license, and the DuckDB CLI.

## Credentials

`scripts/setup_env.sh` reads secrets from a private `dotfiles_env` checkout, which
defaults to `~/Developer/dotfiles_env`. **No secret values are stored in this
repo** — you supply your own. Point the script at your files with these env vars
(all optional; defaults assume the maintainer's layout):

| Env var | What it is | Default |
| --- | --- | --- |
| `DOTFILES_ENV` | Root of your private secrets checkout | `~/Developer/dotfiles_env` |
| `SNOWFLAKE_CREDENTIALS_JSON` | JSON with Snowflake account/user/key | `$DOTFILES_ENV/credentials/fusion.env.json` |
| `SHADOWTRAFFIC_LICENSE_ENV` | ShadowTraffic license env file | `$DOTFILES_ENV/shadowtraffic/license.env` |
| `POLARIS_ENV` | Shell file exporting `POLARIS_*` / `DATABRICKS_*` / `AWS_CLOUD_COST_*` secrets | `$DOTFILES_ENV/secrets.zsh` |

If you only want the local `ducklake` / `lakekeeper` targets you still need the
ShadowTraffic license (to generate the CSV) and a Snowflake credentials JSON
(setup_env.sh requires it to build `.env`), but you can ignore the
external-catalog secrets.

### External-catalog env vars

These names are read from `.env` / your environment; supply your own values.

Snowflake Horizon — provide the Snowflake SQL API values (account, user, private
key, role, warehouse) via `SNOWFLAKE_CREDENTIALS_JSON`, then optionally run
`scripts/configure_horizon_schema.sh`, `scripts/create_horizon_pat.sh`, and
`scripts/doctor.sh`. Override the derived REST endpoint with `SNOWFLAKE_CATALOG_URI`
if needed.

Amazon S3 Tables (experimental):

```bash
export AWS_REGION=us-west-2
export AWS_S3_TABLES_WAREHOUSE='arn:aws:s3tables:<region>:<account>:bucket/<bucket>'
export AWS_S3_TABLES_NAMESPACE=cloud_cost      # cannot start with the reserved "aws" prefix
export AWS_CLOUD_COST_TARGET_SCHEMA=cloud_cost
```

Databricks Unity Catalog:

```bash
export DATABRICKS_HOST='https://<workspace>.cloud.databricks.com'
export DATABRICKS_TOKEN='<personal-access-token>'
export DATABRICKS_CATALOG='<your-managed-catalog>'
export DATABRICKS_SCHEMA='aws_cloud_cost'
```

## Run and switch catalogs

The active output catalog is set by `+catalog_name` in `dbt_project.yml` (default
`horizon`). To run against it, generate a matching `catalogs.yml` then run:

```bash
# horizon — Snowflake-managed Iceberg (matches the default +catalog_name)
scripts/use_catalog.sh horizon && dbt run
```

To target a different catalog, set `+catalog_name` in `dbt_project.yml` to that
name and generate the matching `catalogs.yml`:

```bash
# ducklake — local DuckLake metadata, fastest, no infra
# (set +catalog_name: ducklake in dbt_project.yml first)
scripts/use_catalog.sh ducklake && dbt run

# lakekeeper — local Iceberg REST (needs `docker compose up -d`)
# (set +catalog_name: lakekeeper in dbt_project.yml first)
scripts/use_catalog.sh lakekeeper && dbt run
```

Use `scripts/use_catalog.sh all` to render every catalog into `catalogs.yml` (for
inspection), but for a `dbt run` always scope to a single catalog so dbt does not
attach and enumerate an unused external one.

After a run, inspect `aws_cloud_cost__daily_overview` in the active catalog to
confirm the aggregations are populated.

## Teardown

```bash
docker compose down -v               # stop and wipe the local lakekeeper stack
```

The local outputs (`.tmp/`, DuckDB/DuckLake files) are git-ignored. If you used
the optional Polaris streaming source, `scripts/stop.sh [--drop-table]` cleans
those batch files (and drops the Polaris table with `--drop-table`).

## Troubleshooting

- **`setup_env.sh` says `DBT_BIN`/`DUCKDB_BUILD_DIR` is not set** — you have not
  built and exported the two local binaries; see
  [Build the two local binaries](#build-the-two-local-binaries).
- **`missing ... credentials json` / `license env`** — point the
  `SNOWFLAKE_CREDENTIALS_JSON` / `SHADOWTRAFFIC_LICENSE_ENV` / `DOTFILES_ENV` env
  vars at your own files (see [Credentials](#credentials)).
- **`dbt` not found after setup** — run `direnv allow`; `.envrc` puts the
  `DBT_BIN` and `DUCKDB_CLI` directories on `PATH`.
- **lakekeeper target hangs or fails to attach** — confirm `docker compose up -d`
  is healthy and reachable at `http://localhost:18181`.
- **Unity writes fail with HTTP 400 on the data upload** — your `iceberg`
  extension build predates the write-compat attach options
  (duckdb/duckdb-iceberg#1017); rebuild the local binaries or use DuckDB ≥ 1.5.4.
- **`scripts/doctor.sh`** checks the Snowflake SQL API connectivity for the
  `horizon` path.
```
