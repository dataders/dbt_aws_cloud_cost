# Demo simplification + documentation design

**Date:** 2026-06-12
**Status:** Approved by user (pending implementation)

## Goal

A dbt Labs colleague can clone this repo, build the two local binaries, and get
a successful `dbt seed && dbt run` against the `ducklake` catalog with **zero
external credentials**. External catalogs (lakekeeper, horizon, polaris, unity,
s3_tables) are opt-in, each documented with an honest status label and its own
credential section.

## Audience and scope

- **Audience:** dbt Labs colleagues. They have access to the `fs` repo, a Rust
  toolchain, and possibly Snowflake/Databricks accounts. Docs may assume
  internal context but must not assume the maintainer's machine layout.
- **Scope:** simplify the setup (remove dead files/scripts, fix footguns,
  loosen credential requirements) and rewrite the docs around the cleaned-up
  flow. No changes to the model SQL beyond what the seed switch requires.

## Decisions (settled with user)

| Topic | Decision |
| --- | --- |
| Source data | Commit sample data as a **dbt seed** (`seeds/aws_cost_report.csv`, ~10k rows, uniform `_modified`). Seed **replaces** the `read_csv()` path entirely. |
| catalogs.yml | One static checked-in file containing every catalog block; inactive blocks are **commented out**. Exactly one output catalog active at a time. Default: `ducklake`. |
| use_catalog.sh | **Deleted.** Switching catalogs is a manual two-edit operation (uncomment block in `catalogs.yml`, set `+catalog_name` in `dbt_project.yml`), documented side by side in the README. |
| Catalog lineup | Keep all: ducklake, lakekeeper, horizon, polaris, unity, s3_tables — with honest status labels (see matrix below). |
| Credentials | **Fully optional** for the local path: `setup_env.sh` hard-fails only on missing binaries; `profiles.yml` no longer eagerly creates OAuth secrets. |

## Components

### 1. Seed (replaces read_csv source)

- Generate ~10k rows and commit as `seeds/aws_cost_report.csv`.
- Pin column types via seed config (`+column_types`), replacing the casts
  currently supplied by the `get_aws_cloud_cost_report_columns` macro for the
  read path. Delete the macro if nothing else uses it.
- `models/staging/base/stg_aws_cloud_cost__report_base.sql` selects from
  `{{ ref('aws_cost_report') }}` instead of `read_csv(...)`.
- `scripts/generate_local_csv.sh` (ShadowTraffic, optional) writes to
  `seeds/aws_cost_report.csv`; regeneration is "run the script, re-run
  `dbt seed`".
- Remove the `local_files` source entry / catalog block and any
  `local_files/` references.
- **Risk gate:** smoke-test `dbt seed` with the locally built Fusion binary
  *first*. If seeds misbehave on this debug fork, fall back to committing the
  CSV under `local_files/` and keeping `read_csv()` (the rest of the design is
  unaffected).

### 2. catalogs.yml (one file, commented blocks)

- Header comment explains the invariant: Fusion attaches **every** defined
  catalog, so keep exactly one output catalog uncommented, and its name must
  match `+catalog_name` in `dbt_project.yml`.
- Blocks for: ducklake (active by default), lakekeeper, horizon, polaris,
  unity, s3_tables. Credential-bearing blocks keep `{{ env_var(...) }}`
  references — harmless while commented.
- Delete `catalogs copy.yml` and `catalogs.yml.bak`.

### 3. dbt_project.yml

- Default `+catalog_name: ducklake`, with a comment listing valid names and
  pointing at `catalogs.yml`.

### 4. profiles.yml

- Remove eager `CREATE SECRET` statements from the always-run init path so a
  ducklake run never touches OAuth tokens. Each external catalog's secret
  setup lives with its catalog block / README section instead.

### 5. setup_env.sh

- Hard-fails only on `DBT_BIN` / `DUCKDB_BUILD_DIR`.
- Missing Snowflake JSON, ShadowTraffic license, or `dotfiles_env` secrets
  produce **warnings**, and `.env` is written with whatever was found.

### 6. Scripts cleanup

- Delete: `use_catalog.sh`.
- Keep but label as optional diagnostics (README section, not setup path):
  `feature_compat_probe.py`, `direct_duckdb_catalog_probe.sh`,
  `start.sh`/`stop.sh` (Polaris streaming), `doctor.sh`,
  `configure_horizon_schema.sh`, `create_horizon_pat.sh`.

### 7. README rewrite

Structure:

1. What the demo shows (keep the pipeline diagram, updated for the seed).
2. **Quickstart** — clone → build two binaries → `setup_env.sh` →
   `direnv allow` → `dbt seed && dbt run` (ducklake, zero credentials).
3. **Catalog matrix** with honest statuses:
   - `ducklake` ✅ local, no infra
   - `lakekeeper` ✅ local, needs `docker compose up -d`
   - `horizon` ✅ needs Snowflake credentials
   - `polaris` ✅ needs Polaris credentials
   - `unity` 🚫 **read-only upstream** — UC's Iceberg REST does not implement
     `createTable` (link unitycatalog/unitycatalog#3); usable as a read
     source only
   - `s3_tables` 🧪 experimental, needs AWS credentials
4. Switching catalogs: the two edits, shown side by side.
5. Per-catalog setup sections (credentials, docker, PAT scripts).
6. Optional diagnostics (probe scripts, Polaris streaming source).
7. Troubleshooting — updated; drop stale `use_catalog.sh` / env-var-switching
   references.

### 8. tests/test_demo_configuration.py

Update invariants:

- Exactly one active (uncommented) output catalog in `catalogs.yml`, and it
  matches `+catalog_name` in `dbt_project.yml`.
- `seeds/aws_cost_report.csv` exists and is non-trivial.
- No references to deleted scripts/files remain in scripts/docs.

## Error handling

- A wrong/missing `+catalog_name` ↔ `catalogs.yml` pairing is caught by the
  pytest invariant and documented in troubleshooting.
- Missing external credentials only fail at the point a credentialed catalog
  is actually activated — never on the local path.

## Verification

With all external env vars unset:

1. `dbt parse`, `dbt seed`, `dbt run` against ducklake — green.
2. `pytest tests/test_demo_configuration.py` — green.
3. With docker up: switch to lakekeeper (the two edits) and `dbt run` — green.
4. README walkthrough sanity check: every command in Quickstart is runnable
   verbatim from a fresh clone (given the two built binaries).
