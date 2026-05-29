from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CONFIG_TEMPLATE = ROOT / "shadowtraffic" / "config.json"
RENDERED_CONFIG = ROOT / ".tmp" / "shadowtraffic-config.json"


SOURCE_COLUMNS = {
    "_file": "VARCHAR",
    "_line": "INTEGER",
    "_fivetran_synced": "TIMESTAMP_NTZ",
    "_modified": "TIMESTAMP_NTZ",
    "bill_bill_type": "VARCHAR",
    "bill_billing_period_start_date": "TIMESTAMP_NTZ",
    "bill_billing_period_end_date": "TIMESTAMP_NTZ",
    "bill_payer_account_id": "INTEGER",
    "bill_payer_account_name": "VARCHAR",
    "identity_line_item_id": "VARCHAR",
    "line_item_line_item_description": "VARCHAR",
    "line_item_line_item_type": "VARCHAR",
    "line_item_blended_cost": "FLOAT",
    "line_item_blended_rate": "FLOAT",
    "line_item_currency_code": "VARCHAR",
    "line_item_normalization_factor": "FLOAT",
    "line_item_normalized_usage_amount": "FLOAT",
    "line_item_operation": "VARCHAR",
    "line_item_product_code": "VARCHAR",
    "line_item_unblended_cost": "FLOAT",
    "line_item_unblended_rate": "FLOAT",
    "line_item_usage_account_id": "INTEGER",
    "line_item_usage_account_name": "VARCHAR",
    "line_item_usage_amount": "FLOAT",
    "line_item_usage_start_date": "TIMESTAMP_NTZ",
    "line_item_usage_end_date": "TIMESTAMP_NTZ",
    "line_item_usage_type": "VARCHAR",
    "pricing_public_on_demand_cost": "FLOAT",
    "pricing_public_on_demand_rate": "FLOAT",
    "pricing_purchase_option": "VARCHAR",
    "pricing_term": "VARCHAR",
    "pricing_unit": "VARCHAR",
    "product_product_name": "VARCHAR",
    "product_product_family": "VARCHAR",
    "product_servicecode": "VARCHAR",
    "product_instance_type": "VARCHAR",
    "product_instance_family": "VARCHAR",
    "product_location": "VARCHAR",
    "product_location_type": "VARCHAR",
    "product_region_code": "VARCHAR",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"missing {path}; run scripts/setup_env.sh first")

    explicit_overrides = {
        name.strip()
        for name in os.environ.get("DBT_AWS_CLOUD_COST_ENV_OVERRIDES", "").replace(",", " ").split()
        if name.strip()
    }
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in explicit_overrides and key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1].replace("'\\''", "'")
        os.environ[key] = value


def quote_env(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upsert_dotenv(path: Path, updates: dict[str, str]) -> None:
    existing_lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    rendered: list[str] = []

    for line in existing_lines:
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in updates:
                rendered.append(f"{key}={quote_env(updates[key])}")
                seen.add(key)
                continue
        rendered.append(line)

    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={quote_env(value)}")

    path.write_text("\n".join(rendered) + "\n")
    path.chmod(0o600)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"missing required env var: {name}")
    return value


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def snowflake_private_key():
    from cryptography.hazmat.primitives import serialization

    private_key_text = env("SNOWFLAKE_PRIVATE_KEY")
    try:
        private_key_bytes = base64.b64decode(private_key_text)
        return serialization.load_der_private_key(private_key_bytes, password=None)
    except ValueError:
        return serialization.load_pem_private_key(private_key_text.encode(), password=None)


def public_key_fingerprint(private_key) -> str:
    from cryptography.hazmat.primitives import serialization

    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(public_der).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii")


def snowflake_jwt() -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    account = env("SNOWFLAKE_ACCOUNT").upper()
    user = env("SNOWFLAKE_USER").upper()
    private_key = snowflake_private_key()
    subject = f"{account}.{user}"
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss": f"{subject}.{public_key_fingerprint(private_key)}",
        "sub": subject,
        "iat": now,
        "exp": now + 55 * 60,
    }
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    ).encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + b64url(signature)


def sql_api_url() -> str:
    return f"https://{env('SNOWFLAKE_SQL_API_HOST')}/api/v2/statements"


def request_body(
    statement: str,
    *,
    include_context: bool = True,
    role: str | None = None,
) -> bytes:
    body = {
        "statement": statement,
        "warehouse": env("SNOWFLAKE_WAREHOUSE"),
        "timeout": 60,
    }
    if include_context:
        body["database"] = env("SNOWFLAKE_DATABASE")
        body["schema"] = env("SNOWFLAKE_SCHEMA")
    if role is None:
        if role := os.environ.get("SNOWFLAKE_ROLE"):
            body["role"] = role
    elif role:
        body["role"] = role
    return json.dumps(body).encode("utf-8")


def execute_statement(
    statement: str,
    *,
    include_context: bool = True,
    role: str | None = None,
) -> dict:
    request = urllib.request.Request(
        sql_api_url(),
        data=request_body(statement, include_context=include_context, role=role),
        method="POST",
        headers={
            "Authorization": f"Bearer {snowflake_jwt()}",
            "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "dbt-aws-cloud-cost-demo/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Snowflake SQL API error {error.code}: {detail}") from error


def quote_ident(identifier: str) -> str:
    identifier = identifier.upper()
    return '"' + identifier.replace('"', '""') + '"'


def source_relation() -> str:
    return ".".join(
        quote_ident(part)
        for part in [env("SNOWFLAKE_DATABASE"), env("SNOWFLAKE_SCHEMA"), env("SNOWFLAKE_TABLE")]
    )


def horizon_database_name() -> str:
    return os.environ.get("HORIZON_WAREHOUSE") or env("SNOWFLAKE_DATABASE")


def horizon_schema_name() -> str:
    return os.environ.get("HORIZON_SCHEMA") or env("SNOWFLAKE_SCHEMA")


def horizon_schema_relation() -> str:
    return ".".join(quote_ident(part) for part in [horizon_database_name(), horizon_schema_name()])


def horizon_external_volume() -> str:
    return (
        os.environ.get("HORIZON_EXTERNAL_VOLUME")
        or os.environ.get("SNOWFLAKE_EXTERNAL_VOLUME")
        or "SNOWFLAKE_MANAGED"
    )


def create_source_sql() -> str:
    columns = ",\n    ".join(
        f"{quote_ident(column)} {data_type}" for column, data_type in SOURCE_COLUMNS.items()
    )
    return f"CREATE TABLE IF NOT EXISTS {source_relation()} (\n    {columns}\n)"


def setup_source() -> None:
    execute_statement(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(env('SNOWFLAKE_DATABASE'))}.{quote_ident(env('SNOWFLAKE_SCHEMA'))}")
    execute_statement(create_source_sql())
    execute_statement(f"TRUNCATE TABLE {source_relation()}")
    print(f"ready: {source_relation()}")


def drop_source() -> None:
    execute_statement(f"DROP TABLE IF EXISTS {source_relation()}")
    print(f"dropped: {source_relation()}")


def count_rows() -> None:
    result = execute_statement(f"SELECT COUNT(*) AS row_count FROM {source_relation()}")
    data = result.get("data") or []
    rows = data[0][0] if data and data[0] else "0"
    print(f"{source_relation()} rows: {rows}")


def configured_status(name: str) -> str:
    return "configured: yes" if os.environ.get(name) else "configured: no"


def printable_value(name: str, default: str = "<unset>") -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def probe_statement(
    label: str,
    statement: str,
    *,
    include_context: bool = True,
    role: str | None = None,
) -> bool:
    print(f"probe: {label}")
    try:
        result = execute_statement(statement, include_context=include_context, role=role)
    except SystemExit as error:
        print(f"  failed: {error}")
        return False

    data = result.get("data") or []
    if data and data[0]:
        print("  ok: " + ", ".join(str(value) for value in data[0]))
    else:
        print("  ok")
    return True


def parameter_value(result: dict, key: str) -> str:
    for row in result.get("data") or []:
        if row and str(row[0]).upper() == key.upper():
            return str(row[1] or "")
    return ""


def configure_horizon_schema() -> None:
    relation = horizon_schema_relation()
    external_volume = horizon_external_volume()
    execute_statement(f"CREATE SCHEMA IF NOT EXISTS {relation}", include_context=False)
    execute_statement(
        f"ALTER SCHEMA {relation} SET CATALOG = 'SNOWFLAKE', "
        f"EXTERNAL_VOLUME = {quote_sql_string(external_volume)}",
        include_context=False,
    )
    print(f"configured Horizon schema defaults on {relation}")


def probe_horizon_schema_defaults() -> bool:
    relation = horizon_schema_relation()
    print("probe: Horizon schema write defaults")
    try:
        catalog_result = execute_statement(
            f"show parameters like 'CATALOG%' in schema {relation}",
            include_context=False,
        )
        external_volume_result = execute_statement(
            f"show parameters like 'EXTERNAL_VOLUME' in schema {relation}",
            include_context=False,
        )
    except SystemExit as error:
        print(f"  failed: {error}")
        return False

    catalog = parameter_value(catalog_result, "CATALOG")
    external_volume = parameter_value(external_volume_result, "EXTERNAL_VOLUME")
    if catalog.upper() == "SNOWFLAKE" and external_volume:
        print(f"  ok: CATALOG={catalog}, EXTERNAL_VOLUME={external_volume}")
        return True

    print(
        "  failed: expected CATALOG=SNOWFLAKE and a configured EXTERNAL_VOLUME; "
        "run scripts/configure_horizon_schema.sh"
    )
    return False


def horizon_config_url() -> str:
    endpoint = env("HORIZON_ENDPOINT").rstrip("/")
    query = urllib.parse.urlencode({"warehouse": env("HORIZON_WAREHOUSE")})
    return f"{endpoint}/v1/config?{query}"


def horizon_oauth2_server_uri() -> str:
    return os.environ.get("HORIZON_OAUTH2_SERVER_URI") or (
        env("HORIZON_ENDPOINT").rstrip("/") + "/v1/oauth/tokens"
    )


def request_horizon_access_token() -> tuple[str, int | None]:
    scope = os.environ.get("HORIZON_OAUTH2_SCOPE") or "session:role:" + env("SNOWFLAKE_ROLE")
    form = {
        "grant_type": "client_credentials",
        "scope": scope,
        "client_secret": os.environ.get("HORIZON_CLIENT_SECRET") or env("HORIZON_PAT"),
    }
    client_secret = os.environ.get("HORIZON_CLIENT_SECRET")
    if client_secret:
        form["client_id"] = env("HORIZON_CLIENT_ID")

    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        horizon_oauth2_server_uri(),
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "dbt-aws-cloud-cost-demo/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Horizon OAuth2 token request failed {error.code}: {detail}") from error

    token = payload.get("access_token")
    if not token:
        raise SystemExit("Horizon OAuth2 token response did not include access_token")
    expires_in = payload.get("expires_in")
    return token, int(expires_in) if isinstance(expires_in, int) else None


def refresh_horizon_token() -> None:
    token, expires_in = request_horizon_access_token()
    updates = {"HORIZON_ACCESS_TOKEN": token}
    if expires_in is not None:
        updates["HORIZON_ACCESS_TOKEN_EXPIRES_AT"] = str(int(time.time()) + expires_in)
    upsert_dotenv(ROOT / ".env", updates)
    os.environ.update(updates)
    print(f"wrote HORIZON_ACCESS_TOKEN to {ROOT / '.env'}")


def horizon_bearer_token() -> str:
    if os.environ.get("SNOWFLAKE_ACCESS_TOKEN"):
        return env("SNOWFLAKE_ACCESS_TOKEN")
    if os.environ.get("HORIZON_ACCESS_TOKEN") and not (
        os.environ.get("HORIZON_PAT") or os.environ.get("HORIZON_CLIENT_SECRET")
    ):
        return env("HORIZON_ACCESS_TOKEN")
    token, _ = request_horizon_access_token()
    return token


def probe_horizon_catalog_config() -> bool:
    print("probe: Horizon catalog config")
    try:
        token = horizon_bearer_token()
        request = urllib.request.Request(
            horizon_config_url(),
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "dbt-aws-cloud-cost-demo/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except SystemExit as error:
        print(f"  failed: {error}")
        return False
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(f"  failed: Horizon catalog config returned HTTP {error.code}: {detail}")
        return False

    print("  ok")
    return True


def doctor() -> int:
    print("dbt_aws_cloud_cost demo doctor")
    print(f"SNOWFLAKE_ACCOUNT: {printable_value('SNOWFLAKE_ACCOUNT')}")
    print(f"SNOWFLAKE_SQL_API_HOST: {printable_value('SNOWFLAKE_SQL_API_HOST')}")
    print(f"SNOWFLAKE_USER: {printable_value('SNOWFLAKE_USER')}")
    print(f"SNOWFLAKE_DATABASE: {printable_value('SNOWFLAKE_DATABASE')}")
    print(f"SNOWFLAKE_SCHEMA: {printable_value('SNOWFLAKE_SCHEMA')}")
    print(f"SNOWFLAKE_TABLE: {printable_value('SNOWFLAKE_TABLE')}")
    print(f"SNOWFLAKE_WAREHOUSE: {printable_value('SNOWFLAKE_WAREHOUSE')}")
    print(f"SNOWFLAKE_ROLE: {printable_value('SNOWFLAKE_ROLE', '<omitted>')}")
    print(f"SNOWFLAKE_PRIVATE_KEY: {configured_status('SNOWFLAKE_PRIVATE_KEY')}")
    print(f"HORIZON_PAT: {configured_status('HORIZON_PAT')}")
    print(f"HORIZON_ACCESS_TOKEN: {configured_status('HORIZON_ACCESS_TOKEN')}")
    print(f"HORIZON_CLIENT_SECRET: {configured_status('HORIZON_CLIENT_SECRET')}")
    print(f"HORIZON_OAUTH2_SERVER_URI: {printable_value('HORIZON_OAUTH2_SERVER_URI')}")
    print(f"HORIZON_OAUTH2_SCOPE: {printable_value('HORIZON_OAUTH2_SCOPE')}")
    print(f"HORIZON_EXTERNAL_VOLUME: {printable_value('HORIZON_EXTERNAL_VOLUME', horizon_external_volume())}")

    ok = True
    ok &= probe_statement(
        "SQL API auth with default role",
        "select current_user(), current_role()",
        include_context=False,
        role="",
    )

    configured_role = os.environ.get("SNOWFLAKE_ROLE")
    if configured_role:
        ok &= probe_statement(
            f"SQL API auth with configured role {configured_role}",
            "select current_user(), current_role()",
            include_context=False,
            role=configured_role,
        )

    ok &= probe_statement(
        "configured database and schema context",
        "select current_database(), current_schema()",
        include_context=True,
    )
    ok &= probe_horizon_catalog_config()
    ok &= probe_horizon_schema_defaults()

    if ok:
        print("doctor: no SQL API or Horizon catalog blockers detected")
        return 0

    print("doctor: one or more probes failed")
    return 1


def result_row_dict(result: dict) -> dict[str, str]:
    rows = result.get("data") or []
    if not rows:
        raise SystemExit("Snowflake PAT command returned no rows")

    metadata = result.get("resultSetMetaData") or {}
    row_type = metadata.get("rowType") or []
    names = [str(column.get("name", "")).lower() for column in row_type]
    if not names:
        raise SystemExit("Snowflake PAT command returned no metadata")

    return dict(zip(names, rows[0]))


def create_horizon_pat() -> None:
    role = env("SNOWFLAKE_ROLE")
    token_name = f"CODEX_HORIZON_DEMO_{int(time.time())}"
    statement = (
        f"ALTER USER ADD PROGRAMMATIC ACCESS TOKEN {quote_ident(token_name)} "
        f"ROLE_RESTRICTION = '{role.replace("'", "''")}' "
        "DAYS_TO_EXPIRY = 1 "
        "COMMENT = 'Short-lived PAT for local dbt DuckDB Horizon catalog demo'"
    )
    result = execute_statement(statement, include_context=False)
    row = result_row_dict(result)
    token_secret = row.get("token_secret")
    if not token_secret:
        raise SystemExit("Snowflake PAT command did not return token_secret")

    scope = f"session:role:{role}"
    upsert_dotenv(ROOT / ".env", {"HORIZON_PAT": token_secret, "HORIZON_OAUTH2_SCOPE": scope})
    os.environ["HORIZON_PAT"] = token_secret
    os.environ["HORIZON_OAUTH2_SCOPE"] = scope
    print(f"wrote HORIZON_PAT for {token_name} to {ROOT / '.env'}")


def render_shadowtraffic_config() -> None:
    config = CONFIG_TEMPLATE.read_text()
    replacements = {
        "__SNOWFLAKE_JWT__": snowflake_jwt(),
        "__SNOWFLAKE_SQL_API_HOST__": env("SNOWFLAKE_SQL_API_HOST"),
        "__SNOWFLAKE_DATABASE__": env("SNOWFLAKE_DATABASE"),
        "__SNOWFLAKE_SCHEMA__": env("SNOWFLAKE_SCHEMA"),
        "__SNOWFLAKE_WAREHOUSE__": env("SNOWFLAKE_WAREHOUSE"),
        "__SNOWFLAKE_ROLE__": os.environ.get("SNOWFLAKE_ROLE", ""),
        "AWS_CLOUD_COST.AWS_COST_REPORT": f"{env('SNOWFLAKE_DATABASE')}.{env('SNOWFLAKE_SCHEMA')}.{env('SNOWFLAKE_TABLE')}",
    }
    for old, new in replacements.items():
        config = config.replace(old, new)
    parsed = json.loads(config)
    if not os.environ.get("SNOWFLAKE_ROLE"):
        parsed["generators"][0]["data"].pop("role", None)

    RENDERED_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    RENDERED_CONFIG.write_text(json.dumps(parsed, indent=2) + "\n")
    RENDERED_CONFIG.chmod(0o600)
    print(RENDERED_CONFIG)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "setup-source",
            "drop-source",
            "count",
            "render-shadowtraffic-config",
            "doctor",
            "configure-horizon-schema",
            "create-horizon-pat",
            "refresh-horizon-token",
        ],
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    if args.command == "setup-source":
        setup_source()
    elif args.command == "drop-source":
        drop_source()
    elif args.command == "count":
        count_rows()
    elif args.command == "render-shadowtraffic-config":
        render_shadowtraffic_config()
    elif args.command == "doctor":
        raise SystemExit(doctor())
    elif args.command == "configure-horizon-schema":
        configure_horizon_schema()
    elif args.command == "create-horizon-pat":
        create_horizon_pat()
    elif args.command == "refresh-horizon-token":
        refresh_horizon_token()


if __name__ == "__main__":
    main()
