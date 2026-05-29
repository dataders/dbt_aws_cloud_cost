from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = ROOT / ".tmp" / "demo-workspace"
PROJECT_FILES = ("dbt_project.yml", "packages.yml")
PROJECT_DIRS = ("models", "macros", "local_files", "dbt_packages")

LAKEKEEPER_SECRET_BLOCK = """      secrets:
        - type: s3
          name: minio_secret
          key_id: minio-root-user
          secret: minio-root-password
          endpoint: "localhost:19000"
          url_style: path
          use_ssl: false
"""

POLARIS_SECRET_BLOCK = """      secrets:
        - type: iceberg
          name: polaris_oauth
          client_id: "{{ env_var('POLARIS_ID', '') }}"
          client_secret: "{{ env_var('POLARIS_SECRET', '') }}"
          oauth2_server_uri: "{{ env_var('POLARIS_OAUTH_TOKEN_URI', '') or ((env_var('POLARIS_URL', 'https://example.polaris.catalog')) ~ '/v1/oauth/tokens') }}"
          oauth2_scope: "{{ env_var('POLARIS_OAUTH_SCOPE', 'PRINCIPAL_ROLE:ALL') }}"
          oauth2_grant_type: "client_credentials"
"""

HORIZON_TOKEN_SECRET_BLOCK = """      secrets:
        - type: iceberg
          name: snowflake_oauth
          token: "{{ env_var('SNOWFLAKE_ACCESS_TOKEN', '') or env_var('HORIZON_ACCESS_TOKEN', '') }}"
"""

HORIZON_PAT_SECRET_BLOCK = """      secrets:
        - type: iceberg
          name: snowflake_oauth
          client_id: ""
          client_secret: "{{ env_var('HORIZON_PAT', '') }}"
          oauth2_server_uri: "{{ env_var('SNOWFLAKE_OAUTH2_SERVER_URI', '') or env_var('HORIZON_OAUTH2_SERVER_URI', '') or ((env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog')) ~ '/v1/oauth/tokens') }}"
          oauth2_scope: "{{ env_var('SNOWFLAKE_OAUTH2_SCOPE', '') or env_var('HORIZON_OAUTH2_SCOPE', 'session:role:ACCOUNTADMIN') }}"
          oauth2_grant_type: "client_credentials"
"""

HORIZON_CLIENT_SECRET_BLOCK = """      secrets:
        - type: iceberg
          name: snowflake_oauth
          client_id: "{{ env_var('SNOWFLAKE_CLIENT_ID', '') or env_var('HORIZON_CLIENT_ID', 'snowflake') }}"
          client_secret: "{{ env_var('SNOWFLAKE_CLIENT_SECRET', '') or env_var('HORIZON_CLIENT_SECRET', '') }}"
          oauth2_server_uri: "{{ env_var('SNOWFLAKE_OAUTH2_SERVER_URI', '') or env_var('HORIZON_OAUTH2_SERVER_URI', '') or ((env_var('SNOWFLAKE_CATALOG_URI', '') or env_var('HORIZON_ENDPOINT', 'https://example.snowflakecomputing.com/polaris/api/catalog')) ~ '/v1/oauth/tokens') }}"
          oauth2_scope: "{{ env_var('SNOWFLAKE_OAUTH2_SCOPE', '') or env_var('HORIZON_OAUTH2_SCOPE', 'session:role:ACCOUNTADMIN') }}"
          oauth2_grant_type: "client_credentials"
"""

UNITY_SECRET_BLOCK = """      secrets:
        - type: iceberg
          name: databricks_token
          token: "{{ env_var('DATABRICKS_TOKEN', '') }}"
"""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1].replace("'\\''", "'")
        os.environ[key] = value


def secret_block(catalog: str) -> str | None:
    if catalog == "lakekeeper":
        return LAKEKEEPER_SECRET_BLOCK
    if catalog == "polaris":
        return POLARIS_SECRET_BLOCK
    if catalog == "horizon":
        if os.environ.get("HORIZON_PAT"):
            return HORIZON_PAT_SECRET_BLOCK
        if (
            os.environ.get("SNOWFLAKE_CLIENT_SECRET")
            or os.environ.get("HORIZON_CLIENT_SECRET")
        ):
            return HORIZON_CLIENT_SECRET_BLOCK
        if os.environ.get("SNOWFLAKE_ACCESS_TOKEN") or os.environ.get("HORIZON_ACCESS_TOKEN"):
            return HORIZON_TOKEN_SECRET_BLOCK
        return HORIZON_CLIENT_SECRET_BLOCK
    if catalog == "unity":
        return UNITY_SECRET_BLOCK
    return None


def catalog_blocks() -> dict[str, str]:
    text = (ROOT / "catalogs.yml").read_text()
    blocks: dict[str, list[str]] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("  - name: "):
            if current_name is not None:
                blocks[current_name] = current_lines
            current_name = line.split(":", 1)[1].strip()
            current_lines = [line]
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        blocks[current_name] = current_lines

    return {name: "\n".join(lines).rstrip() for name, lines in blocks.items()}


def normalize_catalogs(include_catalogs: list[str]) -> list[str]:
    aliases = {
        "local": "local_files",
        "builtin": "aws_cloud_cost",
    }
    result: list[str] = []
    for catalog in include_catalogs:
        normalized = aliases.get(catalog, catalog)
        if normalized in ("", "aws_cloud_cost"):
            continue
        if normalized == "all":
            return list(catalog_blocks().keys())
        if normalized not in result:
            result.append(normalized)
    return result or ["local_files"]


def render_catalogs(include_catalogs: list[str]) -> str:
    blocks = catalog_blocks()
    missing = [catalog for catalog in include_catalogs if catalog not in blocks]
    if missing:
        raise SystemExit(f"unknown catalog(s): {', '.join(missing)}")

    rendered = ["catalogs:"]
    rendered.extend(blocks[catalog] for catalog in include_catalogs)
    return "\n\n".join(rendered) + "\n"


def render_profile(include_catalogs: list[str]) -> str:
    text = """aws_cloud_cost:
  target: catalog_demo
  outputs:
    catalog_demo:
      type: duckdb
      path: ".tmp/aws_cloud_cost.duckdb"
      schema: aws_cloud_cost
      threads: 4
      settings:
        allow_unsigned_extensions: true
"""

    selected_secrets = [
        block
        for catalog in include_catalogs
        if (block := secret_block(catalog)) is not None
    ]
    if not selected_secrets:
        return text

    first, *rest = selected_secrets
    for block in rest:
        first += block.replace("      secrets:\n", "")
    return text + first


def prepare_workspace(workspace: Path, include_catalogs: list[str]) -> None:
    workspace = workspace.resolve()
    tmp_root = (ROOT / ".tmp").resolve()
    if not workspace.is_relative_to(tmp_root):
        raise SystemExit(f"workspace must be under {tmp_root}")

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / ".tmp").mkdir()

    for filename in PROJECT_FILES:
        shutil.copy2(ROOT / filename, workspace / filename)

    for dirname in PROJECT_DIRS:
        target = ROOT / dirname
        if target.exists():
            os.symlink(os.path.relpath(target, workspace), workspace / dirname)

    (workspace / "catalogs.yml").write_text(render_catalogs(include_catalogs))
    (workspace / "profiles.yml").write_text(render_profile(include_catalogs))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument(
        "--include-catalog",
        action="append",
        default=[],
        help="Catalog to keep in the scoped catalogs.yml. Repeatable.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    include_catalogs = normalize_catalogs(args.include_catalog)
    prepare_workspace(args.workspace, include_catalogs)
    print(args.workspace.resolve())


if __name__ == "__main__":
    main()
