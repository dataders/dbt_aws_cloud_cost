# Two switchable dbt-compute + MDLS demo scenarios

## Goal

Two branches of this repo, each a working "happy path" demo, switchable by
checking out the other branch:

1. **`alt-compute-only`** — the entire project runs on the dbt-compute Alt
   engine and writes to MDLS as its default destination. No `catalogs.yml`.
2. **`add-mdls-dbt-compute-target`** (current branch, continued) — a 3-stage
   mixed-compute DAG: Snowflake → Alt engine → Snowflake, all materializing
   into the same MDLS destination via the catalog-linked database (CLD).

Also: document, as exhaustively as current fs/quack source supports, what dbt
features the Alt engine (`type: alt` / dbt-compute) does and does not support.

## Background / constraints (from reading fs + quack source directly)

There is no public documentation for dbt-compute; this is an internal,
staging-only Fivetran/dbt Labs product. All claims below are sourced from
`~/Developer/fs` (the dbt Fusion Rust codebase) and `~/Developer/quack` (the
dbt-compute service + its own canonical example projects at
`quack/examples/{dbt_compute_demo,iceberg_snowflake_demo,pure_snowflake_demo}`).

**Two distinct ways to put a model on the Alt engine, with different
maturity:**

- **Routing (`+alt_compute: alt` on specific models)** — the maintained,
  feature-complete path (`crates/dbt-tasks/src/local_engine/runnable/
  compute_platform.rs` in `fs`). Requires: `use_catalogs_v2: true`, the
  *default* profile to be Snowflake/DuckDB/Alt, a resolvable `catalog_name` on
  the model, and materialization ∈ {table, view, incremental (append /
  insert_overwrite only)}. This is what `quack/examples/dbt_compute_demo`
  demonstrates (Snowflake default target purely for credential-minting +
  `+alt_compute: alt` on every model + `catalog_name` pointing at a `horizon`
  catalog).
- **Bare `type: alt` default target (no routing)** — models execute through
  the generic Jinja-macro adapter dispatch instead of `compute_platform.rs`.
  fs's `dbt-alt` macro package (`crates/dbt-loader/src/dbt_macro_assets/
  dbt-alt/`) has **no macro files at all** — only a `dbt_project.yml`. Setting
  `+catalog_name` in this mode is known-broken: `catalog_relation_v2.rs` has
  no dispatch arm for `AdapterType::Alt`, so `build_relation_catalog` errors
  (this matches a comment already in this repo's `dbt_project.yml` from an
  earlier `dbt_compute` test run). Not exercised by any canonical fs/quack
  example — chosen for Scenario 1 anyway per explicit user direction, with the
  caveat that materializations may not work and need real verification.

**Resolver rule for mixed-compute DAGs** (`fs/sa/crates/dbt-parser/src/
resolver.rs`, `check_compute_platform_upstreams` / `upstream_is_catalog_
reachable`): any model with `+alt_compute: alt` must have every `ref`/`source`
upstream be catalog-reachable — i.e. the upstream has `catalog_name` set, is
`table_format: iceberg`, or is itself `alt_compute: alt`. Otherwise: hard
parse error. Sources are exempt from this check.

**Seeds**: no `+alt_compute` config field exists on `seed_config.rs` at all —
structurally impossible to route a seed onto the Alt engine via
`alt_compute`. Seeds *do* work when a profile's default/primary target is
itself `type: alt` (unrelated to routing — `seed_io.rs` has Alt-aware column
casing). Separately, and unrelated to the Alt engine at all: Snowflake's
catalog-linked database rejects `dbt seed`'s multi-statement-transaction load
against unmanaged Iceberg tables — this repo already works around that with a
plain non-CLD `SEEDS` database (`macros/create_seed_db.sql` +
`+database: SEEDS` on the seeds config). That workaround is only relevant when
Snowflake/CLD is involved (Scenario 2); irrelevant for Scenario 1.

**Feature-support table** (to land in README, see Deliverables):

| Feature | Alt engine (`alt_compute: alt` routing) support |
|---|---|
| Materializations | table, view, incremental only |
| Incremental strategies | `append`, `insert_overwrite` only — merge/delete+insert/microbatch/replace_where explicitly rejected at runtime |
| Custom materializations | Pass parse-time validation, **always fail at runtime** |
| Seeds | Not routable (no config field); works if the whole profile is bare `type: alt` |
| Snapshots | Rejected at parse time |
| Python models | Rejected at parse time |
| Grants / contracts / constraints / persist_docs | Config accepted; execution path bypasses macro dispatch entirely — moderate-confidence silent no-op, not an error |
| Write-target catalog types | `iceberg_rest`, `horizon` only — others (`glue`, `unity`, `ducklake`) hit a hard `unimplemented!()` panic |
| Mixed-compute DAGs | Supported via the catalog-reachability resolver rule above |
| Driver distribution | No CDN release yet — requires a locally-built `adbc_driver_dbt` |

This table describes the **routing** path only. Scenario 1 below uses the
other, unmaintained mechanism (bare `type: alt` default target, no routing) —
none of these rows are known to apply to it; that path is genuinely
undocumented upstream.

## Scenario 1: `alt-compute-only` branch

Branched from `main` (not from the mixed-compute branch — avoids carrying
Scenario 2's per-model `catalog_name`/`alt_compute` overrides).

- `profiles.yml`: `main`'s profile is DuckDB-only today (no `dbt_compute`
  output exists there yet). Port the `dbt_compute` output block from this
  branch's history (introduced in `8ef7b64`, base URL fixed in `2b97751`)
  over onto `main`'s profile, then point `target` directly at it (no
  `x_alt_target`) — same connection fields as it has today: `base_url`,
  `method: token`, `token`, `organization`, `database`, `schema`, `threads`,
  sourced from `DBT_COMPUTE_BASE_URL`, `DBT_COMPUTE_AUTH_TOKEN`,
  `DBT_COMPUTE_ORG`, `DBT_COMPUTE_DATABASE`, `DBT_COMPUTE_SCHEMA`.
- `dbt_project.yml`: no `+catalog_name`, no `+alt_compute` anywhere;
  `use_catalogs_v2` off (not needed — routing is the only thing that requires
  it).
- `catalogs.yml`: left fully intact (all catalog blocks, including
  `ducklake`/`lakekeeper`/`horizon`/`unity`/`s3_tables`, stay as they are on
  `main`) but **unreferenced** — no model sets `+catalog_name`, so the file
  has no effect on this branch's run. Not deleted; out of scope to touch its
  contents (see Out of scope).
- Seeds: no special database/workaround — load natively via the Alt
  connection's own `database`/`schema`.
- Models: plain `+materialized: table` (`main`'s current baseline), no
  per-model catalog config.
- README: new section documenting this path, its prerequisites (fs-built
  `dbt` binary, locally-built `adbc_driver_dbt`, the five `DBT_COMPUTE_*` env
  vars above), and the explicit caveat that this is the less-exercised of the
  two Alt patterns (see Background — the feature-support table doesn't apply
  here).

## Scenario 2: `add-mdls-dbt-compute-target` branch (continued)

Already 3 commits ahead of `main` with most of the wiring in place. Changes
needed:

- Fix leaf-node routing: move `+alt_compute: alt` off the two leaf models
  (`daily_instance_report`, `daily_product_report`) and onto the middle model
  (`daily_overview`) instead, so the DAG is a real 3-stage handoff:

  ```
  stg_report (Snowflake, catalog_name=mdls)
    → daily_overview (alt_compute=alt, catalog_name=mdls)   [writes to Polaris/MDLS directly]
        → daily_instance_report, daily_product_report (Snowflake, catalog_name=mdls)  [read back via CLD]
  ```

  `catalog_name='mdls'` + `iceberg_version='3'` stay on all four models
  (`stg_report`, `daily_overview`, `daily_instance_report`,
  `daily_product_report`) as they are today.

  **Risk**: this inverts the data-flow direction from what commit `cda2f69`
  actually verified end-to-end (Snowflake writes, Alt-engine leaf reads back).
  The new direction — Alt engine writes Iceberg, Snowflake CLD reads back —
  is unverified and is exactly what the Verification section below needs to
  confirm before calling this branch done.
- Fold in the uncommitted working-tree cleanup already sitting on this branch
  (`SNOWFLAKE_DEMO_*` → `SNOWFLAKE_*` env var rename, dropped unused `horizon`
  catalog block) as part of this work.
- `profiles.yml` / `catalogs.yml` / `dbt_project.yml`: no structural changes
  beyond what's already there (target `snowflake_demo`, `x_alt_target:
  dbt_compute`, `mdls` catalog block).
- README: new section documenting this 3-stage scenario with the DAG diagram
  above.

## Verification

Both branches get run for real against the local debug builds the user
already has, rather than eyeballed as YAML:

- `dbt` binary: `/Users/dataders/Developer/fs/target/debug/dbt`
- Alt ADBC driver: `/Users/dataders/Developer/quack/target/release/
  libadbc_driver_dbt.dylib` (via `ADBC_REPOSITORY`)

Following the env-var pattern from `quack/examples/*/run-staging.sh`
(`DBT_COMPUTE_AUTH_TOKEN`, `DBT_COMPUTE_BASE_URL`, Snowflake key-pair vars,
`ADBC_REPOSITORY`, `DISABLE_AUTO_DRIVER_REBUILD=true`). `dbt seed && dbt run`
on each branch; confirm rows land in the expected destination for each stage.

## Out of scope

- No changes to the *content* of the `unity`/`s3_tables`/`ducklake`/
  `lakekeeper`/`horizon` catalog blocks in `catalogs.yml` on either branch —
  Scenario 1 leaves the file in place unreferenced rather than editing it.
- No changes to the dbt-compute service itself (`quack` repo) — demo-project
  changes only.
- Not attempting to make grants/contracts/constraints work on the Alt engine
  — documenting the limitation is sufficient.
