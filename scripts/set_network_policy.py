# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "cryptography",
# ]
# ///
"""Create/update a Snowflake network policy and optionally apply it account-wide.

Reuses the key-pair JWT auth and SQL API helpers from scripts/snowflake_sql_api.py
(same env vars: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY, ...).
Requires ACCOUNTADMIN (or a role granted NETWORK_ADMIN) in SNOWFLAKE_ROLE.

The --ip-list value is never hardcoded here — pass the real Lake Compute/Fivetran
staging egress CIDR(s) yourself; check Fivetran's published IP allowlist or ask
whoever administers that service.

Dry-run by default (prints the SQL only). Pass --yes to actually execute.

Usage:
    uv run scripts/set_network_policy.py \\
        --policy-name lake_compute_allow \\
        --ip-list 1.2.3.4/32,5.6.7.0/24 \\
        --apply-to-account \\
        --yes
"""
from __future__ import annotations

import argparse

from snowflake_sql_api import ROOT, execute_statement, load_dotenv, quote_sql_string


def build_ip_list_sql(ip_list: list[str]) -> str:
    return ", ".join(quote_sql_string(ip) for ip in ip_list)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy-name", required=True, help="network policy identifier, e.g. lake_compute_allow")
    parser.add_argument(
        "--ip-list",
        required=True,
        help="comma-separated CIDR list to allow, e.g. 1.2.3.4/32,5.6.7.0/24 "
        "(the real Lake Compute/Fivetran egress range — verify this yourself, it is not looked up here)",
    )
    parser.add_argument(
        "--mode",
        choices=["create", "alter"],
        default="create",
        help="'create': CREATE NETWORK POLICY IF NOT EXISTS ... ALLOWED_IP_LIST=(...). "
        "'alter': ALTER NETWORK POLICY <name> SET ALLOWED_IP_LIST=(...) on an existing policy.",
    )
    parser.add_argument(
        "--apply-to-account",
        action="store_true",
        help="also run ALTER ACCOUNT SET NETWORK_POLICY = <name> to activate it account-wide",
    )
    parser.add_argument("--yes", action="store_true", help="actually execute; without this, only prints the SQL")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    ip_list = [ip.strip() for ip in args.ip_list.split(",") if ip.strip()]
    if not ip_list:
        raise SystemExit("--ip-list must contain at least one CIDR/IP")

    ip_list_sql = build_ip_list_sql(ip_list)
    statements = []
    if args.mode == "create":
        statements.append(
            f"CREATE NETWORK POLICY IF NOT EXISTS {args.policy_name} "
            f"ALLOWED_IP_LIST = ({ip_list_sql}) "
            f"COMMENT = 'Allow Lake Compute (Fivetran) staging egress'"
        )
    else:
        statements.append(f"ALTER NETWORK POLICY {args.policy_name} SET ALLOWED_IP_LIST = ({ip_list_sql})")

    if args.apply_to_account:
        # NOTE: verify this against Snowflake's current docs for your account -
        # some doc versions show a quoted string literal here instead of a bare identifier.
        statements.append(f"ALTER ACCOUNT SET NETWORK_POLICY = {args.policy_name}")

    print("about to run (role must be ACCOUNTADMIN or have NETWORK_ADMIN):")
    for statement in statements:
        print(f"  {statement};")

    if not args.yes:
        print("\ndry run only - pass --yes to actually execute")
        return

    for statement in statements:
        execute_statement(statement, include_context=False)
        print(f"ok: {statement}")


if __name__ == "__main__":
    main()
