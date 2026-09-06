# dbt + MDLS + Lake Compute demo (AWS cloud cost)

A dbt project demonstrating Fivetran's Managed Data Lake Service (MDLS) and
its internal **Lake Compute** service (a `type: lakecompute` dbt-fusion
adapter) alongside native Snowflake, using dbt-fusion 2.0's multi-adapter
profile targets.

The source data is a mocked version of AWS's Cost & Usage Report pipeline.
The models are adapted from Fivetran's
[`dbt_aws_cloud_cost`](https://github.com/fivetran/dbt_aws_cloud_cost) package.

> Throughout, "dbt" means the dbt Fusion (dbt Core v2) `dbt` CLI, **built
> locally from latest `fs` main** (see [Install dbt](#install-dbt)) — the
> published CDN CLI doesn't know the `lakecompute` adapter type,
> multi-adapter targets, or `+adapter`/`+propagate` yet.

## How it works

A single profile target (`prod`) declares **two adapter connections** —
`snowflake` (the default) and `lakecompute` — instead of the old one-target-
per-engine shape. Models opt into LakeCompute per-model with `+adapter:
lakecompute`; everything else runs on the default Snowflake connection. A
model written via LakeCompute can hand its result back to native Snowflake
with `+propagate: snowflake`, so downstream models just `ref()` it like any
other native table — no catalog-linked database, no `catalogs.yml`, no
Iceberg-catalog wiring required on this branch.

```
stg_report (Snowflake)
  -> daily_overview (adapter=lakecompute, propagate=snowflake)   [writes to Polaris/MDLS, propagates back]
      -> daily_instance_report, daily_product_report (Snowflake)  [read the propagated native table]
```

See `profiles.yml` for the `prod` target and `models/daily_overview.sql` for
the routing config.

### LakeCompute feature support

There's no public documentation for Lake Compute, so the table below is
sourced directly from the `fs`/`quack` codebases.

| Feature | LakeCompute (`adapter: lakecompute` routing) support |
| --- | --- |
| Materializations | `table`, `view`, `incremental` only |
| Incremental strategies | `append`, `insert_overwrite` only — `merge`/`delete+insert`/`microbatch`/`replace_where` explicitly rejected at runtime |
| Custom materializations | Pass parse-time validation, **always fail at runtime** — a real gap between the two layers |
| Seeds | Not routable (no `+adapter` config field on seeds at all) |
| Snapshots | Rejected at parse time |
| Python models | Rejected at parse time |
| Grants / contracts / constraints / persist_docs | Config accepted; the execution path bypasses macro dispatch entirely and never references them — moderate-confidence silent no-op, not an error |
| Write-target catalog types | `iceberg_rest`, `horizon` only — `glue`/`unity`/`ducklake` hit a hard `unimplemented!()` panic |
| Mixed-compute DAGs | Supported via a resolver rule: any `adapter: lakecompute` model's upstreams must each be catalog-attached or themselves `adapter: lakecompute` — otherwise a hard parse error |
| Driver distribution | Available via the driver CDN — no local build of the ADBC driver required (the `dbt` binary itself does — see [Install dbt](#install-dbt)) |
| Auth methods | `fivetran` (`fivetran_credential` — a `dct_...` self-service PAT, exchanged for a short-lived JWT; added in [fs#14421](https://github.com/dbt-labs/fs/pull/14421) — **this is the one to use, against `base_url: https://api.lake-compute.fivetran.com/`**, see [Verified status](#verified-status)); `token` (a bearer JWT used as-is); `api_key`; `okta_browser` |

`lakecompute` was renamed from `alt`/`lake_compute` in
[dbt-labs/fs#14380](https://github.com/dbt-labs/fs/pull/14380). As of
dbt-fusion 2.0.0-preview.219, a profile target can list multiple adapter
connections (`outputs.<target>` becomes a YAML list) instead of one flat
mapping; exactly one entry needs `default: true`, and the rest are opted into
per-model via `+adapter: <type>`.

## Verified status

**Full DAG confirmed end-to-end on `ktb38830`, 2026-09-05** — `dbt seed` and
`dbt run --target prod` both succeed for all 4 models, including the
LakeCompute leg:

```
stg_report              10000 rows
daily_overview          10000 rows   (via LakeCompute, propagated back to Snowflake)
daily_instance_report     144 rows
daily_product_report     576 rows
```

Getting there required two fixes beyond this repo's SQL/config alone — worth
knowing if you're setting this up against a fresh account or a fresh
`LAKE_COMPUTE_AUTH_TOKEN`:

1. **Don't set `LAKE_COMPUTE_BASE_URL` at all** — leave it unset so the
   `lakecompute` connection falls back to its built-in default, Fivetran's
   **production** dbt-compute API (`https://api.lake-compute.fivetran.com/`).
   Earlier revisions of this demo pinned it explicitly to the **staging**
   host (`api.dbt-compute.staging.fivetran.com`), and `method: fivetran` +
   `fivetran_credential` (a `dct_...` self-service PAT, exchanged for a
   short-lived JWT — see [fs#14421](https://github.com/dbt-labs/fs/pull/14421)
   and `quack/docs/authentication.md`) 401s there: Layer A (`POST
   https://api.fivetran.com/partner/v1/dbt-compute/token-exchange`) succeeds
   and returns a valid, correctly-scoped JWT, but staging's own JWT
   verification rejects it before the request ever reaches Fivetran's
   credential-vending endpoint — confirmed with raw `curl` at every hop,
   independent of dbt/fs. Against production, the identical JWT is accepted
   (`202`) and the full flow (auth, query execution, credential vending,
   propagation back to Snowflake) works end-to-end — this matches
   `quack/docs/dbt-compute-production-fivetran-auth-test-report-2026-09-02.md`,
   which independently validated the same production path. So the fix is
   just the base URL (by omission), not the credential, the auth method, or
   anything else in this repo's dbt config. Do **not** skip `database`/
   `schema` on the `lakecompute` connection, though — they're the MDLS-side
   namespace and don't inherit from the `snowflake` connection; omitting
   them makes `dbt debug`'s write succeed but its read-back fail (write and
   read silently land in different implicit namespaces).
2. **Snowflake network policy** (account-side, not this repo). LakeCompute's
   write-visibility propagation step back to Snowflake failed with
   `Propagation failed for ... Network policy is required` until *some*
   network policy was assigned to the connecting user — Snowflake requires
   this before it will mint a PAT for the propagation step, independent of
   IP allowlisting specifics. Fix: `ALTER USER <user> SET NETWORK_POLICY =
   <any existing allow-all policy>` (only an account admin can do this).

These fixes carried over cleanly from a previous account/token — the actual
model SQL and materialization logic needed no changes for the account swap:

3. **Timestamp precision.** The LakeCompute write path creates Iceberg **v2**
   tables regardless of a model's `iceberg_version` config — that config
   simply doesn't propagate through LakeCompute's write path. v2 caps
   timestamp precision at microseconds, and `billing_period_start_date`/
   `billing_period_end_date` arrive as nanosecond-precision timestamps, so
   `daily_overview` casts them to `date` (the same treatment
   `usage_start_date`/`usage_end_date` already got).
4. **Mixed-case output columns.** LakeCompute's write path derives each
   output column's stored name from *how it's selected*: a bare passthrough
   column (e.g. `source_report.source_relation`) keeps whatever case the
   upstream native-Snowflake table already stores it in (uppercase,
   Snowflake's default); an explicit alias gets normalized to lowercase by
   the compiler *unless it's double-quoted*. Left alone, this produces a
   single table with **mixed case** — uppercase passthroughs, lowercase
   computed columns — which breaks every downstream native-Snowflake read of
   a computed column (unquoted references auto-uppercase and no longer
   match: `invalid identifier 'USAGE_START_DATE'`). Fix: every
   computed/aliased column in `daily_overview.sql` uses an explicit
   double-quoted **UPPERCASE** alias, matching the passthrough columns.

## Quick start

### Prerequisites

- macOS or Linux
- Rust + Cargo, to build `dbt` from the `fs` repo (see [Install dbt](#install-dbt))
- [`uv`](https://docs.astral.sh/uv/) — only for the optional Snowflake helper
  scripts (`scripts/*.sh`)
- Snowflake key-pair credentials + a Lake Compute self-service token — see
  [Credentials](#credentials)

### Steps

1. **Build `dbt`** — see [Install dbt](#install-dbt).
2. **Write `.env`**:
   ```bash
   scripts/setup.sh
   ```
3. **Supply credentials** — copy `.env.example` to `.env` and fill in the
   `SNOWFLAKE_*` / `LAKE_COMPUTE_*` vars (or use a private overlay — see
   [Credentials](#credentials)), then:
   ```bash
   set -a && source .env && set +a        # or: direnv allow
   ```
4. **Run the demo**:
   ```bash
   /path/to/fs/target/debug/dbt seed --target prod
   /path/to/fs/target/debug/dbt run --target prod --select stg_report   # Snowflake leg only, no Lake Compute token needed
   /path/to/fs/target/debug/dbt run --target prod                      # full DAG
   ```

## Install dbt

This branch needs a **local build off latest `fs` main** — the published CDN
CLI doesn't know the `lakecompute` adapter type, multi-adapter profile
targets, or `+adapter`/`+propagate` config yet:

```bash
cd /path/to/fs           # a checkout of github.com/dbt-labs/fs
git pull --ff-only origin main
cargo build --bin dbt    # produces target/debug/dbt
target/debug/dbt --version
```

The LakeCompute ADBC driver itself is still fetched automatically from the
driver CDN — no local build of that piece needed.

## Credentials

Copy `.env.example` to `.env` and fill in the `SNOWFLAKE_*` and
`LAKE_COMPUTE_*` vars, or keep credentials out of the repo entirely: `.envrc`
calls `source_dotfiles_env dbt-aws-cloud-cost` when your
`~/.config/direnv/direnvrc` defines that helper, so the vars can come from a
private overlay outside the repo:

```bash
# ~/.config/direnv/direnvrc
source_dotfiles_env() {
  source_env_if_exists "$HOME/my-private-env/projects/${1:-$(basename "$PWD")}.envrc"
}
```

Without the helper the call is a no-op; `.env` loads afterwards either way.

### Snowflake (default connection)

Key-pair auth only (password auth 403s on Iceberg writes). The user's role
needs a database + schema to build into (`SNOWFLAKE_DATABASE`/
`SNOWFLAKE_SCHEMA`, and `SNOWFLAKE_SEED_DATABASE` if you don't have `CREATE
DATABASE SEEDS`) and `USAGE` on the warehouse.

### Lake Compute

`LAKE_COMPUTE_AUTH_TOKEN` is a `dct_...` self-service PAT, minted from the
MDLS destination's UI ("Write credentials" tab), used via `method: fivetran`
+ `fivetran_credential` in `profiles.yml`. Don't set a `base_url` for this
connection — the default is Fivetran's **production** dbt-compute API, which
is the one that works (see [Verified status](#verified-status) for why the
staging host doesn't). `LAKE_COMPUTE_DATABASE`/`LAKE_COMPUTE_SCHEMA` are the
MDLS-side namespace LakeCompute writes into — required, and don't inherit
from the Snowflake connection. The connecting Snowflake user also needs a
network policy assigned (see [Verified status](#verified-status), point 2).

## Regenerating the seed

The seed is committed (`seeds/aws_cost_report.csv`), so you normally never touch
it. The ShadowTraffic generator that originally produced it has been removed
from this repo; reintroduce a generator separately if you need fresh data.

## Troubleshooting

- **`lakecompute`/multi-adapter parse errors on the published CLI** — the
  published dbt Fusion CLI doesn't support this branch's config yet; you must
  use a local `fs` build off latest main (see [Install dbt](#install-dbt)).
- **`Database 'SEEDS' does not exist or not authorized`** — set
  `SNOWFLAKE_SEED_DATABASE` to a database you actually have `CREATE SCHEMA`
  on (the default, `SEEDS`, may not exist / be granted on a shared account).
- **LakeCompute connection test fails / 401 invalid token** — if
  `LAKE_COMPUTE_BASE_URL` is set to the staging host
  (`api.dbt-compute.staging.fivetran.com`), unset it; the default is
  Fivetran's production dbt-compute API, which is the one that works (see
  [Verified status](#verified-status)).
- **MDLS write succeeds but the read-back fails (`Table ... does not
  exist`)** — the `lakecompute` connection is missing `database`/`schema`;
  they don't inherit from the `snowflake` connection and are required.
- **`Propagation failed for ... Network policy is required`** — assign any
  network policy to the connecting Snowflake user (see
  [Verified status](#verified-status), point 2); an account admin has to do
  this once per user.
- **`UnusedConfigKey`: `threads:` on the `lakecompute` connection** — thread
  count is target-wide (taken from the default connection or `--threads`);
  don't set `threads:` on a non-default adapter entry.
