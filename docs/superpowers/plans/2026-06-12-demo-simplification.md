# Demo Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dbt Labs colleague clones the repo, builds two binaries, and gets `dbt seed && dbt run` working against `ducklake` with zero external credentials; external catalogs are opt-in via commented blocks.

**Architecture:** The source CSV becomes a committed dbt seed. `catalogs.yml` becomes one static file where exactly one output-catalog block is uncommented (default `ducklake`); `profiles.yml` secrets follow the same commented-block convention so no OAuth secret is created unless its catalog is active. `setup_env.sh` only hard-requires the two local binaries; a committed `.env.example` documents every env var, with a pytest parity check so it can't drift.

**Tech Stack:** dbt Fusion (local debug binary), DuckDB (patched local build), bash, pytest (run via `uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-06-12-demo-simplification-design.md`

---

## Context for the implementer (read first)

- "dbt" always means the locally built Fusion binary at `$DBT_BIN` (on PATH after `direnv allow`). Never pip dbt.
- Run all dbt commands from the repo root: `/Users/dataders/Developer/dbt_aws_cloud_cost`.
- Run tests with `uv run pytest tests/test_demo_configuration.py -v` (never bare `pytest`/`python3` — repo rule).
- The 7.2MB CSV `local_files/aws_cost_report.csv` is **already git-tracked**. Moving it is `git mv`, not re-adding data.
- `macros/utilities/aws_cloud_cost_regex_group.sql` is currently **untracked but required** by `models/staging/stg_aws_cloud_cost__report.sql` — a fresh clone is broken today. Task 0 fixes this.
- Comment convention introduced in this plan for `catalogs.yml` and `profiles.yml`: **inactive YAML blocks are prefixed with exactly `# ` per line; prose comments use `##`.** `scripts/render_demo_workspace.py` and the tests rely on this to mechanically un-comment blocks.
- "Verify ducklake run works with no creds" means prefixing dbt with `env -u HORIZON_PAT -u HORIZON_ENDPOINT -u POLARIS_ID -u POLARIS_SECRET -u DATABRICKS_TOKEN -u DATABRICKS_HOST -u SNOWFLAKE_PRIVATE_KEY` (direnv has loaded `.env`, so unset explicitly when testing the zero-cred claim).

---

### Task 0: Baseline commit of in-flight work

The working tree has 19 modified files plus untracked items. Lock in a reviewable baseline before restructuring.

**Files:**
- Modify: `.gitignore`
- Add: `macros/utilities/aws_cloud_cost_regex_group.sql` (existing untracked file)
- Commit: all currently modified tracked files

- [ ] **Step 1: Ignore the stray `metadata/` output dir**

Append to `.gitignore` (Temporary files section):

```
metadata/
```

- [ ] **Step 2: Stage and commit the baseline**

```bash
git add -u
git add macros/utilities/aws_cloud_cost_regex_group.sql .gitignore
git commit -m "Baseline: commit in-flight demo state before simplification"
```

Note: do NOT add `catalogs copy.yml` / `catalogs.yml.bak` (they get deleted in Task 3, and are untracked so deletion is just `rm`).

- [ ] **Step 3: Sanity check — parse works at baseline**

Run: `dbt parse`
Expected: success (exit 0).

### Task 1: Seed smoke test (risk gate from spec)

Prove `dbt seed` works on the Fusion debug binary before building on it. **If this task fails irrecoverably, STOP and report** — the spec's fallback is keeping `read_csv()` with the CSV committed under `local_files/`, and the rest of the plan needs rework.

**Files:**
- Move: `local_files/aws_cost_report.csv` → `seeds/aws_cost_report.csv`
- Create: `seeds/properties.yml`
- Modify: `dbt_project.yml`

- [ ] **Step 1: Move the CSV into seeds/**

```bash
mkdir -p seeds
git mv local_files/aws_cost_report.csv seeds/aws_cost_report.csv
rmdir local_files 2>/dev/null || true
```

- [ ] **Step 2: Generate the all-varchar column_types config**

The current pipeline reads the CSV with `all_varchar = true` and casts in staging; pin every seed column to `varchar` to preserve that behavior exactly:

```bash
"$DUCKDB_CLI" -unsigned :memory: -c "
SELECT '        ' || column_name || ': varchar'
FROM (DESCRIBE SELECT * FROM read_csv('seeds/aws_cost_report.csv', header=true, all_varchar=true))
" -noheader -list > /tmp/column_types.txt
```

Create `seeds/properties.yml`:

```yaml
version: 2

seeds:
  - name: aws_cost_report
    description: '{{ doc("aws_cloud_cost_report") }}'
    config:
      schema: aws_cloud_cost
      column_types:
        # paste /tmp/column_types.txt here (every column: varchar)
```

(Column descriptions are added in Task 2; keep this minimal for the smoke test.)

- [ ] **Step 3: Add seed config to dbt_project.yml**

Add under the top level (sibling of `models:`):

```yaml
seeds:
  aws_cloud_cost:
    +schema: aws_cloud_cost
```

Seeds must land in the built-in DuckDB catalog — do NOT set `+catalog_name` on seeds.

- [ ] **Step 4: Run the smoke test**

Run: `dbt seed`
Expected: success; then verify rows landed:

```bash
"$DUCKDB_CLI" -unsigned aws_cloud_cost_demo.duckdb -c "select count(*) from aws_cloud_cost.aws_cost_report"
```

Expected: 10000. (Schema name may be `aws_cloud_cost` or `aws_cloud_cost_aws_cloud_cost` depending on `generate_schema_name`; check `dbt seed` log output for the actual relation and adjust the query — what matters is the count.)

- [ ] **Step 5: Commit**

```bash
git add seeds/ dbt_project.yml
git commit -m "Move sample CSV to a dbt seed (smoke-tested on Fusion binary)"
```

### Task 2: Rewire models to the seed; delete the Snowflake source

**Files:**
- Modify: `models/staging/base/stg_aws_cloud_cost__report_base.sql`
- Modify: `models/staging/stg_aws_cloud_cost__report.sql:10`
- Modify: `macros/get_aws_cloud_cost_report_columns.sql:3`
- Modify: `seeds/properties.yml`
- Delete: `models/staging/src_aws_cloud_cost.yml`

- [ ] **Step 1: Replace the base model body entirely**

`models/staging/base/stg_aws_cloud_cost__report_base.sql` becomes:

```sql
{#-
  The demo's source is the committed seed (seeds/aws_cost_report.csv), loaded
  into the built-in DuckDB catalog by `dbt seed`. Final-model output routes
  through catalogs v2 (+catalog_name in dbt_project.yml).
-#}
select *
from {{ ref('aws_cost_report') }}
```

- [ ] **Step 2: Drop the env-var lineage label in staging**

In `models/staging/stg_aws_cloud_cost__report.sql`, replace line 10:

```sql
        '{{ env_var('AWS_CLOUD_COST_SOURCE_CATALOG', 'polaris') }}.{{ env_var('AWS_CLOUD_COST_SOURCE_TABLE', 'aws_cost_report') }}' as source_relation,
```

with:

```sql
        'seed.aws_cost_report' as source_relation,
```

- [ ] **Step 3: Drop the dead Snowflake branch in the columns macro**

In `macros/get_aws_cloud_cost_report_columns.sql`, replace line 3:

```jinja
{% set timestamp_type = "timestamp_ntz(6)" if target.type == "snowflake" else dbt.type_timestamp() %}
```

with:

```jinja
{% set timestamp_type = dbt.type_timestamp() %}
```

The macro itself STAYS — `stg_aws_cloud_cost__report.sql` uses it for the cast loop.

- [ ] **Step 4: Move column docs from the source yml onto the seed**

Transform the `columns:` list of `models/staging/src_aws_cloud_cost.yml` (every entry is `- name: X` / `description: '{{ doc("X") }}'`) into `seeds/properties.yml` under the seed's `columns:` key. The `doc()` blocks live in `models/docs.md`, which is untouched. Then:

```bash
git rm models/staging/src_aws_cloud_cost.yml
```

- [ ] **Step 5: Verify and commit**

Run: `dbt parse && dbt seed`
Expected: success, no warnings about missing source/doc references.

```bash
git add -A models seeds macros
git commit -m "Read staging from the seed; drop Snowflake source and dead branches"
```

### Task 3: catalogs.yml commented blocks; default ducklake; delete use_catalog.sh

**Files:**
- Rewrite: `catalogs.yml`
- Modify: `dbt_project.yml:9`
- Modify: `scripts/render_demo_workspace.py`
- Delete: `scripts/use_catalog.sh`, `catalogs copy.yml`, `catalogs.yml.bak`

- [ ] **Step 1: Write the new catalogs.yml**

Full content (sources: current `catalogs.yml` horizon block, `catalogs.yml.bak` for the rest; verify each block against those files before deleting them). Simplifications vs the .bak: drop the `local_files` block (seed replaced it); drop the `snowflake:` sections from `polaris`/`unity` (only used by the deleted Snowflake target); collapse `SNOWFLAKE_*`-or-`HORIZON_*` env fallback chains to the single `HORIZON_*` name; polaris `default_schema` becomes the literal `aws_cloud_cost`; s3_tables `default_schema` uses only `AWS_S3_TABLES_NAMESPACE`.

```yaml
## dbt catalogs (v2) for the multi-catalog demo.
##
## RULES:
##   1. Fusion ATTACHES EVERY catalog defined in this file at every dbt
##      invocation. Keep exactly ONE output catalog uncommented.
##   2. The active catalog's `name` must match `+catalog_name` in
##      dbt_project.yml.
##   3. Inactive blocks are prefixed with exactly `# ` per line (prose
##      comments use `##`) — scripts/render_demo_workspace.py and the tests
##      rely on this convention to un-comment blocks mechanically.
##   4. Credentialed catalogs also need their secret block uncommented in
##      profiles.yml (same `# ` convention) and the env vars from
##      .env.example. See README "Switching catalogs".

catalogs:
  - name: ducklake
    type: ducklake
    table_format: default
    config:
      duckdb:
        metadata_path: "./.tmp/ducklake.db"
        attach_as: "ducklake"
        create_if_not_exists: true

#   - name: lakekeeper
#     type: iceberg_rest
#     table_format: iceberg
#     config:
#       duckdb:
#         endpoint: "http://localhost:18181/catalog"
#         warehouse: "demo"
#         authorization_type: "NONE"
#         access_delegation_mode: "NONE"
#         attach_as: "lakekeeper"
#         default_schema: "default"

#   - name: horizon
#     type: horizon
#     table_format: iceberg
#     config:
#       snowflake:
#         external_volume: "{{ env_var('SNOWFLAKE_EXTERNAL_VOLUME', 'FUSION_ADAPTERS_CI_TEMP') }}"
#         base_location_root: "{{ env_var('SNOWFLAKE_BASE_LOCATION_ROOT', 'dbt_aws_cloud_cost/horizon') }}"
#       duckdb:
#         endpoint: "{{ env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog') }}"
#         warehouse: "{{ env_var('HORIZON_WAREHOUSE', 'DEVELOPMENT') }}"
#         secret: snowflake_oauth
#         authorization_type: "OAUTH2"
#         access_delegation_mode: "VENDED_CREDENTIALS"
#         default_region: "{{ env_var('SNOWFLAKE_DEFAULT_REGION', 'us-west-2') }}"
#         stage_create_tables: false
#         disable_multi_table_commit: true
#         skip_create_table_metadata_updates: true
#         remove_files_on_delete: false
#         attach_as: "horizon"
#         default_schema: "{{ env_var('HORIZON_SCHEMA', 'AWS_CLOUD_COST') }}"

#   - name: polaris
#     type: iceberg_rest
#     table_format: iceberg
#     config:
#       duckdb:
#         endpoint: "{{ env_var('POLARIS_URL', 'https://example.polaris.catalog') }}"
#         warehouse: "{{ env_var('POLARIS_WAREHOUSE', 'aws_cloud_cost') }}"
#         secret: polaris_oauth
#         authorization_type: "OAUTH2"
#         access_delegation_mode: "VENDED_CREDENTIALS"
#         default_region: "{{ env_var('POLARIS_DEFAULT_REGION', 'us-east-1') }}"
#         attach_as: "polaris"
#         default_schema: "aws_cloud_cost"

#   - name: unity
#     type: iceberg_rest
#     table_format: iceberg
#     config:
#       duckdb:
#         endpoint: "{{ env_var('DATABRICKS_HOST', 'https://example.cloud.databricks.com') }}/api/2.1/unity-catalog/iceberg-rest"
#         warehouse: "{{ env_var('DATABRICKS_CATALOG', 'main') }}"
#         secret: databricks_token
#         authorization_type: "OAUTH2"
#         access_delegation_mode: "VENDED_CREDENTIALS"
#         default_region: "{{ env_var('DATABRICKS_DEFAULT_REGION', 'us-west-2') }}"
#         attach_as: "unity"
#         default_schema: "{{ env_var('DATABRICKS_SCHEMA', 'aws_cloud_cost') }}"

#   - name: s3_tables
#     type: s3_tables
#     table_format: iceberg
#     config:
#       duckdb:
#         warehouse: "{{ env_var('AWS_S3_TABLES_WAREHOUSE', '') }}"
#         secret: aws_s3_tables
#         attach_as: "s3_tables"
#         default_schema: "{{ env_var('AWS_S3_TABLES_NAMESPACE', 'cloud_cost') }}"
```

Note the `horizon` block keeps `type: horizon` (NOT `iceberg_rest`) — required by this Fusion build's catalogs-v2 schema for the `snowflake.external_volume` keys.

- [ ] **Step 2: Default dbt_project.yml to ducklake**

Replace `+catalog_name: horizon` with:

```yaml
    ## Must match the single uncommented catalog in catalogs.yml.
    ## Valid names: ducklake | lakekeeper | horizon | polaris | unity | s3_tables
    +catalog_name: ducklake
```

- [ ] **Step 3: Teach render_demo_workspace.py to read commented blocks**

In `scripts/render_demo_workspace.py`, replace the first line of `catalog_blocks()`:

```python
    text = (ROOT / "catalogs.yml").read_text()
```

with:

```python
    raw = (ROOT / "catalogs.yml").read_text()
    # Inactive blocks are commented with exactly '# ' per line ('##' = prose).
    text = "\n".join(
        line[2:] if line.startswith("# ") and not line.startswith("## ")
        else ("" if line == "#" else line)
        for line in raw.splitlines()
        if not line.startswith("## ") and line != "##"
    )
```

Also in `PROJECT_DIRS`, replace `"local_files"` with `"seeds"`. And in `normalize_catalogs()`, the `local_files` references must die with the catalog block: change the alias map entry `"local": "local_files"` to `"local": "ducklake"` and the fallback `return result or ["local_files"]` to `return result or ["ducklake"]` (otherwise a no-arg run of the script exits with `unknown catalog(s): local_files`).

- [ ] **Step 4: Delete the switcher and backups**

```bash
git rm scripts/use_catalog.sh
rm 'catalogs copy.yml' catalogs.yml.bak
```

- [ ] **Step 5: Verify the local path end-to-end and commit**

Run: `dbt parse && dbt run`
Expected: all models build into ducklake. Spot check:

```bash
"$DUCKDB_CLI" -unsigned :memory: -c "ATTACH 'ducklake:./.tmp/ducklake.db' as dl; select count(*) from dl.aws_cloud_cost.aws_cloud_cost__daily_overview;"
```

Expected: a non-zero row count.

```bash
git add -A catalogs.yml dbt_project.yml scripts
git commit -m "Single commented-blocks catalogs.yml, default ducklake; drop use_catalog.sh"
```

### Task 4: profiles.yml — no eager OAuth secrets

**Files:**
- Rewrite: `profiles.yml`

- [ ] **Step 1: Write the new profiles.yml**

Full content. `minio_secret` stays active (static values, no network call at CREATE, needed by lakekeeper — keeps lakekeeper a two-edit switch). OAuth/iceberg secrets move to commented blocks (same `# ` convention); the Horizon secret keeps only the **PAT** path (the verified-working auth mechanism) with `HORIZON_*` names:

```yaml
## Profile for the multi-catalog demo (DuckDB only).
##
## Secret blocks follow the same convention as catalogs.yml: the secrets for
## inactive catalogs are commented with `# ` and must be uncommented together
## with their catalog block. minio_secret stays active — it is static, makes
## no network call at creation, and is required by the lakekeeper target.
aws_cloud_cost:
  target: catalog_demo
  outputs:
    catalog_demo:
      type: duckdb
      path: "aws_cloud_cost_demo.duckdb"
      schema: aws_cloud_cost
      threads: 4
      settings:
        allow_unsigned_extensions: true
        extension_directory: "{{ env_var('DUCKDB_EXTENSION_REPOSITORY', './.tmp/duckdb-extension-repository') }}"
        custom_extension_repository: "{{ env_var('DUCKDB_EXTENSION_REPOSITORY', './.tmp/duckdb-extension-repository') }}"
      secrets:
        - type: s3
          name: minio_secret
          key_id: minio-root-user
          secret: minio-root-password
          endpoint: "localhost:19000"
          url_style: path
          use_ssl: false
#         ## horizon (Snowflake) — uncomment with the horizon catalog block
#         - type: iceberg
#           name: snowflake_oauth
#           client_id: ""
#           client_secret: "{{ env_var('HORIZON_PAT', '') }}"
#           oauth2_server_uri: "{{ env_var('HORIZON_OAUTH2_SERVER_URI', '') or ((env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog')) ~ '/v1/oauth/tokens') }}"
#           oauth2_scope: "{{ env_var('HORIZON_OAUTH2_SCOPE', 'session:role:ACCOUNTADMIN') }}"
#           oauth2_grant_type: "client_credentials"
#         ## polaris — uncomment with the polaris catalog block
#         - type: iceberg
#           name: polaris_oauth
#           client_id: "{{ env_var('POLARIS_ID', '') }}"
#           client_secret: "{{ env_var('POLARIS_SECRET', '') }}"
#           oauth2_server_uri: "{{ env_var('POLARIS_OAUTH_TOKEN_URI', '') or ((env_var('POLARIS_URL', 'https://example.polaris.catalog')) ~ '/v1/oauth/tokens') }}"
#           oauth2_scope: "{{ env_var('POLARIS_OAUTH_SCOPE', 'PRINCIPAL_ROLE:ALL') }}"
#           oauth2_grant_type: "client_credentials"
#         ## unity (read-only upstream) — uncomment with the unity catalog block
#         - type: iceberg
#           name: databricks_token
#           token: "{{ env_var('DATABRICKS_TOKEN', '') }}"
#         ## s3_tables — uncomment with the s3_tables catalog block
#         - type: s3
#           name: aws_s3_tables
#           provider: credential_chain
#           region: "{{ env_var('AWS_REGION', 'us-west-2') }}"
```

- [ ] **Step 2: Verify zero-cred run and commit**

Run (explicitly unsetting every credential var that direnv loaded):

```bash
env -u HORIZON_PAT -u HORIZON_ENDPOINT -u HORIZON_CLIENT_ID -u HORIZON_CLIENT_SECRET \
    -u POLARIS_ID -u POLARIS_SECRET -u POLARIS_URL \
    -u DATABRICKS_TOKEN -u DATABRICKS_HOST -u SNOWFLAKE_PRIVATE_KEY \
    dbt run
```

Expected: success against ducklake — proving no eager OAuth secret creation.

```bash
git add profiles.yml
git commit -m "profiles.yml: comment out per-catalog OAuth secrets; PAT-only horizon auth"
```

### Task 5: .env.example with pytest parity guard (TDD)

**Files:**
- Create: `.env.example`
- Modify: `.gitignore`
- Modify: `tests/test_demo_configuration.py` (add one test now; full rewrite is Task 7)

- [ ] **Step 1: Write the failing parity test**

Add this method **inside the `DemoConfigurationTest` class** in `tests/test_demo_configuration.py` (before the `if __name__ == "__main__":` guard at the bottom):

```python
    def test_env_example_covers_all_referenced_env_vars(self):
        import re

        def referenced(text):
            return set(re.findall(r"env_var\('([A-Z][A-Z0-9_]*)'", text))

        refs = referenced(self.read("profiles.yml")) | referenced(self.read("catalogs.yml"))
        documented = set(
            re.findall(r"^#? ?([A-Z][A-Z0-9_]*)=", self.read(".env.example"), re.M)
        )
        self.assertEqual(refs - documented, set(), "referenced but not in .env.example")
        # .env.example may additionally document setup-only vars (DBT_BIN etc.)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_demo_configuration.py::DemoConfigurationTest::test_env_example_covers_all_referenced_env_vars -v`
Expected: FAIL (`.env.example` missing).

- [ ] **Step 3: Write .env.example**

```bash
## Environment variables for the multi-catalog demo.
## scripts/setup_env.sh generates .env in this same layout, filling in what it
## can find. Lines commented out are OPTIONAL — only needed for the catalog
## named in their section header. The ducklake target needs NO credentials.

## ---- always required (the two locally built binaries) ----
DBT_BIN=/path/to/fs/target/debug/dbt
DUCKDB_BUILD_DIR=/path/to/duckdb-iceberg
## derived from DUCKDB_BUILD_DIR by setup_env.sh (override only if needed):
# DUCKDB_CLI=
# DUCKDB_DRIVER_LIB=
# DUCKDB_EXTENSION_REPOSITORY=

## ---- optional: regenerate the seed CSV (scripts/generate_seed_csv.sh) ----
# SHADOWTRAFFIC_LICENSE_ENV=/path/to/shadowtraffic/license.env

## ---- catalog: lakekeeper (local docker; no credentials) ----
## no env vars — `docker compose up -d` is enough

## ---- catalog: horizon (Snowflake) ----
# HORIZON_ENDPOINT=https://<account>.snowflakecomputing.com/polaris/api/catalog
# HORIZON_PAT=
# HORIZON_WAREHOUSE=
# HORIZON_SCHEMA=AWS_CLOUD_COST
# HORIZON_OAUTH2_SERVER_URI=
# HORIZON_OAUTH2_SCOPE=session:role:ACCOUNTADMIN
# SNOWFLAKE_EXTERNAL_VOLUME=
# SNOWFLAKE_BASE_LOCATION_ROOT=dbt_aws_cloud_cost/horizon
# SNOWFLAKE_DEFAULT_REGION=us-west-2
## for the optional horizon helper scripts (doctor.sh, create_horizon_pat.sh):
# SNOWFLAKE_ACCOUNT=
# SNOWFLAKE_USER=
# SNOWFLAKE_ROLE=
# SNOWFLAKE_PRIVATE_KEY=
# SNOWFLAKE_SQL_API_HOST=

## ---- catalog: polaris ----
# POLARIS_URL=
# POLARIS_ID=
# POLARIS_SECRET=
# POLARIS_WAREHOUSE=aws_cloud_cost
# POLARIS_OAUTH_TOKEN_URI=
# POLARIS_OAUTH_SCOPE=PRINCIPAL_ROLE:ALL
# POLARIS_DEFAULT_REGION=us-east-1

## ---- catalog: unity (Databricks — READ-ONLY upstream, writes 403) ----
# DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
# DATABRICKS_TOKEN=
# DATABRICKS_CATALOG=
# DATABRICKS_SCHEMA=aws_cloud_cost
# DATABRICKS_DEFAULT_REGION=us-west-2

## ---- catalog: s3_tables (experimental) ----
# AWS_S3_TABLES_WAREHOUSE=arn:aws:s3tables:<region>:<account>:bucket/<bucket>
# AWS_S3_TABLES_NAMESPACE=cloud_cost
# AWS_REGION=us-west-2

## ---- advanced overrides ----
# AWS_CLOUD_COST_TARGET_SCHEMA=   ## override output schema (generate_schema_name)
# CATALOG_SCHEMA=                 ## same, lower precedence
```

- [ ] **Step 4: Un-ignore it**

In `.gitignore`, directly after the `.env.*` line, add:

```
!.env.example
```

- [ ] **Step 5: Verify pass and commit**

Run: `uv run pytest tests/test_demo_configuration.py::DemoConfigurationTest::test_env_example_covers_all_referenced_env_vars -v`
Expected: PASS. (If a var fails parity, fix `.env.example`, not the test.)

```bash
git add .env.example .gitignore tests/test_demo_configuration.py
git commit -m "Add .env.example with env-var parity test"
```

### Task 6: setup_env.sh required/optional split; generate_seed_csv.sh

**Files:**
- Modify: `scripts/setup_env.sh`
- Rename: `scripts/generate_local_csv.sh` → `scripts/generate_seed_csv.sh`

- [ ] **Step 1: Make external credentials optional in setup_env.sh**

Keep the existing structure/helpers; make these changes:

1. **Delete the hard requirements** on the credentials JSON and license (lines 127–130). Replace with warnings:

```bash
[ -f "$CREDENTIALS_JSON" ] || printf 'note: no Snowflake credentials json at %s — horizon helper scripts disabled (fine for local targets)\n' "$CREDENTIALS_JSON" >&2
[ -f "$LICENSE_ENV" ] || printf 'note: no ShadowTraffic license at %s — seed regeneration disabled (the committed seed still works)\n' "$LICENSE_ENV" >&2
```

2. **Guard the Snowflake jq block** (lines 247–252): wrap `SNOWFLAKE_ACCOUNT=...` through `SNOWFLAKE_PRIVATE_KEY=...` and the derived `HORIZON_*` defaults (lines 273–286) in `if [ -f "$CREDENTIALS_JSON" ]; then ... fi`, switching the `jq -er` calls to the existing `jq_optional` helper so absence never aborts.
3. **Make all credential lines optional in the .env writer**: in the heredoc-style block (lines 290–347), replace every unconditional `printf 'SNOWFLAKE_...'`/`printf 'HORIZON_...'` with `write_optional_env NAME` calls, and group the output with the same section headers as `.env.example` (`## ---- always required ----`, `## ---- catalog: horizon ----`, etc.). The always-required block (`DBT_BIN`, `DBT_PROFILES_DIR`, `DUCKDB_*`, `ADBC_REPOSITORY`, `DISABLE_*`) stays unconditional. Drop `SNOWFLAKE_DATABASE`, `SNOWFLAKE_TABLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_SCHEMA`, `HORIZON_CLIENT_ID`, `HORIZON_CLIENT_SECRET`, `POLARIS_ENV`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_CLOUD_COST_SOURCE_*`, `POLARIS_NAMESPACE`, `POLARIS_TABLE`, `POLARIS_ACCESS_DELEGATION_MODE` from the writer and the `load_existing_env_var` list (no longer referenced by anything; `snowflake_sql_api.py` reads what remains).
4. **Update the closing message** (line 360) to:

```bash
printf '\nnext: run `direnv allow`, then `dbt seed && dbt run`.\n'
```

5. Keep `bootstrap_demo_schemas`, the ADBC link, and extension checks exactly as they are.

- [ ] **Step 2: Verify setup works without credentials**

```bash
DOTFILES_ENV=/nonexistent scripts/setup_env.sh
```

Expected: exits 0, prints the two `note:` lines, writes `.env` containing the always-required block. Then run `scripts/setup_env.sh` normally (with your real `DOTFILES_ENV`) to restore your full `.env`.

- [ ] **Step 3: Rename and rewire the CSV generator**

```bash
git mv scripts/generate_local_csv.sh scripts/generate_seed_csv.sh
```

In the renamed script: change the output path `local_files/aws_cost_report.csv` → `seeds/aws_cost_report.csv` (lines 42–48: `mkdir -p "$ROOT/local_files"` → `mkdir -p "$ROOT/seeds"`, COPY target, and the final echo), and update the header comment to say it regenerates the committed seed.

- [ ] **Step 4: Commit**

```bash
git add scripts
git commit -m "setup_env.sh: external credentials optional; rename generate_seed_csv.sh"
```

### Task 7: Rewrite the test suite around the new invariants

**Files:**
- Rewrite: `tests/test_demo_configuration.py`

- [ ] **Step 1: Replace the file**

Keep (verbatim from the current file): `test_packages_use_dbt_utils_without_fivetran_utils`, `test_docker_compose_contains_lakekeeper_stack`, `test_scripts_use_uv_python_without_python3`, the three `snowflake_helper` tests, `test_shadowtraffic_writes_iceberg_to_polaris`, `test_union_macros_are_removed`, `test_repo_does_not_ship_dbt_invocation_scripts`, `test_schema_name_generation_keeps_catalog_schema_env_override`, the four `render_demo_workspace` tests for `lakekeeper`/`polaris`/`s3_tables`/`horizon` (update: replace every `--include-catalog local_files` argument and `local_files` assertion — the builtin-workspace test now passes `--include-catalog ducklake` and asserts `name: ducklake` present / others absent; drop `assertTrue((workspace / "local_files").exists())`), and `test_env_example_covers_all_referenced_env_vars` from Task 5.

Delete: `test_plan_artifacts_are_present` (superseded by the new `test_required_artifacts_exist`), `test_local_file_source_is_generated_csv_with_report_columns`, `test_project_uses_catalogs_v2_with_env_selected_output`, `test_setup_env_wires_local_fusion_and_duckdb_builds`, `test_profiles_and_catalogs_match_duckdb_multi_catalog_demo`, `test_snowflake_source_uses_snowflake_env_defaults`, `test_staging_uses_single_source_without_fivetran_union_macros`, `test_staging_report_avoids_window_functions` (keep the window-function assertion — fold into the new staging test below), `test_readme_documents_source_generation_and_catalog_verification`.

Add these new tests:

```python
    def test_required_artifacts_exist(self):
        for relative_path in [
            "profiles.yml",
            "catalogs.yml",
            "docker-compose.yml",
            ".envrc",
            ".env.example",
            "seeds/aws_cost_report.csv",
            "seeds/properties.yml",
        ]:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())
        for gone in [
            "scripts/use_catalog.sh",
            "catalogs.yml.bak",
            "catalogs copy.yml",
            "local_files",
            "models/staging/src_aws_cloud_cost.yml",
        ]:
            with self.subTest(gone=gone):
                self.assertFalse((ROOT / gone).exists())

    def test_seed_csv_has_report_columns(self):
        header = self.read("seeds/aws_cost_report.csv").splitlines()[0]
        for column in ["identity_line_item_id", "line_item_unblended_cost", "_modified", "_file"]:
            self.assertIn(column, header)

    def active_catalog_names(self):
        import re
        return re.findall(r"^  - name: (\w+)$", self.read("catalogs.yml"), re.M)

    def test_exactly_one_active_catalog_matching_project_config(self):
        import re
        active = self.active_catalog_names()
        self.assertEqual(len(active), 1, f"expected exactly one active catalog, got {active}")
        project = self.read("dbt_project.yml")
        match = re.search(r"\+catalog_name: (\w+)", project)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), active[0])

    def test_commented_catalog_blocks_cover_all_targets(self):
        catalogs = self.read("catalogs.yml")
        for name in ["ducklake", "lakekeeper", "horizon", "polaris", "unity", "s3_tables"]:
            with self.subTest(name=name):
                self.assertTrue(
                    f"  - name: {name}" in catalogs or f"#   - name: {name}" in catalogs
                )

    def test_profiles_creates_no_eager_oauth_secrets(self):
        import re
        profile = self.read("profiles.yml")
        active_secret_names = []
        for line in profile.splitlines():
            if line.startswith("#"):
                continue
            match = re.search(r"name: (\w+)$", line)
            if match:
                active_secret_names.append(match.group(1))
        self.assertEqual(active_secret_names, ["minio_secret"])

    def test_staging_reads_from_seed_without_env_vars(self):
        base = self.read("models/staging/base/stg_aws_cloud_cost__report_base.sql")
        staging = self.read("models/staging/stg_aws_cloud_cost__report.sql")
        self.assertIn("ref('aws_cost_report')", base)
        self.assertNotIn("read_csv", base)
        self.assertNotIn("source(", base)
        self.assertNotIn("env_var", staging)
        self.assertNotIn(" over (", staging.lower())

    def test_readme_documents_the_simplified_flow(self):
        readme = self.read("README.md")
        for snippet in [
            "dbt seed",
            "+catalog_name",
            ".env.example",
            "scripts/generate_seed_csv.sh",
            "docker compose up -d",
            "aws_cloud_cost__daily_overview",
            "read-only",
        ]:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, readme)
        self.assertNotIn("use_catalog.sh", readme)
        self.assertNotIn("local_files", readme)
        self.assertNotIn("AWS_CLOUD_COST_TARGET_CATALOG", readme)
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_demo_configuration.py -v`
Expected: everything passes EXCEPT `test_readme_documents_the_simplified_flow` (README rewrite is Task 8 — this is the deliberate failing test driving it).

- [ ] **Step 3: Commit**

```bash
git add tests/test_demo_configuration.py
git commit -m "Rewrite demo configuration tests around simplified invariants"
```

### Task 8: README rewrite

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: Write the new README**

Structure and required content (keep the existing "Build the two local binaries" section nearly verbatim — it is accurate — and reuse current prose where it survives):

1. **Title + one-paragraph pitch** (same as today: same models, different output catalogs via catalogs v2; note "dbt" = local Fusion binary).
2. **How it works** — updated diagram:

```
seeds/aws_cost_report.csv      (committed sample data; `dbt seed` loads it)
        |
        v
stg_aws_cloud_cost__report_base / stg_aws_cloud_cost__report   (built-in DuckDB catalog)
        |
        v
aws_cloud_cost__daily_*        (output catalog = +catalog_name in dbt_project.yml)
```

3. **Catalog matrix** (honest statuses):

| Catalog | Type | Status | Needs |
| --- | --- | --- | --- |
| `ducklake` | DuckLake local | ✅ default | nothing |
| `lakekeeper` | Iceberg REST (local) | ✅ | `docker compose up -d` |
| `horizon` | Snowflake Horizon | ✅ | Snowflake creds (`HORIZON_*`) |
| `polaris` | Iceberg REST | ✅ | Polaris creds (`POLARIS_*`) |
| `unity` | Databricks Unity | 🚫 read-only upstream | n/a (reads only) |
| `s3_tables` | Amazon S3 Tables | 🧪 experimental | AWS creds |

Under the table: the Unity explanation (UC's Iceberg REST implements no `createTable`; external-engine writes get 403 — link `https://github.com/unitycatalog/unitycatalog/issues/3`), and the note that `unity`/`horizon` writes need the write-compat attach options from duckdb/duckdb-iceberg#1017 (shipping in DuckDB 1.5.4; the local build includes them).

4. **Prerequisites** — macOS/Linux, `jq`, `curl`, `zsh`, `docker` (lakekeeper only), `uv`, Rust + C++ toolchains. NO credentials for the default path.
5. **Build the two local binaries** — keep current section.
6. **Quickstart** (zero credentials):

```bash
export DBT_BIN=/path/to/your/fs/target/debug/dbt
export DUCKDB_BUILD_DIR=/path/to/your/duckdb-iceberg
scripts/setup_env.sh
direnv allow
dbt seed
dbt run
```

Then: inspect `aws_cloud_cost__daily_overview` in the active catalog to confirm aggregations.

7. **Switching catalogs** — the explicit edits, e.g. for lakekeeper: (1) in `catalogs.yml`, comment the `ducklake` block and uncomment `lakekeeper` (strip the leading `# `); (2) in `dbt_project.yml`, set `+catalog_name: lakekeeper`; (3) for credentialed catalogs only: uncomment the matching secret block in `profiles.yml` and supply the env vars from `.env.example`. State the invariant: exactly one active catalog; name must match; `uv run pytest tests/test_demo_configuration.py` checks it.
8. **Per-catalog setup** — subsection per external catalog listing its `.env.example` block, helper scripts (`doctor.sh`, `create_horizon_pat.sh`, `configure_horizon_schema.sh` for horizon), and for lakekeeper the docker-compose note + teardown (`docker compose down -v`).
9. **Regenerating the seed (optional)** — `scripts/generate_seed_csv.sh [ROWS]`, needs docker + uv + ShadowTraffic license; then `dbt seed` again.
10. **Optional diagnostics** — one-liners for `feature_compat_probe.py`, `direct_duckdb_catalog_probe.sh`, `render_demo_workspace.py`, `start.sh`/`stop.sh` (Polaris streaming source).
11. **Troubleshooting** — keep current entries minus `use_catalog.sh`/env-var-switching; add: "catalog name mismatch" (pytest invariant), "OAuth errors on a local run" (you uncommented a secret block without supplying creds; re-comment it), "two catalogs active" symptom.

- [ ] **Step 2: Verify the failing test now passes**

Run: `uv run pytest tests/test_demo_configuration.py -v`
Expected: ALL pass.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Rewrite README around the simplified seed + commented-blocks flow"
```

### Task 9: Full verification sweep (spec's verification section)

- [ ] **Step 1: Zero-cred local path from clean state**

```bash
rm -f aws_cloud_cost_demo.duckdb .tmp/ducklake.db*
env -u HORIZON_PAT -u HORIZON_ENDPOINT -u POLARIS_ID -u POLARIS_SECRET \
    -u DATABRICKS_TOKEN -u DATABRICKS_HOST -u SNOWFLAKE_PRIVATE_KEY \
    sh -c 'dbt parse && dbt seed && dbt run'
```

Expected: green.

- [ ] **Step 2: Test suite**

Run: `uv run pytest tests/test_demo_configuration.py -v`
Expected: all pass.

- [ ] **Step 3: Lakekeeper switch smoke test (the README's own instructions)**

```bash
docker compose up -d
```

Then perform exactly the two edits the README describes (comment ducklake / uncomment lakekeeper in `catalogs.yml`; `+catalog_name: lakekeeper`), run `dbt run`, expect green. Revert both edits afterward (`git checkout catalogs.yml dbt_project.yml`) and `docker compose down`.

- [ ] **Step 4: README walkthrough audit**

Read the README top to bottom; every command must be runnable verbatim from a fresh clone given the two built binaries. Fix anything that isn't.

- [ ] **Step 5: Final commit if anything changed**

```bash
git add -A && git commit -m "Verification fixes from full demo walkthrough"
```
