from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class DemoConfigurationTest(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text()

    def test_plan_artifacts_are_present(self):
        for relative_path in [
            "profiles.yml",
            "catalogs.yml",
            "docker-compose.yml",
            ".envrc",
            "local_files/aws_cost_report.csv",
        ]:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())

    def test_local_file_source_is_static_seed_copy(self):
        self.assertEqual(
            self.read("integration_tests/seeds/aws_cost_report.csv"),
            self.read("local_files/aws_cost_report.csv"),
        )

    def test_project_uses_catalogs_v2_with_static_lakekeeper_output(self):
        project = self.read("dbt_project.yml")

        self.assertIn("profile: aws_cloud_cost", project)
        self.assertIn("use_catalogs_v2: true", project)
        self.assertNotIn("env_var('CATALOG'", project)
        self.assertIn("+schema: aws_cloud_cost", project)
        self.assertIn("+catalog: lakekeeper", project)
        self.assertIn("+catalog: builtin", project)
        self.assertIn("staging:\n      +materialized: table", project)
        self.assertNotIn("+database:", project)
        self.assertNotIn("+catalog_name:", project)
        self.assertNotIn("aws_cloud_cost_sources", project)
        self.assertNotIn("aws_cloud_cost_models_catalog", project)
        self.assertNotIn("aws_cloud_cost_source_catalog", project)

    def test_setup_env_wires_local_fusion_and_duckdb_builds(self):
        setup = self.read("scripts/setup_env.sh")
        envrc = self.read(".envrc")

        self.assertIn(
            "/Users/dataders/Developer/fs.codex-duckdb-catalog-stack-combined/target/debug/dbt",
            setup,
        )
        self.assertIn(
            "/Users/dataders/Developer/duckdb-iceberg.codex-catalog-write-compat-stack",
            setup,
        )
        self.assertIn("[ -x \"$FS_DBT_BIN\" ]", setup)
        self.assertIn("[ -x \"$DUCKDB_CLI\" ]", setup)
        self.assertIn("ln -sfn \"$DUCKDB_DRIVER_LIB\" \"$ADBC_REPOSITORY/duckdb\"", setup)
        self.assertIn("DISABLE_CDN_DRIVER_CACHE=${DISABLE_CDN_DRIVER_CACHE:-true}", setup)
        self.assertIn("DISABLE_AUTO_DRIVER_REBUILD=${DISABLE_AUTO_DRIVER_REBUILD:-true}", setup)
        self.assertIn("require_duckdb_extension httpfs", setup)
        self.assertIn("require_duckdb_extension iceberg", setup)
        self.assertIn("bootstrap_demo_schemas", setup)
        self.assertIn("create schema if not exists aws_cloud_cost", setup)
        self.assertIn("create schema if not exists lakekeeper.aws_cloud_cost", setup)
        self.assertIn("http://localhost:18181/catalog/v1/config?warehouse=demo", setup)
        self.assertIn("printf 'ADBC_REPOSITORY=%s\\n'", setup)
        self.assertNotIn("printf 'HORIZON_ACCESS_TOKEN=", setup)
        self.assertNotIn("HORIZON_ACCESS_TOKEN_EXPIRES_AT", setup)

        self.assertIn("source_env_if_exists .env", envrc)
        self.assertIn("PATH_add \"$(dirname \"$DBT_BIN\")\"", envrc)
        self.assertIn("PATH_add \"$(dirname \"$DUCKDB_CLI\")\"", envrc)

    def test_packages_use_dbt_utils_without_fivetran_utils(self):
        packages = self.read("packages.yml")
        lock = self.read("package-lock.yml")

        self.assertIn("package: dbt-labs/dbt_utils", packages)
        self.assertNotIn("fivetran/fivetran_utils", packages)
        self.assertNotIn("fivetran_utils", lock)

    def test_profiles_and_catalogs_match_duckdb_multi_catalog_demo(self):
        profile = self.read("profiles.yml")
        catalogs = self.read("catalogs.yml")

        self.assertIn("aws_cloud_cost:", profile)
        self.assertIn("type: duckdb", profile)
        self.assertIn("path: \".tmp/aws_cloud_cost.duckdb\"", profile)
        self.assertIn("schema: aws_cloud_cost", profile)
        self.assertNotIn("\n      extensions:", profile)
        self.assertNotIn("\n        - httpfs", profile)
        self.assertNotIn("\n        - iceberg", profile)
        self.assertIn("allow_unsigned_extensions: true", profile)
        self.assertIn("name: snowflake_oauth", profile)
        self.assertIn("client_id: \"\"", profile)
        self.assertIn("client_secret: \"{{ env_var('HORIZON_PAT'", profile)
        self.assertIn("HORIZON_PAT", profile)
        self.assertIn("oauth2_grant_type: \"client_credentials\"", profile)
        self.assertNotIn("token: \"{{ env_var('SNOWFLAKE_ACCESS_TOKEN'", profile)
        self.assertIn("name: databricks_token", profile)
        self.assertIn("name: minio_secret", profile)
        self.assertIn("name: polaris_oauth", profile)
        self.assertIn("endpoint: \"localhost:19000\"", profile)

        for catalog_name in ["local_files", "polaris", "ducklake", "lakekeeper", "horizon", "unity"]:
            with self.subTest(catalog_name=catalog_name):
                self.assertIn(f"\n  - name: {catalog_name}", catalogs)

        self.assertIn("type: local_filesystem", catalogs)
        self.assertIn("root_path: \"./local_files\"", catalogs)
        self.assertIn("file_format: csv", catalogs)
        self.assertIn("secret: polaris_oauth", catalogs)
        self.assertIn("env_var('POLARIS_URL'", catalogs)
        self.assertIn("env_var('POLARIS_WAREHOUSE'", catalogs)
        self.assertIn("env_var('POLARIS_DEFAULT_REGION', 'us-east-1')", catalogs)
        self.assertIn("AWS_CLOUD_COST_SOURCE_SCHEMA", catalogs)
        self.assertIn("type: ducklake", catalogs)
        self.assertIn("metadata_path: \"./.tmp/ducklake.db\"", catalogs)
        self.assertIn("create_if_not_exists: true", catalogs)
        self.assertIn("type: iceberg_rest", catalogs)
        self.assertIn("endpoint: \"http://localhost:18181/catalog\"", catalogs)
        self.assertIn("type: horizon", catalogs)
        self.assertIn("type: unity", catalogs)

    def test_source_reads_report_from_polaris_catalog_by_default(self):
        source = self.read("models/staging/src_aws_cloud_cost.yml")

        self.assertIn("env_var('AWS_CLOUD_COST_SOURCE_CATALOG', 'polaris')", source)
        self.assertIn("env_var('AWS_CLOUD_COST_SOURCE_SCHEMA', 'aws_cloud_cost')", source)
        self.assertIn("env_var('AWS_CLOUD_COST_SOURCE_TABLE', 'aws_cost_report')", source)
        self.assertNotIn("{% if", source)
        self.assertNotIn("AWS_CLOUD_COST_SOURCE_EXTERNAL_LOCATION", source)
        self.assertNotIn("aws_cloud_cost_sources", source)
        self.assertNotIn("aws_cloud_cost_source_catalog", source)

    def test_staging_uses_single_source_without_fivetran_union_macros(self):
        base = self.read("models/staging/base/stg_aws_cloud_cost__report_base.sql")
        staging = self.read("models/staging/stg_aws_cloud_cost__report.sql")
        columns = self.read("macros/get_aws_cloud_cost_report_columns.sql")

        self.assertIn("select * from {{ source('aws_cloud_cost', 'report') }}", base)
        self.assertNotIn("union_aws_cost_report_connections", base)
        self.assertNotIn("fivetran_utils", staging)
        self.assertNotIn("aws_cloud_cost_source_relation", staging)
        self.assertNotIn("fill_pass_through_columns", staging)
        self.assertIn("source_relation", staging)
        self.assertIn("env_var('AWS_CLOUD_COST_SOURCE_CATALOG', 'polaris')", staging)
        self.assertIn("env_var('AWS_CLOUD_COST_SOURCE_TABLE', 'aws_cost_report')", staging)
        self.assertIn("regexp_extract(product, '\"product_name\":\"([^\"]+)\"', 1)", staging)
        self.assertIn("coalesce(line_item_usage_start_date, bill_billing_period_start_date)", staging)
        self.assertNotIn("fivetran_utils", columns)
        self.assertNotIn("add_pass_through_columns", columns)
        self.assertIn('"name": "product"', columns)

    def test_union_macros_are_removed(self):
        for relative_path in [
            "macros/union/aws_cloud_cost_union_relations.sql",
            "macros/union/union_aws_cost_report_connections.sql",
            "macros/union/aws_cloud_cost_source_relation.sql",
        ]:
            with self.subTest(relative_path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())

    def test_readme_documents_source_generation_and_catalog_verification(self):
        readme = self.read("README.md")

        for snippet in [
            "does not ship a dbt wrapper script",
            "AWS_CLOUD_COST_SOURCE_CATALOG",
            "final models are hardcoded to the `lakekeeper` catalog",
            "scripts/start.sh --sample",
            "--batch-size 10000",
            "scripts/stop.sh --drop-table",
            "pyiceberg[s3fs]",
            "POLARIS_URL",
            "SNOWFLAKE_CATALOG_URI",
            "scripts/doctor.sh",
            "scripts/create_horizon_pat.sh",
            "scripts/configure_horizon_schema.sh",
            "DATABRICKS_HOST",
            "aws_cloud_cost__daily_overview",
        ]:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, readme)

        self.assertNotIn("scripts/build.sh", readme)
        self.assertNotIn("scripts/dbt", readme)

    def test_docker_compose_contains_lakekeeper_stack(self):
        compose = self.read("docker-compose.yml")

        self.assertIn("lakekeeper:", compose)
        self.assertIn("minio:", compose)
        self.assertIn("postgres:17", compose)
        self.assertIn("18181:8181", compose)
        self.assertIn("19000:9000", compose)
        self.assertIn("19001:9001", compose)

    def test_rendered_builtin_workspace_scopes_eager_duckdb_catalogs(self):
        import subprocess
        import sys

        workspace = ROOT / ".tmp" / "test-render-builtin"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "render_demo_workspace.py"),
                "--workspace",
                str(workspace),
                "--include-catalog",
                "local_files",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout.strip(), str(workspace.resolve()))
        catalogs = (workspace / "catalogs.yml").read_text()
        profile = (workspace / "profiles.yml").read_text()

        self.assertIn("name: local_files", catalogs)
        self.assertNotIn("name: lakekeeper", catalogs)
        self.assertNotIn("name: horizon", catalogs)
        self.assertNotIn("name: unity", catalogs)
        self.assertNotIn("name: ducklake", catalogs)
        self.assertNotIn("snowflake_oauth", profile)
        self.assertNotIn("databricks_token", profile)
        self.assertTrue((workspace / "local_files").exists())

    def test_rendered_lakekeeper_workspace_includes_minio_secret(self):
        import subprocess
        import sys

        workspace = ROOT / ".tmp" / "test-render-lakekeeper"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "render_demo_workspace.py"),
                "--workspace",
                str(workspace),
                "--include-catalog",
                "local_files",
                "--include-catalog",
                "lakekeeper",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        profile = (workspace / "profiles.yml").read_text()
        self.assertIn("name: minio_secret", profile)
        self.assertIn("endpoint: \"localhost:19000\"", profile)
        self.assertNotIn("snowflake_oauth", profile)
        self.assertNotIn("databricks_token", profile)

    def test_rendered_polaris_workspace_includes_polaris_secret(self):
        import subprocess
        import sys

        workspace = ROOT / ".tmp" / "test-render-polaris"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "render_demo_workspace.py"),
                "--workspace",
                str(workspace),
                "--include-catalog",
                "polaris",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        catalogs = (workspace / "catalogs.yml").read_text()
        profile = (workspace / "profiles.yml").read_text()
        self.assertIn("name: polaris", catalogs)
        self.assertIn("name: polaris_oauth", profile)
        self.assertIn("POLARIS_ID", profile)
        self.assertIn("POLARIS_SECRET", profile)
        self.assertIn("POLARIS_OAUTH_SCOPE", profile)
        self.assertIn("PRINCIPAL_ROLE:ALL", profile)

    def test_rendered_horizon_workspace_prefers_pat_over_cached_access_token(self):
        import subprocess
        import sys

        workspace = ROOT / ".tmp" / "test-render-horizon"
        env = os.environ.copy()
        env["HORIZON_PAT"] = "fake-pat"
        env["HORIZON_ACCESS_TOKEN"] = "stale-token"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "render_demo_workspace.py"),
                "--workspace",
                str(workspace),
                "--include-catalog",
                "horizon",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        profile = (workspace / "profiles.yml").read_text()
        self.assertIn("name: snowflake_oauth", profile)
        self.assertIn("client_id: \"\"", profile)
        self.assertIn("client_secret: \"{{ env_var('HORIZON_PAT'", profile)
        self.assertIn("HORIZON_PAT", profile)
        self.assertNotIn("token: \"{{ env_var('SNOWFLAKE_ACCESS_TOKEN'", profile)

    def test_repo_does_not_ship_dbt_invocation_scripts(self):
        self.assertFalse((ROOT / "scripts" / "build.sh").exists())
        self.assertFalse((ROOT / "scripts" / "dbt").exists())

    def test_shadowtraffic_writes_iceberg_to_polaris(self):
        config = self.read("shadowtraffic/config.json")
        helper = self.read("scripts/polaris_shadowtraffic.py")
        start = self.read("scripts/start.sh")
        stop = self.read("scripts/stop.sh")

        self.assertIn('"connection": "local_files"', config)
        self.assertIn('"kind": "fileSystem"', config)
        self.assertIn('"directory": "/home/output"', config)
        self.assertIn('"format": "parquet"', config)
        self.assertIn('"batchElements": __SHADOWTRAFFIC_MAX_EVENTS__', config)
        self.assertIn('"data": "__AWS_CLOUD_COST_REPORT_DATA__"', config)
        self.assertIn('"__AWS_CLOUD_COST_REPORT_DATA__"', config)
        self.assertNotIn("__POLARIS_CREDENTIAL__", config)

        self.assertIn("load_polaris_catalog", helper)
        self.assertIn("append_events", helper)
        self.assertIn("append_parquet", helper)
        self.assertIn("drop_table", helper)
        self.assertIn("aws_cost_arrow_schema", helper)
        self.assertIn("pyiceberg.io.fsspec.FsspecFileIO", helper)
        self.assertIn("header.X-Iceberg-Access-Delegation", helper)
        self.assertIn("namespace_exists", helper)
        self.assertIn("POLARIS_OAUTH_SCOPE", helper)
        self.assertIn("PRINCIPAL_ROLE:ALL", helper)
        self.assertIn("AWS_CLOUD_COST_SOURCE_SCHEMA", helper)
        self.assertIn("AWS_CLOUD_COST_SOURCE_TABLE", helper)

        self.assertIn("scripts/polaris_shadowtraffic.py", start)
        self.assertIn("--sample", start)
        self.assertIn("--batch-size", start)
        self.assertIn("SHADOWTRAFFIC_SAMPLE_BATCH_SIZE", start)
        self.assertIn("shadowtraffic-parquet", start)
        self.assertIn("/home/output", start)
        self.assertIn("docker cp", start)
        self.assertIn("append-parquet", start)
        self.assertIn("pyiceberg[s3fs]", start)
        self.assertIn("redact_file", start)
        self.assertIn("ShadowTraffic local Parquet generation failed", start)
        self.assertIn("load_dotenv\nload_polaris_env", start)
        self.assertIn("load_dotenv\nload_polaris_env", stop)
        self.assertIn("POLARIS_*)\n        if [ -z \"${!key+x}\" ]; then", start)
        self.assertIn("POLARIS_*)\n        if [ -z \"${!key+x}\" ]; then", stop)
        self.assertNotIn("--stdout", start)
        self.assertNotIn("$HOME/.aws:/root/.aws:ro", start)
        self.assertNotIn("snowflake_sql_api.py", start)
        self.assertIn("--drop-table", stop)
        self.assertIn("drop-table", stop)
        self.assertIn("POLARIS_WAREHOUSE", stop)

    def test_scripts_use_uv_python_without_python3(self):
        for path in (ROOT / "scripts").glob("*.sh"):
            script = path.read_text()
            with self.subTest(path=path.name):
                self.assertNotIn("python3", script)

    def test_snowflake_helper_checks_horizon_schema_write_defaults(self):
        helper = self.read("scripts/snowflake_sql_api.py")

        self.assertIn("configure-horizon-schema", helper)
        self.assertIn("probe_horizon_schema_defaults", helper)
        self.assertIn("HORIZON_EXTERNAL_VOLUME", helper)
        self.assertIn("CATALOG = 'SNOWFLAKE'", helper)
        self.assertIn("EXTERNAL_VOLUME", helper)
        self.assertIn("request_horizon_access_token", helper)
        self.assertIn("HORIZON_PAT", helper)
        self.assertNotIn("refresh_horizon_token()\n    print(f\"wrote HORIZON_PAT", helper)

    def test_snowflake_helper_dotenv_preserves_explicit_overrides(self):
        import importlib.util

        module_path = ROOT / "scripts" / "snowflake_sql_api.py"
        spec = importlib.util.spec_from_file_location("snowflake_sql_api", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("SNOWFLAKE_ROLE='TESTER'\nSNOWFLAKE_DATABASE='DEVELOPMENT'\n")
            with mock.patch.dict(
                os.environ,
                {
                    "DBT_AWS_CLOUD_COST_ENV_OVERRIDES": "SNOWFLAKE_ROLE",
                    "SNOWFLAKE_ROLE": "ACCOUNTADMIN",
                },
                clear=True,
            ):
                module.load_dotenv(dotenv)
                self.assertEqual(os.environ["SNOWFLAKE_ROLE"], "ACCOUNTADMIN")
                self.assertEqual(os.environ["SNOWFLAKE_DATABASE"], "DEVELOPMENT")

    def test_snowflake_helper_dotenv_replaces_ambient_environment_without_override(self):
        import importlib.util

        module_path = ROOT / "scripts" / "snowflake_sql_api.py"
        spec = importlib.util.spec_from_file_location("snowflake_sql_api", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("SNOWFLAKE_ACCOUNT='local_account'\n")
            with mock.patch.dict(os.environ, {"SNOWFLAKE_ACCOUNT": "ambient_account"}, clear=True):
                module.load_dotenv(dotenv)
                self.assertEqual(os.environ["SNOWFLAKE_ACCOUNT"], "local_account")

    def test_schema_name_generation_keeps_catalog_schema_env_override(self):
        macro = self.read("macros/generate_schema_name.sql")

        self.assertIn("CATALOG_SCHEMA", macro)
        self.assertIn("custom_schema_name == 'aws_cloud_cost'", macro)
        self.assertIn("target.schema }}_{{ custom_schema_name", macro)


if __name__ == "__main__":
    unittest.main()
