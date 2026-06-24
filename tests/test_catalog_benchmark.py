"""Unit tests for the raw DuckDB catalog benchmark harness.

Run: uv run tests/test_catalog_benchmark.py -v
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "catalog_benchmark.py"


def load_module():
    spec = importlib.util.spec_from_file_location("catalog_benchmark", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CatalogBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.bench = load_module()

    def test_size_matrix_supports_named_defaults_and_explicit_rows(self):
        named = self.bench.parse_size_matrix("tiny,medium", None)
        self.assertEqual(
            [(size.label, size.rows) for size in named], [("tiny", 4), ("medium", 1_000_000)]
        )

        explicit = self.bench.parse_size_matrix(None, "7,42")
        self.assertEqual(
            [(size.label, size.rows) for size in explicit],
            [("rows_7", 7), ("rows_42", 42)],
        )

    def test_attach_variants_cover_horizon_ablation_cases(self):
        variants = self.bench.ATTACH_VARIANTS
        for name in [
            "default",
            "no_stage_create",
            "no_multi_commit",
            "skip_create_metadata_updates",
            "no_cleanup_on_rollback",
            "legacy_without_stage_create",
            "legacy_full_compat",
        ]:
            with self.subTest(name=name):
                self.assertIn(name, variants)

        no_stage_options = variants["legacy_without_stage_create"].options
        self.assertEqual(no_stage_options["DISABLE_MULTI_TABLE_COMMIT"], "true")
        self.assertEqual(no_stage_options["SKIP_CREATE_TABLE_METADATA_UPDATES"], "true")
        self.assertEqual(no_stage_options["REMOVE_FILES_ON_DELETE"], "false")
        self.assertNotIn("STAGE_CREATE_TABLES", no_stage_options)
        self.assertNotIn("READ_ONLY", no_stage_options)

        legacy_options = variants["legacy_full_compat"].options
        self.assertEqual(legacy_options["STAGE_CREATE_TABLES"], "false")
        self.assertEqual(legacy_options["DISABLE_MULTI_TABLE_COMMIT"], "true")
        self.assertEqual(legacy_options["SKIP_CREATE_TABLE_METADATA_UPDATES"], "true")
        self.assertEqual(legacy_options["REMOVE_FILES_ON_DELETE"], "false")

    def test_target_missing_env_reports_only_required_names(self):
        target = self.bench.load_targets()["horizon"]
        missing = self.bench.missing_env(target, {"HORIZON_ENDPOINT": "https://example"})
        self.assertEqual(
            missing,
            [
                "HORIZON_WAREHOUSE",
                "HORIZON_ACCESS_TOKEN",
                "HORIZON_SCHEMA",
                "SNOWFLAKE_DEFAULT_REGION",
            ],
        )

    def test_horizon_sql_uses_secret_name_and_legacy_options(self):
        env = {
            "HORIZON_ENDPOINT": "https://acct.snowflakecomputing.com/polaris/api/catalog",
            "HORIZON_WAREHOUSE": "CODEX_HORIZON_DEMO",
            "HORIZON_ACCESS_TOKEN": "super-secret-token",
            "HORIZON_SCHEMA": "AWS_CLOUD_COST",
            "SNOWFLAKE_DEFAULT_REGION": "us-east-1",
        }
        target = self.bench.load_targets(env)["horizon"]

        secret_sql = self.bench.render_secret_sql(target, env)
        attach_sql = self.bench.render_attach_sql(
            target, env, self.bench.ATTACH_VARIANTS["legacy_full_compat"]
        )

        self.assertIn("CREATE OR REPLACE SECRET snowflake_oauth", secret_sql)
        self.assertIn("TOKEN 'super-secret-token'", secret_sql)
        self.assertIn("ATTACH 'CODEX_HORIZON_DEMO' AS horizon", attach_sql)
        self.assertIn("SECRET snowflake_oauth", attach_sql)
        self.assertIn("STAGE_CREATE_TABLES false", attach_sql)
        self.assertIn("DISABLE_MULTI_TABLE_COMMIT true", attach_sql)
        self.assertIn("SKIP_CREATE_TABLE_METADATA_UPDATES true", attach_sql)
        self.assertIn("REMOVE_FILES_ON_DELETE false", attach_sql)

    def test_workload_sql_varies_table_name_and_row_count(self):
        target = self.bench.load_targets()["lakekeeper_local"]
        size = self.bench.BenchmarkSize("small", 10_000)
        sql = self.bench.render_workload_sql(
            target, "legacy_full_compat", size, repetition=2, keep_tables=False
        )

        self.assertIn("bench_legacy_full_compat_small_r2", sql)
        self.assertIn("FROM range(10000)", sql)
        self.assertIn("SELECT count(*) AS row_count", sql)
        self.assertIn(
            "DROP TABLE IF EXISTS lakekeeper.default.bench_legacy_full_compat_small_r2", sql
        )

    def test_run_sql_loads_required_extensions_after_disabling_autoload(self):
        target = self.bench.load_targets()["lakekeeper_local"]
        sql, _ = self.bench.render_run_sql(
            target=target,
            env={},
            variant=self.bench.ATTACH_VARIANTS["default"],
            size=self.bench.BenchmarkSize("tiny", 4),
            repetition=1,
            output_dir=ROOT / ".tmp",
            threads=4,
            memory_limit="4GB",
            keep_tables=False,
        )

        self.assertIn(
            "SET autoload_known_extensions=false;\nLOAD iceberg;\nLOAD httpfs;",
            sql,
        )

    def test_redaction_removes_known_secret_values_and_bearer_headers(self):
        env = {
            "HORIZON_ACCESS_TOKEN": "super-secret-token",
            "POLARIS_SECRET": "polaris-secret",
            "POLARIS_ID": "client-id-is-not-secret",
        }
        text = (
            "TOKEN 'super-secret-token'\n"
            "Authorization='Basic YmFkLWJhc2lj'\n"
            "Authorization='AWS4-HMAC-SHA256 "
            "Credential=AKIA/20260624/us-east-1/s3/aws4_request, "
            "SignedHeaders=host, Signature=deadbeef'\n"
            "Authorization=Bearer abc.def\n"
            "client_secret=polaris-secret\n"
            "x-amz-security-token='bad-session-token'\n"
            "https://example.com/path?X-Amz-Credential=AKIA%2F20260624&X-Amz-Signature=deadbeef&X-Amz-Security-Token=bad-query-token\n"
            "id=client-id-is-not-secret"
        )

        redacted = self.bench.redact(text, env)

        self.assertNotIn("super-secret-token", redacted)
        self.assertNotIn("polaris-secret", redacted)
        self.assertNotIn("YmFkLWJhc2lj", redacted)
        self.assertNotIn("AWS4-HMAC-SHA256 Credential=AKIA", redacted)
        self.assertNotIn("Bearer abc.def", redacted)
        self.assertNotIn("bad-session-token", redacted)
        self.assertNotIn("deadbeef", redacted)
        self.assertNotIn("bad-query-token", redacted)
        self.assertIn("client-id-is-not-secret", redacted)

    def test_summary_error_redacts_http_debug_secrets(self):
        output = (
            "noise before failure\n"
            "{'request': {'headers': {Authorization='AWS4-HMAC-SHA256 "
            "Credential=AKIA/20260624/us-east-1/s3/aws4_request, "
            "SignedHeaders=host, Signature=deadbeef', "
            "x-amz-security-token='bad-session-token "
            "TransactionContext Error: Failed to commit\n"
        )

        error = self.bench.redacted_error(output, {})

        self.assertNotIn("AKIA", error)
        self.assertNotIn("deadbeef", error)
        self.assertNotIn("bad-session-token", error)
        self.assertNotIn("AWS4-HMAC-SHA256 Credential", error)


if __name__ == "__main__":
    unittest.main()
