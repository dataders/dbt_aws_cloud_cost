#!/usr/bin/env python3
"""Run raw DuckDB Iceberg REST catalog benchmarks.

The measured work is executed by the DuckDB CLI. Python only expands target
configuration, writes SQL, invokes DuckDB, redacts artifacts, and summarizes
DuckDB timer/HTTP-log output.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET_CONFIG = ROOT / "benchmarks" / "catalog_benchmarks.toml"
RUN_TEMPLATE = ROOT / "benchmarks" / "sql" / "run.sql"
OUTPUT_ROOT = ROOT / ".tmp" / "catalog_benchmarks"

DEFAULT_SIZES = {
    "tiny": 4,
    "small": 10_000,
    "medium": 1_000_000,
    "large": 10_000_000,
}

SECRET_ENV_PATTERNS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "ACCESS_KEY",
    "SESSION",
)


@dataclass(frozen=True)
class BenchmarkSize:
    label: str
    rows: int


@dataclass(frozen=True)
class AttachVariant:
    name: str
    options: dict[str, str]


@dataclass(frozen=True)
class CatalogTarget:
    name: str
    description: str
    attach_as: str
    warehouse: str
    endpoint: str
    default_schema: str
    authorization_type: str | None
    access_delegation_mode: str | None
    default_region: str | None
    create_schema: bool
    required_env: list[str]
    token_secret: dict[str, str]
    oauth_secret: dict[str, str]
    s3_secret: dict[str, str]


ATTACH_VARIANTS = {
    "default": AttachVariant("default", {}),
    "no_stage_create": AttachVariant("no_stage_create", {"STAGE_CREATE_TABLES": "false"}),
    "no_multi_commit": AttachVariant("no_multi_commit", {"DISABLE_MULTI_TABLE_COMMIT": "true"}),
    "skip_create_metadata_updates": AttachVariant(
        "skip_create_metadata_updates",
        {"STAGE_CREATE_TABLES": "false", "SKIP_CREATE_TABLE_METADATA_UPDATES": "true"},
    ),
    "no_cleanup_on_rollback": AttachVariant("no_cleanup_on_rollback", {"REMOVE_FILES_ON_DELETE": "false"}),
    "legacy_full_compat": AttachVariant(
        "legacy_full_compat",
        {
            "STAGE_CREATE_TABLES": "false",
            "DISABLE_MULTI_TABLE_COMMIT": "true",
            "SKIP_CREATE_TABLE_METADATA_UPDATES": "true",
            "REMOVE_FILES_ON_DELETE": "false",
            "READ_ONLY": "false",
        },
    ),
}


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def merged_env() -> dict[str, str]:
    env = load_dotenv(ROOT / ".env")
    env.update(os.environ)
    return env


def resolve_template(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_template(nested, env) for key, nested in value.items()}
    if isinstance(value, list):
        return [resolve_template(nested, env) for nested in value]
    if not isinstance(value, str):
        return value

    pattern = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in env and env[key] != "":
            return env[key]
        if default is not None:
            return resolve_template(default, env)
        return ""

    return pattern.sub(replace, value)


def load_targets(env: dict[str, str] | None = None) -> dict[str, CatalogTarget]:
    env = env or merged_env()
    raw = tomllib.loads(TARGET_CONFIG.read_text())["targets"]
    targets = {}
    for name, values in raw.items():
        resolved = {key: resolve_template(value, env) for key, value in values.items()}
        targets[name] = CatalogTarget(
            name=name,
            description=str(resolved.get("description", "")),
            attach_as=str(resolved["attach_as"]),
            warehouse=str(resolved["warehouse"]),
            endpoint=str(resolved["endpoint"]),
            default_schema=str(resolved["default_schema"]),
            authorization_type=resolved.get("authorization_type"),
            access_delegation_mode=resolved.get("access_delegation_mode"),
            default_region=resolved.get("default_region"),
            create_schema=bool(resolved.get("create_schema", False)),
            required_env=list(values.get("required_env", [])),
            token_secret=dict(resolved.get("token_secret", {})),
            oauth_secret=dict(resolved.get("oauth_secret", {})),
            s3_secret=dict(resolved.get("s3_secret", {})),
        )
        if targets[name].oauth_secret and not targets[name].oauth_secret.get("oauth2_server_uri"):
            secret = dict(targets[name].oauth_secret)
            secret["oauth2_server_uri"] = targets[name].endpoint.rstrip("/") + "/v1/oauth/tokens"
            targets[name] = CatalogTarget(
                name=targets[name].name,
                description=targets[name].description,
                attach_as=targets[name].attach_as,
                warehouse=targets[name].warehouse,
                endpoint=targets[name].endpoint,
                default_schema=targets[name].default_schema,
                authorization_type=targets[name].authorization_type,
                access_delegation_mode=targets[name].access_delegation_mode,
                default_region=targets[name].default_region,
                create_schema=targets[name].create_schema,
                required_env=targets[name].required_env,
                token_secret=targets[name].token_secret,
                oauth_secret=secret,
                s3_secret=targets[name].s3_secret,
            )
    return targets


def missing_env(target: CatalogTarget, env: dict[str, str]) -> list[str]:
    return [name for name in target.required_env if not env.get(name)]


def parse_size_matrix(sizes: str | None, rows: str | None) -> list[BenchmarkSize]:
    if rows:
        parsed = []
        for raw_row_count in rows.split(","):
            raw_row_count = raw_row_count.strip()
            if not raw_row_count:
                continue
            row_count = int(raw_row_count.replace("_", ""))
            if row_count <= 0:
                raise ValueError(f"row count must be positive: {raw_row_count}")
            parsed.append(BenchmarkSize(f"rows_{row_count}", row_count))
        if parsed:
            return parsed

    selected = sizes.split(",") if sizes else ["tiny", "small"]
    parsed = []
    for raw_name in selected:
        name = raw_name.strip()
        if not name:
            continue
        if name not in DEFAULT_SIZES:
            valid = ", ".join(DEFAULT_SIZES)
            raise ValueError(f"unknown size {name!r}; valid sizes: {valid}")
        parsed.append(BenchmarkSize(name, DEFAULT_SIZES[name]))
    return parsed


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def option_sql(name: str, value: str | bool) -> str:
    if isinstance(value, bool):
        return f"  {name} {'true' if value else 'false'}"
    if name in {"TYPE", "SECRET"}:
        return f"  {name} {value}"
    if value in {"true", "false"}:
        return f"  {name} {value}"
    return f"  {name} {sql_literal(value)}"


def render_secret_sql(target: CatalogTarget, env: dict[str, str]) -> str:
    statements = []
    if target.s3_secret:
        secret = target.s3_secret
        statements.append(
            "CREATE OR REPLACE SECRET {name} (TYPE S3, KEY_ID {key_id}, SECRET {secret}, "
            "ENDPOINT {endpoint}, URL_STYLE {url_style}, USE_SSL {use_ssl});".format(
                name=secret["name"],
                key_id=sql_literal(secret["key_id"]),
                secret=sql_literal(secret["secret"]),
                endpoint=sql_literal(secret["endpoint"]),
                url_style=sql_literal(secret.get("url_style", "path")),
                use_ssl="true" if secret.get("use_ssl") else "false",
            )
        )
    if target.token_secret:
        secret = target.token_secret
        statements.append(
            "CREATE OR REPLACE SECRET {name} (TYPE ICEBERG, TOKEN {token});".format(
                name=secret["name"],
                token=sql_literal(secret["token"]),
            )
        )
    if target.oauth_secret:
        secret = target.oauth_secret
        statements.append(
            "CREATE OR REPLACE SECRET {name} (TYPE ICEBERG, CLIENT_ID {client_id}, CLIENT_SECRET {client_secret}, "
            "OAUTH2_SERVER_URI {oauth2_server_uri}, OAUTH2_SCOPE {oauth2_scope}, "
            "OAUTH2_GRANT_TYPE {oauth2_grant_type});".format(
                name=secret["name"],
                client_id=sql_literal(secret["client_id"]),
                client_secret=sql_literal(secret["client_secret"]),
                oauth2_server_uri=sql_literal(secret["oauth2_server_uri"]),
                oauth2_scope=sql_literal(secret.get("oauth2_scope", "PRINCIPAL_ROLE:ALL")),
                oauth2_grant_type=sql_literal(secret.get("oauth2_grant_type", "client_credentials")),
            )
        )
    if not statements:
        return "SELECT 'no secrets required' AS benchmark_secret_status;"
    return "\n".join(statements)


def secret_name(target: CatalogTarget) -> str | None:
    if target.token_secret:
        return target.token_secret["name"]
    if target.oauth_secret:
        return target.oauth_secret["name"]
    return None


def render_attach_sql(target: CatalogTarget, env: dict[str, str], variant: AttachVariant) -> str:
    options: list[tuple[str, str | bool]] = [
        ("TYPE", "ICEBERG"),
        ("ENDPOINT", target.endpoint),
    ]
    target_secret = secret_name(target)
    if target_secret:
        options.append(("SECRET", target_secret))
    if target.authorization_type:
        options.append(("AUTHORIZATION_TYPE", target.authorization_type))
    if target.access_delegation_mode:
        options.append(("ACCESS_DELEGATION_MODE", target.access_delegation_mode))
    if target.default_region:
        options.append(("DEFAULT_REGION", target.default_region))
    if target.default_schema:
        options.append(("DEFAULT_SCHEMA", target.default_schema))
    options.extend(variant.options.items())

    rendered_options = ",\n".join(option_sql(name, value) for name, value in options)
    return f"ATTACH {sql_literal(target.warehouse)} AS {target.attach_as} (\n{rendered_options}\n);"


def relation_name(target: CatalogTarget, variant_name: str, size: BenchmarkSize, repetition: int) -> str:
    table = f"bench_{variant_name}_{size.label}_r{repetition}".lower()
    table = re.sub(r"[^a-z0-9_]", "_", table)
    return f"{target.attach_as}.{target.default_schema}.{table}"


def render_workload_sql(target: CatalogTarget, variant_name: str, size: BenchmarkSize, repetition: int, keep_tables: bool) -> str:
    relation = relation_name(target, variant_name, size, repetition)
    lines = []
    if target.create_schema:
        lines.extend(
            [
                ".print >>> PHASE: create_schema",
                f"CREATE SCHEMA IF NOT EXISTS {target.attach_as}.{target.default_schema};",
                "",
            ]
        )
    lines.extend(
        [
            f".print >>> PHASE: create_table {size.label} rep {repetition}",
            f"CREATE TABLE {relation} (id BIGINT, h BIGINT, label VARCHAR);",
            "",
            f".print >>> PHASE: insert {size.label} rep {repetition}",
            f"INSERT INTO {relation}",
            "SELECT",
            "  i AS id,",
            "  (i * 2654435761 % 1000000007)::BIGINT AS h,",
            "  ('payload ' || i)::VARCHAR AS label",
            f"FROM range({size.rows}) AS t(i);",
            "",
            f".print >>> PHASE: readback {size.label} rep {repetition}",
            "SELECT count(*) AS row_count, sum(id) AS id_sum,",
            "       count(*) FILTER (WHERE id % 10 = 0) AS decile_rows",
            f"FROM {relation};",
            "",
        ]
    )
    if not keep_tables:
        lines.extend(
            [
                f".print >>> PHASE: cleanup {size.label} rep {repetition}",
                f"DROP TABLE IF EXISTS {relation};",
                "",
            ]
        )
    return "\n".join(lines)


def render_run_sql(
    target: CatalogTarget,
    env: dict[str, str],
    variant: AttachVariant,
    size: BenchmarkSize,
    repetition: int,
    output_dir: Path,
    threads: int,
    memory_limit: str,
    keep_tables: bool,
    profile: bool = False,
) -> tuple[str, Path]:
    http_log_path = output_dir / f"http_{target.name}_{variant.name}_{size.label}_r{repetition}.csv"
    profile_sql = ""
    if profile:
        profile_path = output_dir / f"profile_{target.name}_{variant.name}_{size.label}_r{repetition}.json"
        profile_sql = "\n".join(
            [
                "PRAGMA enable_profiling='json';",
                f"PRAGMA profiling_output={sql_literal(str(profile_path))};",
            ]
        )
    sql = RUN_TEMPLATE.read_text().format(
        threads=threads,
        memory_limit=memory_limit,
        profile_sql=profile_sql,
        secret_sql=render_secret_sql(target, env),
        attach_sql=render_attach_sql(target, env, variant),
        workload_sql=render_workload_sql(target, variant.name, size, repetition, keep_tables),
        http_log_path=http_log_path,
    )
    return sql, http_log_path


def redact(text: str, env: dict[str, str]) -> str:
    redacted = text
    for key, value in env.items():
        if not value or len(value) < 4:
            continue
        if any(pattern in key for pattern in SECRET_ENV_PATTERNS):
            redacted = redacted.replace(value, f"<redacted:{key}>")
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", redacted)
    redacted = re.sub(r"(client_secret=)[^&\s']+", r"\1<redacted>", redacted)
    return redacted


def duckdb_cli(env: dict[str, str]) -> Path:
    candidate = env.get("DUCKDB_CLI")
    if not candidate and env.get("DUCKDB_BUILD_DIR"):
        candidate = str(Path(env["DUCKDB_BUILD_DIR"]) / "build" / "debug" / "duckdb")
    if not candidate:
        raise SystemExit("Set DUCKDB_CLI or DUCKDB_BUILD_DIR before running benchmarks.")
    path = Path(candidate)
    if not path.exists():
        raise SystemExit(f"DuckDB CLI does not exist: {path}")
    return path


def parse_timer_output(output: str) -> dict[str, float]:
    current_phase = "startup"
    timings: dict[str, float] = {}
    for line in output.splitlines():
        if line.startswith(">>> PHASE: "):
            current_phase = line.removeprefix(">>> PHASE: ").strip()
            continue
        match = re.search(r"Run Time \(s\): real\s+([0-9.]+)", line)
        if match:
            timings[current_phase] = timings.get(current_phase, 0.0) + float(match.group(1))
    return timings


def classify_url(url: str) -> str:
    clean = url.split("?", 1)[0]
    if "/v1/config" in clean or clean.endswith("/config"):
        return "rest_config"
    if "/namespaces" in clean:
        return "rest_namespace"
    if "/transactions/commit" in clean or "/tables/" in clean:
        return "rest_table_commit_or_load"
    if clean.endswith("/tables"):
        return "rest_create_table"
    if "/data/" in clean or clean.endswith(".parquet"):
        return "object_data"
    if "/metadata/" in clean or clean.endswith((".avro", ".metadata.json")):
        return "object_metadata"
    return "other"


def summarize_http_log(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {"http_request_count": 0, "http_duration_ms": 0, "http_groups": {}}
    text = path.read_text(errors="replace")
    groups: dict[str, dict[str, float | int]] = {}
    total = 0
    count = 0
    for match in re.finditer(r"'url': '([^']+)'.*?'duration_ms': ([0-9]+)", text):
        url = match.group(1)
        duration = int(match.group(2))
        group = classify_url(url)
        bucket = groups.setdefault(group, {"count": 0, "duration_ms": 0})
        bucket["count"] = int(bucket["count"]) + 1
        bucket["duration_ms"] = float(bucket["duration_ms"]) + duration
        total += duration
        count += 1
    return {"http_request_count": count, "http_duration_ms": total, "http_groups": groups}


def run_duckdb(duckdb: Path, sql_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(duckdb), ":memory:"],
        input=sql_path.read_text(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, **env},
        check=False,
    )


def write_redacted(path: Path, text: str, env: dict[str, str]) -> None:
    path.write_text(redact(text, env))


def csv_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened = []
    for row in rows:
        base = {key: value for key, value in row.items() if key not in {"timings", "http_groups"}}
        base["http_groups"] = json.dumps(row.get("http_groups", {}), sort_keys=True)
        timings = row.get("timings") or {"": ""}
        for phase, seconds in timings.items():
            flattened.append({**base, "phase": phase, "duckdb_wall_seconds": seconds})
    return flattened


def write_summary(rows: list[dict[str, Any]], output_dir: Path) -> None:
    json_path = output_dir / "summary.json"
    csv_path = output_dir / "summary.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True))
    csv_rows = csv_summary_rows(rows)
    if not csv_rows:
        csv_path.write_text("")
        return
    fieldnames = sorted({key for row in csv_rows for key in row})
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)


def run_one(
    target: CatalogTarget,
    env: dict[str, str],
    variant: AttachVariant,
    size: BenchmarkSize,
    repetition: int,
    output_dir: Path,
    threads: int,
    memory_limit: str,
    keep_tables: bool,
    profile: bool = False,
) -> dict[str, Any]:
    duckdb = duckdb_cli(env)
    sql, http_log_path = render_run_sql(
        target, env, variant, size, repetition, output_dir, threads, memory_limit, keep_tables, profile
    )
    stem = f"{target.name}_{variant.name}_{size.label}_r{repetition}"
    sql_path = output_dir / f"{stem}.sql"
    output_path = output_dir / f"{stem}.out"
    write_redacted(sql_path, sql, env)

    raw_sql_path = output_dir / f"{stem}.raw.sql"
    raw_sql_path.write_text(sql)
    result = run_duckdb(duckdb, raw_sql_path, env)
    raw_sql_path.unlink(missing_ok=True)
    write_redacted(output_path, result.stdout, env)
    if http_log_path.exists():
        write_redacted(http_log_path, http_log_path.read_text(errors="replace"), env)

    http_summary = summarize_http_log(http_log_path)
    timings = parse_timer_output(result.stdout)
    return {
        "target": target.name,
        "variant": variant.name,
        "size": size.label,
        "rows": size.rows,
        "repetition": repetition,
        "passed": result.returncode == 0,
        "exit_code": result.returncode,
        "error": "" if result.returncode == 0 else first_error(result.stdout),
        "timings": timings,
        **http_summary,
    }


def first_error(output: str) -> str:
    for line in output.splitlines():
        if "Error:" in line or "Exception:" in line or "Catalog Error" in line:
            return line.strip()
    return output.splitlines()[-1].strip() if output.splitlines() else "unknown error"


def choose_minimal_passing(rows: list[dict[str, Any]]) -> AttachVariant | None:
    passing = [row["variant"] for row in rows if row.get("passed")]
    if not passing:
        return None
    names = sorted(set(passing), key=lambda name: (len(ATTACH_VARIANTS[name].options), name))
    variant = ATTACH_VARIANTS[names[0]]
    return AttachVariant("minimal_passing", dict(variant.options))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=False, help="Target name from benchmarks/catalog_benchmarks.toml")
    parser.add_argument("--list-targets", action="store_true", help="List configured targets and exit")
    parser.add_argument("--sizes", default="tiny,small", help="Comma-separated named sizes")
    parser.add_argument("--rows", help="Comma-separated explicit row counts; overrides --sizes")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--keep-tables", action="store_true")
    parser.add_argument("--compat-only", action="store_true", help="Only run attach-option ablation variants")
    parser.add_argument("--profile", action="store_true", help="Enable DuckDB JSON profiling for the run")
    parser.add_argument("--variants", help="Comma-separated variant names; defaults to the ablation suite")
    parser.add_argument("--run-id", help="Stable output directory suffix")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = merged_env()
    targets = load_targets(env)

    if args.list_targets:
        for target in targets.values():
            print(f"{target.name}: {target.description}")
        return 0
    if not args.target:
        raise SystemExit("--target is required unless --list-targets is used")
    if args.target not in targets:
        valid = ", ".join(sorted(targets))
        raise SystemExit(f"unknown target {args.target!r}; valid targets: {valid}")

    target = targets[args.target]
    missing = missing_env(target, env)
    if missing:
        raise SystemExit(f"missing required env vars for {target.name}: {', '.join(missing)}")

    variant_names = [name.strip() for name in args.variants.split(",")] if args.variants else list(ATTACH_VARIANTS)
    variants = []
    for name in variant_names:
        if name not in ATTACH_VARIANTS:
            raise SystemExit(f"unknown variant {name!r}; valid variants: {', '.join(ATTACH_VARIANTS)}")
        variants.append(ATTACH_VARIANTS[name])

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%S")
    output_dir = OUTPUT_ROOT / run_id / target.name
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    tiny = BenchmarkSize("tiny", DEFAULT_SIZES["tiny"])
    for variant in variants:
        rows.append(
            run_one(
                target,
                env,
                variant,
                tiny,
                1,
                output_dir,
                args.threads,
                args.memory_limit,
                args.keep_tables,
                args.profile,
            )
        )

    minimal = choose_minimal_passing(rows)
    if minimal is not None and not args.compat_only:
        for size in parse_size_matrix(args.sizes, args.rows):
            for repetition in range(1, args.repetitions + 1):
                rows.append(
                    run_one(
                        target,
                        env,
                        minimal,
                        size,
                        repetition,
                        output_dir,
                        args.threads,
                        args.memory_limit,
                        args.keep_tables,
                        args.profile,
                    )
                )

    write_summary(rows, output_dir)
    print(output_dir)
    compat_names = set(variant_names)
    compat_passed = any(row["passed"] for row in rows if row["variant"] in compat_names)
    benchmark_passed = all(row["passed"] for row in rows if row["variant"] == "minimal_passing")
    return 0 if compat_passed and benchmark_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
