"""Static configuration invariants for the demo.

These are fast, no-dbt-run checks that catch the ways a fresh clone silently
breaks: a catalog name that doesn't match `+catalog_name`, a missing seed
column, an env var referenced by the config but undocumented in `.env.example`,
an eagerly-active OAuth secret that defeats the zero-credential default, or
leftover references to tooling that was removed.

Run: uv run pytest tests/test_demo_configuration.py -v
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every catalog the demo ships a (possibly commented) block for.
ALL_CATALOGS = ["ducklake", "lakekeeper", "horizon", "polaris", "unity", "s3_tables"]


class DemoConfigurationTest(unittest.TestCase):
    def read(self, rel: str) -> str:
        return (ROOT / rel).read_text()

    def active_catalog_names(self) -> list[str]:
        # Active catalog blocks are uncommented `  - name: <x>`; inactive ones
        # are prefixed with `# `.
        return re.findall(r"^  - name: (\w+)$", self.read("catalogs.yml"), re.M)

    def test_required_artifacts_exist(self):
        for rel in [
            "profiles.yml",
            "catalogs.yml",
            "dbt_project.yml",
            "docker-compose.yml",
            ".env.example",
            "scripts/setup.sh",
            "seeds/aws_cost_report.csv",
            "seeds/properties.yml",
        ]:
            with self.subTest(present=rel):
                self.assertTrue((ROOT / rel).is_file(), f"missing required file: {rel}")
        # Tooling removed during simplification must stay gone.
        for gone in [
            "scripts/setup_env.sh",
            "scripts/generate_local_csv.sh",
            "scripts/start.sh",
            "scripts/stop.sh",
            "scripts/count_aws_cost_rows.sh",
            "scripts/polaris_shadowtraffic.py",
            "scripts/use_catalog.sh",
            "shadowtraffic",
            "local_files",
            "models/staging/src_aws_cloud_cost.yml",
            "models/staging/base/stg_aws_cloud_cost__report_base.sql",
        ]:
            with self.subTest(removed=gone):
                self.assertFalse((ROOT / gone).exists(), f"should have been removed: {gone}")

    def test_exactly_one_active_catalog_matches_project(self):
        active = self.active_catalog_names()
        self.assertEqual(len(active), 1, f"expected exactly one active catalog, got {active}")
        match = re.search(r"\+catalog_name:\s*(\w+)", self.read("dbt_project.yml"))
        self.assertIsNotNone(match, "no +catalog_name in dbt_project.yml")
        self.assertEqual(
            match.group(1),
            active[0],
            "dbt_project.yml +catalog_name must match the single active catalog in catalogs.yml "
            "(Fusion attaches every catalog; the names must line up)",
        )

    def test_all_catalog_blocks_present(self):
        catalogs = self.read("catalogs.yml")
        for name in ALL_CATALOGS:
            with self.subTest(name=name):
                self.assertTrue(
                    f"  - name: {name}" in catalogs or f"#   - name: {name}" in catalogs,
                    f"catalog block missing entirely: {name}",
                )

    def test_seed_has_report_columns(self):
        header = self.read("seeds/aws_cost_report.csv").splitlines()[0]
        for column in ["identity_line_item_id", "line_item_unblended_cost", "_modified", "_file"]:
            with self.subTest(column=column):
                self.assertIn(column, header)

    def test_env_example_covers_referenced_env_vars(self):
        def referenced(text: str) -> set:
            return set(re.findall(r"env_var\('([A-Z][A-Z0-9_]*)'", text))

        refs = referenced(self.read("profiles.yml")) | referenced(self.read("catalogs.yml"))
        documented = set(
            re.findall(r"^#? ?([A-Z][A-Z0-9_]*)=", self.read(".env.example"), re.M)
        )
        self.assertEqual(
            refs - documented,
            set(),
            "env vars referenced in profiles.yml/catalogs.yml but missing from .env.example",
        )

    def test_profiles_default_activates_only_minio_secret(self):
        # The default (ducklake) path must create no OAuth secrets at DuckDB init,
        # or it would require credentials. minio_secret is static (no network call).
        active = []
        for line in self.read("profiles.yml").splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = re.search(r"name:\s*(\w+)\s*$", line)
            if match:
                active.append(match.group(1))
        self.assertEqual(
            active,
            ["minio_secret"],
            "default profile must activate only minio_secret (keeps the ducklake path zero-cred)",
        )

    def test_staging_reads_from_the_seed(self):
        staging = self.read("models/staging/stg_report.sql")
        self.assertIn("ref('aws_cost_report')", staging, "staging must read the committed seed")
        self.assertNotIn("read_csv", staging)
        self.assertNotIn("source(", staging)
        self.assertNotIn("env_var", staging)
        self.assertNotIn(" over (", staging.lower(), "staging must avoid window functions")

    def test_readme_documents_the_simplified_flow(self):
        readme = self.read("README.md")
        for snippet in [
            ".env.example",
            "DuckDB 1.5.4",
            "dbt seed",
            "+catalog_name",
            "docker compose up",
            "daily_overview",
        ]:
            with self.subTest(present=snippet):
                self.assertIn(snippet, readme)
        # The demo runs on the published dbt Fusion CLI; no local build, no DBT_BIN.
        for gone in [
            "use_catalog.sh",
            "local_files",
            "shadowtraffic",
            "setup_env.sh",
            "DBT_BIN",
            "cargo build",
        ]:
            with self.subTest(absent=gone):
                self.assertNotIn(gone, readme)

    def test_shell_scripts_use_uv_not_bare_python(self):
        for script in sorted(ROOT.glob("scripts/*.sh")):
            for line in script.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "python" not in stripped:
                    continue
                with self.subTest(script=script.name, line=stripped):
                    self.assertIn("uv run", stripped, "invoke python via `uv run`, never bare")


if __name__ == "__main__":
    unittest.main()
