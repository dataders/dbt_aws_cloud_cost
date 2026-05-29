from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CONFIG_TEMPLATE = ROOT / "shadowtraffic" / "config.json"
RENDERED_CONFIG = ROOT / ".tmp" / "shadowtraffic-polaris-config.json"


STRING_COLUMNS = {
    "_file",
    "_fivetran_synced",
    "_modified",
    "bill_bill_type",
    "bill_billing_entity",
    "bill_billing_period_end_date",
    "bill_billing_period_start_date",
    "bill_invoicing_entity",
    "bill_payer_account_name",
    "identity_line_item_id",
    "identity_time_interval",
    "line_item_availability_zone",
    "line_item_currency_code",
    "line_item_line_item_description",
    "line_item_line_item_type",
    "line_item_operation",
    "line_item_product_code",
    "line_item_resource_id",
    "line_item_tax_type",
    "line_item_usage_account_name",
    "line_item_usage_end_date",
    "line_item_usage_start_date",
    "line_item_usage_type",
    "pricing_currency",
    "pricing_purchase_option",
    "pricing_term",
    "pricing_unit",
    "product_pricing_unit",
    "product_fee_code",
    "product_fee_description",
    "product_from_location",
    "product_from_location_type",
    "product_from_region_code",
    "product_instance_family",
    "product_instance_type",
    "product_location",
    "product_location_type",
    "product_operation",
    "product",
    "product_product_name",
    "product_product_family",
    "product_region_code",
    "product_servicecode",
    "product_to_location",
    "product_to_location_type",
    "product_to_region_code",
    "product_usagetype",
}

INTEGER_COLUMNS = {
    "_line",
    "bill_invoice_id",
    "bill_payer_account_id",
    "line_item_usage_account_id",
    "reservation_number_of_reservations",
}

FLOAT_COLUMNS = {
    "line_item_blended_cost",
    "line_item_blended_rate",
    "line_item_normalization_factor",
    "line_item_normalized_usage_amount",
    "line_item_unblended_cost",
    "line_item_unblended_rate",
    "line_item_usage_amount",
    "pricing_public_on_demand_cost",
    "pricing_public_on_demand_rate",
    "reservation_amortized_upfront_cost_for_usage",
    "reservation_amortized_upfront_fee_for_billing_period",
    "reservation_effective_cost",
    "reservation_normalized_units_per_reservation",
    "reservation_recurring_fee_for_usage",
    "reservation_total_reserved_normalized_units",
    "reservation_total_reserved_units",
    "reservation_units_per_reservation",
    "reservation_unused_amortized_upfront_fee_for_billing_period",
    "reservation_unused_normalized_unit_quantity",
    "reservation_unused_quantity",
    "reservation_unused_recurring_fee",
    "reservation_upfront_value",
    "savings_plan_amortized_upfront_commitment_for_billing_period",
    "savings_plan_recurring_commitment_for_billing_period",
    "savings_plan_savings_plan_effective_cost",
    "savings_plan_savings_plan_rate",
    "savings_plan_total_commitment_to_date",
    "savings_plan_used_commitment",
}

TIMESTAMP_COLUMNS = {
    "_fivetran_synced",
    "_modified",
    "bill_billing_period_end_date",
    "bill_billing_period_start_date",
    "line_item_usage_end_date",
    "line_item_usage_start_date",
}

STRING_COLUMNS = STRING_COLUMNS - TIMESTAMP_COLUMNS
REPORT_COLUMNS = sorted(STRING_COLUMNS | INTEGER_COLUMNS | FLOAT_COLUMNS | TIMESTAMP_COLUMNS)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"missing required env var: {name}")
    return value


def optional_env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def polaris_url() -> str:
    return env("POLARIS_URL").rstrip("/")


def polaris_warehouse() -> str:
    return env("POLARIS_WAREHOUSE")


def polaris_scope() -> str:
    return optional_env("POLARIS_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")


def polaris_token_uri() -> str:
    return os.environ.get("POLARIS_OAUTH_TOKEN_URI") or polaris_url() + "/v1/oauth/tokens"


def polaris_namespace() -> str:
    return optional_env(
        "AWS_CLOUD_COST_SOURCE_SCHEMA",
        optional_env("POLARIS_NAMESPACE", "aws_cloud_cost"),
    )


def polaris_table() -> str:
    return optional_env(
        "AWS_CLOUD_COST_SOURCE_TABLE",
        optional_env("POLARIS_TABLE", "aws_cost_report"),
    )


def polaris_access_delegation() -> str:
    mode = optional_env("POLARIS_ACCESS_DELEGATION_MODE", "vended-credentials")
    if mode.upper() == "VENDED_CREDENTIALS":
        return "vended-credentials"
    return mode


def shadowtraffic_max_events() -> int:
    return int(optional_env("SHADOWTRAFFIC_MAX_EVENTS", "100"))


def oauth_token() -> str:
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": env("POLARIS_ID"),
            "client_secret": env("POLARIS_SECRET"),
            "scope": polaris_scope(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        polaris_token_uri(),
        data=form,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "dbt-aws-cloud-cost-shadowtraffic/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Polaris OAuth2 token request failed {error.code}: {detail}") from error

    token = payload.get("access_token")
    if not token:
        raise SystemExit("Polaris OAuth2 token response did not include access_token")
    return token


def rest_request(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        polaris_url() + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {oauth_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "dbt-aws-cloud-cost-shadowtraffic/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Polaris REST request failed {error.code}: {detail}") from error
    return json.loads(raw) if raw else {}


def namespace_exists() -> bool:
    warehouse = urllib.parse.quote(polaris_warehouse(), safe="")
    namespace = urllib.parse.quote(polaris_namespace(), safe="")
    try:
        rest_request(f"/v1/{warehouse}/namespaces/{namespace}")
    except SystemExit as error:
        if "NoSuchNamespaceException" in str(error) or "NotFound" in str(error):
            return False
        raise
    return True


def default_base_location() -> str:
    query = urllib.parse.urlencode({"warehouse": polaris_warehouse()})
    config = rest_request(f"/v1/config?{query}")
    location = (config.get("defaults") or {}).get("default-base-location")
    if not location:
        raise SystemExit("Polaris catalog config did not include default-base-location")
    return location


def ensure_namespace() -> None:
    if namespace_exists():
        return

    namespace = polaris_namespace()
    warehouse = urllib.parse.quote(polaris_warehouse(), safe="")
    path = f"/v1/{warehouse}/namespaces"
    try:
        rest_request(path, method="POST", body={"namespace": [namespace]})
    except SystemExit as error:
        message = str(error)
        if "AlreadyExistsException" not in message and "already exists" not in message:
            raise


def scalar_string(value: str) -> dict:
    return {"_gen": "oneOf", "choices": [value]}


def report_data() -> dict:
    data = {column: "" for column in REPORT_COLUMNS}
    now = {"_gen": "formatDateTime", "ms": {"_gen": "now"}, "format": "yyyy-MM-dd HH:mm:ss.SSS"}

    data.update(
        {
            "_file": scalar_string("shadowtraffic/aws-cost/2026-05/aws-cost-report.parquet"),
            "_fivetran_synced": now,
            "_line": {"_gen": "uniformDistribution", "bounds": [1, 500000], "decimals": 0},
            "_modified": now,
            "bill_bill_type": scalar_string("Anniversary"),
            "bill_billing_entity": scalar_string("AWS"),
            "bill_billing_period_start_date": scalar_string("2026-05-01 00:00:00"),
            "bill_billing_period_end_date": scalar_string("2026-06-01 00:00:00"),
            "bill_invoicing_entity": scalar_string("Amazon Web Services, Inc."),
            "bill_payer_account_id": {"_gen": "oneOf", "choices": [100000000001, 100000000002]},
            "bill_payer_account_name": {"_gen": "oneOf", "choices": ["Platform", "Analytics"]},
            "identity_line_item_id": {"_gen": "uuid"},
            "identity_time_interval": scalar_string("2026-05-01T00:00:00Z/2026-06-01T00:00:00Z"),
            "line_item_line_item_description": {
                "_gen": "oneOf",
                "choices": ["EC2 instance hours", "S3 standard storage", "Lambda requests", "Data transfer"],
            },
            "line_item_line_item_type": {
                "_gen": "weightedOneOf",
                "choices": [
                    {"weight": 82, "value": "Usage"},
                    {"weight": 10, "value": "DiscountedUsage"},
                    {"weight": 8, "value": "RIFee"},
                ],
            },
            "line_item_blended_cost": {"_gen": "uniformDistribution", "bounds": [0.01, 18.0], "decimals": 4},
            "line_item_blended_rate": {"_gen": "uniformDistribution", "bounds": [0.001, 3.0], "decimals": 5},
            "line_item_currency_code": scalar_string("USD"),
            "line_item_normalization_factor": 1.0,
            "line_item_normalized_usage_amount": {"_gen": "uniformDistribution", "bounds": [0.1, 40.0], "decimals": 4},
            "line_item_operation": {"_gen": "oneOf", "choices": ["RunInstances", "TimedStorage-ByteHrs", "Invoke", "DataTransfer"]},
            "line_item_product_code": {"_gen": "oneOf", "choices": ["AmazonEC2", "AmazonS3", "AWSLambda", "AWSDataTransfer"]},
            "line_item_unblended_cost": {"_gen": "uniformDistribution", "bounds": [0.01, 20.0], "decimals": 4},
            "line_item_unblended_rate": {"_gen": "uniformDistribution", "bounds": [0.001, 3.2], "decimals": 5},
            "line_item_usage_account_id": {"_gen": "oneOf", "choices": [200000000001, 200000000002, 200000000003]},
            "line_item_usage_account_name": {"_gen": "oneOf", "choices": ["Core Services", "Data Platform", "Developer Experience"]},
            "line_item_usage_amount": {"_gen": "uniformDistribution", "bounds": [0.1, 40.0], "decimals": 4},
            "line_item_usage_start_date": now,
            "line_item_usage_end_date": now,
            "line_item_usage_type": {"_gen": "oneOf", "choices": ["BoxUsage:m7g.large", "TimedStorage-ByteHrs", "Request", "DataTransfer-Out-Bytes"]},
            "pricing_currency": scalar_string("USD"),
            "pricing_public_on_demand_cost": {"_gen": "uniformDistribution", "bounds": [0.01, 22.0], "decimals": 4},
            "pricing_public_on_demand_rate": {"_gen": "uniformDistribution", "bounds": [0.001, 4.0], "decimals": 5},
            "pricing_purchase_option": {"_gen": "oneOf", "choices": ["On Demand", "No Upfront", "Partial Upfront"]},
            "pricing_term": {"_gen": "oneOf", "choices": ["OnDemand", "Reserved"]},
            "pricing_unit": {"_gen": "oneOf", "choices": ["Hrs", "GB-Mo", "Requests", "GB"]},
            "product": {
                "_gen": "interpolate",
                "template": "{\"product_name\":\":product_name\"}",
                "params": {"product_name": {"_gen": "oneOf", "choices": ["Amazon Elastic Compute Cloud", "Amazon Simple Storage Service", "AWS Lambda", "AWS Data Transfer"]}},
            },
            "product_product_name": {"_gen": "oneOf", "choices": ["Amazon Elastic Compute Cloud", "Amazon Simple Storage Service", "AWS Lambda", "AWS Data Transfer"]},
            "product_product_family": {"_gen": "oneOf", "choices": ["Compute Instance", "Storage", "Serverless", "Data Transfer"]},
            "product_servicecode": {"_gen": "oneOf", "choices": ["AmazonEC2", "AmazonS3", "AWSLambda", "AWSDataTransfer"]},
            "product_instance_type": {"_gen": "oneOf", "choices": ["m7g.large", "c7g.xlarge", "", ""]},
            "product_instance_family": {"_gen": "oneOf", "choices": ["General purpose", "Compute optimized", "", ""]},
            "product_location": {"_gen": "oneOf", "choices": ["US East (N. Virginia)", "US West (Oregon)", "EU (Ireland)"]},
            "product_location_type": scalar_string("AWS Region"),
            "product_region_code": {"_gen": "oneOf", "choices": ["us-east-1", "us-west-2", "eu-west-1"]},
        }
    )
    return data


def table_identifier() -> str:
    return f"{polaris_namespace()}.{polaris_table()}"


def iter_json_objects(text: str):
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start == -1:
            return
        try:
            value, offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        yield value
        index = start + offset


def shadowtraffic_records(path: Path) -> list[dict]:
    records = []
    for event in iter_json_objects(path.read_text()):
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("value"), dict):
            records.append(event["value"])
        elif isinstance(event.get("data"), dict):
            records.append(event["data"])
        elif any(column in event for column in REPORT_COLUMNS):
            records.append(event)
    if not records:
        raise SystemExit(f"ShadowTraffic output did not include generated AWS cost records: {path}")
    return records


def parse_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        divisor = 1000 if value > 10_000_000_000 else 1
        parsed = datetime.fromtimestamp(value / divisor, tz=timezone.utc)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise SystemExit(f"Could not parse timestamp value: {text}") from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def aws_cost_arrow_schema():
    import pyarrow as pa

    fields = []
    for column in REPORT_COLUMNS:
        if column in TIMESTAMP_COLUMNS:
            fields.append(pa.field(column, pa.timestamp("us"), nullable=True))
        elif column in INTEGER_COLUMNS:
            fields.append(pa.field(column, pa.int64(), nullable=True))
        elif column in FLOAT_COLUMNS:
            fields.append(pa.field(column, pa.float64(), nullable=True))
        else:
            fields.append(pa.field(column, pa.string(), nullable=True))
    return pa.schema(fields)


def coerce_records_for_arrow(records: list[dict], arrow_schema):
    import pyarrow.types as pa_types

    coerced = []
    for record in records:
        row = {}
        for field in arrow_schema:
            value = record.get(field.name)
            if value == "":
                row[field.name] = "" if pa_types.is_string(field.type) or pa_types.is_large_string(field.type) else None
            elif value is None:
                row[field.name] = None
            elif pa_types.is_timestamp(field.type):
                row[field.name] = parse_timestamp(value)
            elif pa_types.is_integer(field.type):
                row[field.name] = int(value)
            elif pa_types.is_floating(field.type):
                row[field.name] = float(value)
            elif pa_types.is_string(field.type) or pa_types.is_large_string(field.type):
                row[field.name] = str(value)
            else:
                row[field.name] = value
        coerced.append(row)
    return coerced


def arrow_table_from_records(records: list[dict], arrow_schema):
    import pyarrow as pa

    return pa.Table.from_pylist(coerce_records_for_arrow(records, arrow_schema), schema=arrow_schema)


def load_polaris_catalog():
    from pyiceberg.catalog import load_catalog

    return load_catalog(
        "polaris",
        **{
            "type": "rest",
            "uri": polaris_url(),
            "warehouse": polaris_warehouse(),
            "credential": f"{env('POLARIS_ID')}:{env('POLARIS_SECRET')}",
            "oauth2-server-uri": polaris_token_uri(),
            "scope": polaris_scope(),
            "header.X-Iceberg-Access-Delegation": polaris_access_delegation(),
            "token-refresh-enabled": "true",
            "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
        },
    )


def load_or_create_table(catalog, arrow_schema):
    identifier = table_identifier()
    ensure_namespace()
    if catalog.table_exists(identifier):
        return catalog.load_table(identifier)
    try:
        return catalog.create_table(identifier, schema=arrow_schema)
    except Exception as error:
        if catalog.table_exists(identifier):
            return catalog.load_table(identifier)
        raise SystemExit(f"Could not create Polaris Iceberg source table {identifier}: {error}") from error


def append_events(path: Path) -> int:
    records = shadowtraffic_records(path)
    catalog = load_polaris_catalog()
    table = load_or_create_table(catalog, aws_cost_arrow_schema())
    arrow_schema = table.schema().as_arrow()
    arrow_table = arrow_table_from_records(records, arrow_schema)
    table.append(arrow_table)
    print(f"appended {arrow_table.num_rows} ShadowTraffic events into polaris.{table_identifier()}")
    return arrow_table.num_rows


def parquet_paths(path: Path) -> list[Path]:
    if path.is_dir():
        paths = sorted(path.rglob("*.parquet"))
    else:
        paths = [path]
    paths = [candidate for candidate in paths if candidate.is_file()]
    if not paths:
        raise SystemExit(f"No local Parquet files found at: {path}")
    return paths


def append_parquet(path: Path) -> int:
    import pyarrow.parquet as pq

    catalog = load_polaris_catalog()
    table = load_or_create_table(catalog, aws_cost_arrow_schema())
    arrow_schema = table.schema().as_arrow()
    total_rows = 0

    for parquet_path in parquet_paths(path):
        records = pq.read_table(parquet_path).to_pylist()
        arrow_table = arrow_table_from_records(records, arrow_schema)
        table.append(arrow_table)
        total_rows += arrow_table.num_rows
        print(f"appended {arrow_table.num_rows} rows from {parquet_path.name} into polaris.{table_identifier()}")

    print(f"appended {total_rows} total ShadowTraffic rows into polaris.{table_identifier()}")
    return total_rows


def drop_table() -> None:
    catalog = load_polaris_catalog()
    identifier = table_identifier()
    if catalog.table_exists(identifier):
        catalog.drop_table(identifier)
        print(f"dropped polaris.{identifier}")
    else:
        print(f"table not found: polaris.{identifier}")


def render_config() -> Path:
    replacements = {
        "__SHADOWTRAFFIC_MAX_EVENTS__": str(shadowtraffic_max_events()),
    }
    config = CONFIG_TEMPLATE.read_text()
    for old, new in replacements.items():
        config = config.replace(old, new)
    config = config.replace('"__AWS_CLOUD_COST_REPORT_DATA__"', json.dumps(report_data(), indent=8))

    parsed = json.loads(config)
    RENDERED_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    RENDERED_CONFIG.write_text(json.dumps(parsed, indent=2) + "\n")
    RENDERED_CONFIG.chmod(0o600)
    return RENDERED_CONFIG


def doctor() -> int:
    print("dbt_aws_cloud_cost Polaris ShadowTraffic/PyIceberg doctor")
    ok = True
    try:
        token = oauth_token()
        print("Polaris OAuth2 token: ok")
    except SystemExit as error:
        print(f"Polaris OAuth2 token: failed: {error}")
        token = ""
        ok = False

    if token:
        try:
            location = default_base_location()
            print(f"Polaris default-base-location: {urllib.parse.urlparse(location).scheme}://<redacted>")
        except SystemExit as error:
            print(f"Polaris catalog config: failed: {error}")
            ok = False

    print(f"Polaris warehouse: {polaris_warehouse()}")
    print(f"Source relation: polaris.{polaris_namespace()}.{polaris_table()}")
    print(f"Access delegation: {polaris_access_delegation()}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("render-config")
    append_parser = subparsers.add_parser("append-events")
    append_parser.add_argument("path", type=Path)
    append_parquet_parser = subparsers.add_parser("append-parquet")
    append_parquet_parser.add_argument("path", type=Path)
    subparsers.add_parser("drop-table")
    subparsers.add_parser("doctor")
    args = parser.parse_args()

    if args.command == "render-config":
        print(render_config())
    elif args.command == "append-events":
        append_events(args.path)
    elif args.command == "append-parquet":
        append_parquet(args.path)
    elif args.command == "drop-table":
        drop_table()
    elif args.command == "doctor":
        raise SystemExit(doctor())


if __name__ == "__main__":
    main()
