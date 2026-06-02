from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / ".tmp" / "feature-compat"


@dataclass(frozen=True)
class Target:
    name: str
    catalog_name: str | None
    catalog_block: str
    note: str


def local_files_block() -> str:
    return """  - name: local_files
    type: local_filesystem
    table_format: default
    config:
      duckdb:
        root_path: "./local_files"
        file_format: csv
"""


def catalog_targets() -> dict[str, Target]:
    return {
        "builtin": Target("builtin", None, "", "Default DuckDB catalog, no external catalog block."),
        "ducklake": Target(
            "ducklake",
            "ducklake",
            """  - name: ducklake
    type: ducklake
    table_format: default
    config:
      duckdb:
        metadata_path: "./.tmp/ducklake.db"
        attach_as: "ducklake"
        create_if_not_exists: true
""",
            "DuckLake local metadata catalog.",
        ),
        "lakekeeper": Target(
            "lakekeeper",
            "lakekeeper",
            """  - name: lakekeeper
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: "http://localhost:18181/catalog"
        warehouse: "demo"
        authorization_type: "NONE"
        access_delegation_mode: "NONE"
        attach_as: "lakekeeper"
        default_schema: "default"
""",
            "Local Lakekeeper Iceberg REST catalog.",
        ),
        "horizon_checkedin": Target(
            "horizon_checkedin",
            "horizon",
            """  - name: horizon
    type: horizon
    table_format: iceberg
    config:
      duckdb:
        endpoint: "{{ env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog') }}"
        warehouse: "{{ env_var('HORIZON_WAREHOUSE', '') or env_var('SNOWFLAKE_CATALOG_WAREHOUSE', '') or env_var('SNOWFLAKE_DATABASE', 'DEVELOPMENT') }}"
        secret: snowflake_oauth
        default_region: "{{ env_var('SNOWFLAKE_DEFAULT_REGION', 'us-west-2') }}"
        attach_as: "horizon"
        default_schema: "{{ env_var('HORIZON_SCHEMA', '') or env_var('SNOWFLAKE_SCHEMA', 'AWS_CLOUD_COST') }}"
""",
            "Horizon block exactly matching the current checked-in catalogs.yml shape.",
        ),
        "horizon_scripted": Target(
            "horizon_scripted",
            "horizon",
            """  - name: horizon
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: "{{ env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog') }}"
        warehouse: "{{ env_var('HORIZON_WAREHOUSE', '') or env_var('SNOWFLAKE_CATALOG_WAREHOUSE', '') or env_var('SNOWFLAKE_DATABASE', 'DEVELOPMENT') }}"
        secret: snowflake_oauth
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('SNOWFLAKE_DEFAULT_REGION', 'us-west-2') }}"
        stage_create_tables: false
        disable_multi_table_commit: true
        skip_create_table_metadata_updates: true
        remove_files_on_delete: false
        attach_as: "horizon"
        default_schema: "{{ env_var('HORIZON_SCHEMA', '') or env_var('SNOWFLAKE_SCHEMA', 'AWS_CLOUD_COST') }}"
""",
            "Horizon block emitted by scripts/use_catalog.sh horizon.",
        ),
        "unity": Target(
            "unity",
            "unity",
            """  - name: unity
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: "{{ env_var('DATABRICKS_HOST', 'https://example.cloud.databricks.com') }}/api/2.1/unity-catalog/iceberg-rest"
        warehouse: "{{ env_var('DATABRICKS_CATALOG', 'dbt_dataders') }}"
        secret: databricks_token
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('DATABRICKS_DEFAULT_REGION', 'us-west-2') }}"
        attach_as: "unity"
        default_schema: "{{ env_var('DATABRICKS_SCHEMA', 'aws_cloud_cost') }}"
""",
            "Databricks Unity Catalog Iceberg REST catalog.",
        ),
        "polaris": Target(
            "polaris",
            "polaris",
            """  - name: polaris
    type: iceberg_rest
    table_format: iceberg
    config:
      duckdb:
        endpoint: "{{ env_var('POLARIS_URL', 'https://example.polaris.catalog') }}"
        warehouse: "{{ env_var('POLARIS_WAREHOUSE', 'aws_cloud_cost') }}"
        secret: polaris_oauth
        authorization_type: "OAUTH2"
        access_delegation_mode: "VENDED_CREDENTIALS"
        default_region: "{{ env_var('POLARIS_DEFAULT_REGION', 'us-east-1') }}"
        attach_as: "polaris"
        default_schema: "{{ env_var('AWS_CLOUD_COST_SOURCE_SCHEMA', env_var('POLARIS_NAMESPACE', 'aws_cloud_cost')) }}"
""",
            "Fivetran Polaris Iceberg REST catalog.",
        ),
    }


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def secret_values(env: dict[str, str]) -> list[str]:
    needles = []
    for key, value in env.items():
        upper = key.upper()
        if not value or len(value) < 8:
            continue
        if any(token in upper for token in ("TOKEN", "SECRET", "PAT", "PASSWORD", "PRIVATE_KEY", "ACCESS_KEY")):
            needles.append(value)
    return sorted(set(needles), key=len, reverse=True)


def sanitize(text: str, env: dict[str, str]) -> str:
    redacted = text
    for value in secret_values(env):
        redacted = redacted.replace(value, "<redacted>")
    redacted = re.sub(r"dapi[a-zA-Z0-9_-]{20,}", "dapi<redacted>", redacted)
    redacted = re.sub(r"gh[opurs]_[A-Za-z0-9_]{20,}", "gh<redacted>", redacted)
    redacted = re.sub(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}", "jwt<redacted>", redacted)
    return redacted


def profile_text() -> str:
    return """compat_probe:
  target: catalog_demo
  outputs:
    catalog_demo:
      type: duckdb
      path: ".tmp/compat.duckdb"
      schema: aws_cloud_cost
      threads: 1
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
        - type: iceberg
          name: polaris_oauth
          client_id: "{{ env_var('POLARIS_ID', '') }}"
          client_secret: "{{ env_var('POLARIS_SECRET', '') }}"
          oauth2_server_uri: "{{ env_var('POLARIS_OAUTH_TOKEN_URI', '') or ((env_var('POLARIS_URL', 'https://example.polaris.catalog')) ~ '/v1/oauth/tokens') }}"
          oauth2_scope: "{{ env_var('POLARIS_OAUTH_SCOPE', 'PRINCIPAL_ROLE:ALL') }}"
          oauth2_grant_type: "client_credentials"
        - type: iceberg
          name: snowflake_oauth
          client_id: ""
          client_secret: "{{ env_var('HORIZON_PAT', '') }}"
          oauth2_server_uri: "{{ env_var('SNOWFLAKE_OAUTH2_SERVER_URI', '') or env_var('HORIZON_OAUTH2_SERVER_URI', '') or ((env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog')) ~ '/v1/oauth/tokens') }}"
          oauth2_scope: "{{ env_var('SNOWFLAKE_OAUTH2_SCOPE', '') or env_var('HORIZON_OAUTH2_SCOPE', 'session:role:ACCOUNTADMIN') }}"
          oauth2_grant_type: "client_credentials"
        - type: iceberg
          name: databricks_token
          token: "{{ env_var('DATABRICKS_TOKEN', '') }}"
"""


def project_text(target: Target, prefix: str) -> str:
    catalog_line = f"    +catalog_name: {target.catalog_name}\n" if target.catalog_name else ""
    seed_catalog_line = f"  +catalog_name: {target.catalog_name}\n" if target.catalog_name else ""
    snapshot_catalog_line = f"  +catalog_name: {target.catalog_name}\n" if target.catalog_name else ""
    return f"""config-version: 2
name: 'compat_probe'
version: '1.0.0'
profile: compat_probe

model-paths: ["models"]
seed-paths: ["seeds"]
snapshot-paths: ["snapshots"]
test-paths: ["tests"]
analysis-paths: ["analyses"]
macro-paths: ["macros"]

models:
  compat_probe:
{catalog_line}    +materialized: table

seeds:
{seed_catalog_line}
snapshots:
  compat_probe:
    +target_schema: aws_cloud_cost
{snapshot_catalog_line}
flags:
  use_catalogs_v2: true
  require_generic_test_arguments_property: true
  send_anonymous_usage_stats: false

vars:
  compat_prefix: "{prefix}"
"""


def catalogs_text(target: Target) -> str:
    blocks = [local_files_block()]
    if target.catalog_block:
        blocks.append(target.catalog_block)
    return "catalogs:\n" + "\n".join(blocks)


def model_names(prefix: str, feature: str) -> dict[str, str]:
    stem = f"{prefix}_{feature}".lower()
    return {
        "base": f"{stem}_base",
        "table": f"{stem}_table",
        "view": f"{stem}_view",
        "ephemeral": f"{stem}_ephemeral",
        "uses_ephemeral": f"{stem}_uses_ephemeral",
        "inc_append": f"{stem}_inc_append",
        "inc_merge": f"{stem}_inc_merge",
        "inc_delete_insert": f"{stem}_inc_delete_insert",
        "hook": f"{stem}_hook",
        "contract": f"{stem}_contract",
        "seed": f"{stem}_seed_input",
        "snapshot": f"{stem}_snapshot",
        "analysis": f"{stem}_analysis",
        "singular_test": f"{stem}_assert_no_negative_ids",
        "source_test": f"{stem}_source_seed_input",
        "store_failures_test": f"{stem}_store_failures_expected_failure",
        "retry_test": f"{stem}_retry_expected_failure",
        "unit_test": f"{stem}_unit_table",
    }


def safe_name(value: str, limit: int = 18) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower())[:limit].strip("_")


def source_database(target: Target) -> str:
    if target.catalog_name:
        return f'    database: "{target.catalog_name}"\n'
    return ""


def write_fixture_project(project_dir: Path, target: Target, feature: str, prefix: str) -> dict[str, str]:
    names = model_names(prefix, feature)
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)
    (project_dir / ".tmp").mkdir()
    write(project_dir / "dbt_project.yml", project_text(target, prefix))
    write(project_dir / "profiles.yml", profile_text())
    write(project_dir / "catalogs.yml", catalogs_text(target))
    write(project_dir / "seeds" / f"{names['seed']}.csv", "id,name,updated_at\n1,alpha,2026-01-01 00:00:00\n2,beta,2026-01-02 00:00:00\n")
    write(
        project_dir / "macros" / "compat_probe_ping.sql",
        """{% macro compat_probe_ping() %}
    {% do run_query("select 1 as ok") %}
    {% do log("compat_probe_ping", info=True) %}
{% endmacro %}
""",
    )

    write(
        project_dir / "models" / f"{names['base']}.sql",
        """select
    1 as id,
    'alpha' as name,
    timestamp '2026-01-01 00:00:00' as updated_at
union all
select
    2 as id,
    'beta' as name,
    timestamp '2026-01-02 00:00:00' as updated_at
""",
    )
    write(project_dir / "models" / f"{names['table']}.sql", f"select * from {{{{ ref('{names['base']}') }}}}\n")
    write(project_dir / "models" / f"{names['view']}.sql", f"{{{{ config(materialized='view') }}}}\nselect * from {{{{ ref('{names['base']}') }}}}\n")
    write(project_dir / "models" / f"{names['ephemeral']}.sql", f"{{{{ config(materialized='ephemeral') }}}}\nselect id, upper(name) as name_upper from {{{{ ref('{names['base']}') }}}}\n")
    write(project_dir / "models" / f"{names['uses_ephemeral']}.sql", f"select * from {{{{ ref('{names['ephemeral']}') }}}}\n")
    write(
        project_dir / "models" / f"{names['inc_append']}.sql",
        """{{ config(materialized='incremental') }}
select 1 as id, 'initial' as status
{% if is_incremental() %}
union all
select 2 as id, 'second' as status
{% endif %}
""",
    )
    write(
        project_dir / "models" / f"{names['inc_merge']}.sql",
        """{{ config(materialized='incremental', unique_key='id', incremental_strategy='merge') }}
select 1 as id,
{% if is_incremental() %}
    'updated'
{% else %}
    'initial'
{% endif %}
as status
{% if is_incremental() %}
union all
select 2 as id, 'second' as status
{% endif %}
""",
    )
    write(
        project_dir / "models" / f"{names['inc_delete_insert']}.sql",
        """{{ config(materialized='incremental', unique_key='id', incremental_strategy='delete+insert') }}
select 1 as id,
{% if is_incremental() %}
    'updated'
{% else %}
    'initial'
{% endif %}
as status
{% if is_incremental() %}
union all
select 2 as id, 'second' as status
{% endif %}
""",
    )
    write(
        project_dir / "models" / f"{names['hook']}.sql",
        f"""{{{{ config(
    pre_hook="create table if not exists {{{{ this.database }}}}.{{{{ this.schema }}}}.{names['hook']}_audit (event varchar)",
    post_hook="insert into {{{{ this.database }}}}.{{{{ this.schema }}}}.{names['hook']}_audit values ('post')"
) }}}}
select * from {{{{ ref('{names['base']}') }}}}
""",
    )
    write(
        project_dir / "models" / f"{names['contract']}.sql",
        """{{ config(contract={'enforced': true}) }}
select cast(1 as integer) as id, cast('alpha' as varchar) as name
""",
    )
    source_freshness = ""
    if feature == "source_freshness":
        source_freshness = """        config:
          loaded_at_field: updated_at
          freshness:
            warn_after: {count: 1, period: day}
            error_after: {count: 3650, period: day}
"""

    write(
        project_dir / "models" / "schema.yml",
        f"""version: 2

models:
  - name: {names['table']}
    columns:
      - name: id
        data_tests:
          - not_null
          - unique
      - name: name
        data_tests:
          - not_null
  - name: {names['contract']}
    config:
      contract:
        enforced: true
    columns:
      - name: id
        data_type: integer
        constraints:
          - type: not_null
      - name: name
        data_type: varchar

unit_tests:
  - name: {names['unit_test']}
    model: {names['table']}
    given:
      - input: ref('{names['base']}')
        rows:
          - {{id: 1, name: alpha, updated_at: '2026-01-01 00:00:00'}}
    expect:
      rows:
        - {{id: 1, name: alpha, updated_at: '2026-01-01 00:00:00'}}

sources:
  - name: compat_source
{source_database(target)}    schema: aws_cloud_cost
    tables:
      - name: {names['seed']}
{source_freshness}
        columns:
          - name: id
            data_tests:
              - not_null
              - unique
""",
    )
    write(project_dir / "tests" / f"{names['singular_test']}.sql", f"select * from {{{{ ref('{names['table']}') }}}} where id < 0\n")
    store_catalog = f", catalog_name='{target.catalog_name}'" if target.catalog_name else ""
    write(
        project_dir / "tests" / f"{names['store_failures_test']}.sql",
        f"""{{{{ config(store_failures=true{store_catalog}) }}}}
select 1 as id, 'expected failure for store_failures support check' as reason
""",
    )
    write(
        project_dir / "tests" / f"{names['retry_test']}.sql",
        """select 1 as id, 'expected failure for retry support check' as reason
""",
    )
    write(project_dir / "analyses" / f"{names['analysis']}.sql", f"select count(*) as row_count from {{{{ ref('{names['table']}') }}}}\n")
    snapshot_catalog = f", catalog_name='{target.catalog_name}'" if target.catalog_name else ""
    write(
        project_dir / "snapshots" / f"{names['snapshot']}.sql",
        f"""{{% snapshot {names['snapshot']} %}}
{{{{
    config(
      target_schema='aws_cloud_cost',
      unique_key='id',
      strategy='timestamp',
      updated_at='updated_at'{snapshot_catalog}
    )
}}}}
select * from {{{{ ref('{names['base']}') }}}}
{{% endsnapshot %}}
""",
    )
    return names


def feature_commands(feature: str, names: dict[str, str]) -> list[list[str]]:
    table = names["table"]
    return {
        "parse": [["parse"]],
        "compile": [["compile", "--select", table]],
        "table": [["run", "--select", names["base"]], ["run", "--select", table]],
        "view": [["run", "--select", names["base"]], ["run", "--select", names["view"]]],
        "ephemeral": [["run", "--select", names["base"]], ["run", "--select", names["uses_ephemeral"]]],
        "seed": [["seed", "--select", names["seed"]]],
        "generic_tests": [["run", "--select", names["base"]], ["run", "--select", table], ["test", "--select", table]],
        "singular_tests": [["run", "--select", names["base"]], ["run", "--select", table], ["test", "--select", names["singular_test"]]],
        "source_generic_tests": [["seed", "--select", names["seed"]], ["test", "--select", f"source:compat_source.{names['seed']}"]],
        "store_failures": [["test", "--select", names["store_failures_test"], "--store-failures"]],
        "unit_tests": [["run", "--select", names["base"]], ["build", "--resource-type", "unit_test", "--select", names["unit_test"]]],
        "snapshot": [["run", "--select", names["base"]], ["snapshot", "--select", names["snapshot"]]],
        "incremental_append": [["run", "--select", names["inc_append"]], ["run", "--select", names["inc_append"]]],
        "incremental_merge": [["run", "--select", names["inc_merge"]], ["run", "--select", names["inc_merge"]]],
        "incremental_delete_insert": [["run", "--select", names["inc_delete_insert"]], ["run", "--select", names["inc_delete_insert"]]],
        "hooks": [["run", "--select", names["base"]], ["run", "--select", names["hook"]]],
        "contract": [["run", "--select", names["contract"]]],
        "source_freshness": [["seed", "--select", names["seed"]], ["source", "freshness", "--select", f"source:compat_source.{names['seed']}"]],
        "show": [["run", "--select", names["base"]], ["run", "--select", table], ["show", "--select", table]],
        "catalog_json": [["run", "--select", names["base"]], ["run", "--select", table], ["compile", "--write-catalog", "--select", table]],
        "docs_generate": [["run", "--select", names["base"]], ["run", "--select", table], ["docs", "generate", "--select", table]],
        "clone": [["run", "--select", names["base"]], ["run", "--select", table], ["clone", "--select", table, "--state", "__TARGET_PATH__"]],
        "retry": [["test", "--select", names["retry_test"]], ["retry"]],
        "run_operation": [["run-operation", "compat_probe_ping"]],
        "build": [["build", "--select", f"+{table}"]],
        "analysis_compile": [["run", "--select", names["base"]], ["run", "--select", table], ["compile", "--select", names["analysis"]]],
    }[feature]


def all_features() -> list[str]:
    return [
        "parse",
        "compile",
        "table",
        "view",
        "ephemeral",
        "seed",
        "generic_tests",
        "singular_tests",
        "source_generic_tests",
        "store_failures",
        "unit_tests",
        "snapshot",
        "incremental_append",
        "incremental_merge",
        "incremental_delete_insert",
        "hooks",
        "contract",
        "source_freshness",
        "show",
        "catalog_json",
        "docs_generate",
        "clone",
        "retry",
        "run_operation",
        "build",
        "analysis_compile",
    ]


def run_command(project_dir: Path, cmd: list[str], env: dict[str, str], timeout: int) -> dict[str, object]:
    dbt_bin = env.get("DBT_BIN")
    if not dbt_bin:
        raise SystemExit("DBT_BIN must be set; run scripts/setup_env.sh and source .env first")
    resolved_cmd = [
        str(project_dir / "target") if arg == "__TARGET_PATH__" else str(project_dir) if arg == "__PROJECT_DIR__" else arg
        for arg in cmd
    ]
    full_cmd = [
        dbt_bin,
        *resolved_cmd,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
        "--target-path",
        str(project_dir / "target"),
        "--log-path",
        str(project_dir / "logs"),
        "--threads",
        "1",
        "--no-version-check",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            full_cmd,
            cwd=project_dir,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        code = proc.returncode
        output = proc.stdout
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        code = 124
        output = (exc.stdout or "") + (exc.stderr or "")
        timed_out = True
    elapsed = time.monotonic() - started
    return {
        "cmd": " ".join(cmd),
        "returncode": code,
        "elapsed_seconds": round(elapsed, 3),
        "timed_out": timed_out,
        "output": sanitize(output, env),
    }


def expected_test_failure(output: str) -> bool:
    lower = output.lower()
    if any(marker in lower for marker in ("database error", "adaptererror", "internal error", "unexpected result", "dbdriverfailed")):
        return False
    return "fail" in lower or "failure" in lower


def should_continue_after_failure(feature: str, step: dict[str, object]) -> bool:
    if feature == "retry" and expected_test_failure(str(step["output"])):
        return True
    return False


def classify_output(feature: str, steps: list[dict[str, object]]) -> str:
    if all(step["returncode"] == 0 for step in steps):
        return "pass"
    if feature == "store_failures" and steps and expected_test_failure(str(steps[-1]["output"])):
        return "pass"
    if feature == "retry" and len(steps) == 2 and expected_test_failure(str(steps[0]["output"])) and expected_test_failure(str(steps[1]["output"])):
        return "pass"
    if any(step.get("timed_out") for step in steps):
        return "timeout"
    return "fail"


def first_failure(feature: str, steps: list[dict[str, object]]) -> str:
    if feature == "store_failures" and steps and expected_test_failure(str(steps[-1]["output"])):
        return "expected failing test; nonzero exit means dbt stored/handled test failure path"
    if feature == "retry" and len(steps) == 2 and expected_test_failure(str(steps[0]["output"])) and expected_test_failure(str(steps[1]["output"])):
        return "expected failing test rerun by retry"
    for step in steps:
        if step["returncode"] != 0:
            lines = str(step["output"]).splitlines()
            useful = [line for line in lines if "[error]" in line or "Error" in line or "ERROR" in line or "failed" in line.lower()]
            return "\n".join(useful[:6]) or "\n".join(lines[-8:])
    return ""


def write_markdown(run_dir: Path, results: list[dict[str, object]]) -> None:
    lines = [
        "# dbt Feature Compatibility Probe",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Target | Feature | Result | First failure |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        failure = str(result.get("first_failure") or "").replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| {result['target']} | {result['feature']} | {result['result']} | {failure} |")
    lines.append("")
    lines.append("Raw sanitized command outputs are in `results.json`.")
    write(run_dir / "summary.md", "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--target", action="append", choices=sorted(catalog_targets()), dest="targets")
    parser.add_argument("--feature", action="append", choices=all_features(), dest="features")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    targets = catalog_targets()
    selected_targets = args.targets or ["builtin", "ducklake", "lakekeeper", "horizon_checkedin", "horizon_scripted", "unity", "polaris"]
    selected_features = args.features or all_features()
    env = os.environ.copy()
    env.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    env.setdefault("DBT_DISABLE_VERSION_CHECK", "true")

    run_dir = args.out_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "targets": selected_targets,
        "features": selected_features,
        "dbt_bin": env.get("DBT_BIN", ""),
        "duckdb_cli": env.get("DUCKDB_CLI", ""),
        "duckdb_extension_repository": env.get("DUCKDB_EXTENSION_REPOSITORY", ""),
    }
    results: list[dict[str, object]] = []
    for target_name in selected_targets:
        target = targets[target_name]
        for feature in selected_features:
            prefix = f"fc_{safe_name(args.run_id, 10)}_{safe_name(target_name, 10)}_{safe_name(feature, 8)}"
            project_dir = run_dir / target_name / feature
            names = write_fixture_project(project_dir, target, feature, prefix)
            steps = []
            for cmd in feature_commands(feature, names):
                step = run_command(project_dir, cmd, env, args.timeout)
                steps.append(step)
                if step["returncode"] != 0 and not should_continue_after_failure(feature, step):
                    break
            result = {
                "target": target_name,
                "target_note": target.note,
                "feature": feature,
                "result": classify_output(feature, steps),
                "first_failure": first_failure(feature, steps),
                "project_dir": str(project_dir),
                "steps": steps,
            }
            results.append(result)
            print(f"{target_name:18s} {feature:26s} {result['result']}")

    write(run_dir / "metadata.json", json.dumps(metadata, indent=2) + "\n")
    write(run_dir / "results.json", json.dumps(results, indent=2) + "\n")
    write_markdown(run_dir, results)
    print(f"wrote {run_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
