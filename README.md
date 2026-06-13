# dbt multi-catalog demo (AWS cloud cost on DuckDB)

A clone-and-run dbt project that shows the **same models written to different
output catalogs** using dbt's catalogs v2. The transformation is a small AWS
Cost & Usage Report pipeline; the interesting part is that you can point the
models at any of several Iceberg / lakehouse catalogs by changing one config
value. The default path (`ducklake`) runs with **zero credentials**.

> Throughout, "dbt" means the locally built Fusion `dbt` binary
> (see [Build the two local binaries](#build-the-two-local-binaries)), not a
> `pip`-installed dbt Core.

## How it works

```
seeds/aws_cost_report.csv          (committed sample data; `dbt seed` loads it
        |                           into the built-in DuckDB catalog)
        v
stg_aws_cloud_cost__report_base
stg_aws_cloud_cost__report
        |
        v
aws_cloud_cost__daily_*            (output catalog = +catalog_name in dbt_project.yml)
```

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
| `unity` | Databricks Unity Catalog | 🚫 read-only upstream | n/a (reads only) |
| `s3_tables` | Amazon S3 Tables | 🧪 experimental | AWS creds |

`unity` is read-only: Unity Catalog's Iceberg REST endpoint implements no
`createTable`, so external-engine writes get `403`
([unitycatalog/unitycatalog#3](https://github.com/unitycatalog/unitycatalog/issues/3)).
`horizon`/`unity` writes also need the write-compat attach options from
[duckdb/duckdb-iceberg#1017](https://github.com/duckdb/duckdb-iceberg/pull/1017)
(shipping in DuckDB 1.5.4) — the locally built driver includes them.

## Prerequisites

- macOS or Linux
- A Rust toolchain (`cargo`) and a C++ toolchain (`make`, CMake) to build the
  two local binaries below; plus `git` for the vcpkg checkout
- `docker` — only for the `lakekeeper` catalog
- [`uv`](https://docs.astral.sh/uv/) — only for the optional Snowflake helper
  scripts (`scripts/*.sh`)
- Credentials only for whichever external catalog you want to exercise. The
  default `ducklake` path needs none.

## Build the two local binaries

This demo depends on two locally built debug binaries that are **not** published
anywhere — the prerequisite a newcomer is most likely to trip on. The absolute
paths below are examples; use wherever you checked the repos out.

1. **Custom Fusion `dbt` binary** — from an `fs` checkout/worktree:

   ```bash
   cd /path/to/your/fs            # e.g. ~/Developer/fs
   cargo build --bin dbt          # produces target/debug/dbt
   ```

   Then set `DBT_BIN=/path/to/your/fs/target/debug/dbt`.

2. **Patched `duckdb-iceberg` debug build** — from a `duckdb-iceberg` checkout.
   Two non-obvious requirements; get either wrong and `dbt run` fails in
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
DBT_BIN=/path/to/your/fs/target/debug/dbt \
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
  "ATTACH 'ducklake:./.tmp/ducklake.db' AS dl;
   SELECT * FROM dl.aws_cloud_cost.aws_cloud_cost__daily_overview LIMIT 5;"
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

`uv run pytest tests/test_demo_configuration.py` enforces the one-active-catalog
== `+catalog_name` invariant.

## External-catalog credentials

For any non-default catalog, copy the matching section from `.env.example` into
`.env`, fill it in, then uncomment that catalog's block in `catalogs.yml` and its
secret block in `profiles.yml`. `.env` is git-ignored — never commit it.

- **lakekeeper** — no credentials; `docker compose up -d` (lakekeeper + minio +
  postgres), then run. Teardown with `docker compose down -v`.
- **horizon** (Snowflake) — set the `SNOWFLAKE_*` key-pair vars, then mint a
  short-lived PAT and configure the Snowflake schema:
  ```bash
  scripts/create_horizon_pat.sh        # writes HORIZON_PAT to .env (1-day expiry)
  scripts/configure_horizon_schema.sh  # sets CATALOG=SNOWFLAKE + external volume
  scripts/doctor.sh                    # checks SQL-API + Horizon connectivity
  ```
- **polaris** — set the `POLARIS_*` vars.
- **unity** (read-only) — set `DATABRICKS_*`; reads work, writes return `403`.
- **s3_tables** (experimental) — set `AWS_S3_TABLES_*`; auth uses the standard
  AWS credential chain (configure your AWS SSO/profile).

## Regenerating the seed

The seed is committed (`seeds/aws_cost_report.csv`), so you normally never touch
it. The ShadowTraffic generator that originally produced it has been removed from
this repo; reintroduce a generator separately if you need fresh data.

## Optional diagnostics

- `scripts/direct_duckdb_catalog_probe.sh` — raw-DuckDB attach probe (bypasses dbt).

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
- **Unity writes fail (`403`)** — expected; Unity's Iceberg REST is read-only.
