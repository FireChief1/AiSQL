"""Apply SQL migrations against the AiSQL PostgreSQL database.

Usage:
    python migrations/apply.py

Required environment variables (auto-loaded from .env):
    DATABASE_URL              - superuser connection string
    APP_AGENT_PASSWORD        - password for the app_agent login role
    APP_AUDIT_WRITER_PASSWORD - password for the app_audit_writer login role

Behaviour:
    1. Connects as the superuser.
    2. Runs every *.sql file in migrations/ alphabetically, wrapping each in
       its own transaction.
    3. Creates (or rotates the password of) the two login roles using
       parameterized queries.

This script is the only place in the codebase that ever sees a database
password in plain text in memory, and even there it is only used to call
psycopg's parameterized API.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql


MIGRATIONS_DIR = Path(__file__).resolve().parent


def quoted_identifier(name: str) -> sql.Composable:
    return sql.Identifier(name)


def upsert_login_role(
    conn: psycopg.Connection,
    role_name: str,
    password: str,
) -> None:
    """Create the login role if missing, otherwise rotate its password.

    The role name is interpolated via psycopg.sql.Identifier (no injection
    surface). The password is sent as a bound parameter inside a literal
    string, so it is not concatenated into SQL text.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (role_name,),
        )
        exists = cur.fetchone() is not None

    role_ident = quoted_identifier(role_name)
    with conn.cursor() as cur:
        if not exists:
            # NOINHERIT is critical: the agent must explicitly SET ROLE
            # before it gets any table privileges. Without NOINHERIT,
            # granting app_role_admin would silently give the agent admin
            # privileges on every connection.
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {role} LOGIN NOINHERIT PASSWORD {pwd}"
                ).format(
                    role=role_ident,
                    pwd=sql.Literal(password),
                )
            )
            print(f"  created login role: {role_name} (NOINHERIT)")
        else:
            cur.execute(
                sql.SQL(
                    "ALTER ROLE {role} WITH LOGIN NOINHERIT PASSWORD {pwd}"
                ).format(
                    role=role_ident,
                    pwd=sql.Literal(password),
                )
            )
            print(f"  rotated password + enforced NOINHERIT for: {role_name}")


def grant_membership(
    conn: psycopg.Connection,
    member: str,
    groups: list[str],
) -> None:
    """Grant role memberships with INHERIT FALSE.

    Per PostgreSQL 16, the per-grant `WITH INHERIT FALSE` option forces the
    receiving role to use `SET ROLE` before it can exercise the privileges of
    the granted role. The legacy role-level `NOINHERIT` flag alone is not
    sufficient in PG 16+.
    """
    with conn.cursor() as cur:
        # Re-grant cleanly: REVOKE first so we replace any previous grant
        # that might lack the WITH INHERIT FALSE option.
        for group in groups:
            cur.execute(
                sql.SQL("REVOKE {group} FROM {member}").format(
                    group=quoted_identifier(group),
                    member=quoted_identifier(member),
                )
            )
        for group in groups:
            cur.execute(
                sql.SQL(
                    "GRANT {group} TO {member} WITH INHERIT FALSE, SET TRUE"
                ).format(
                    group=quoted_identifier(group),
                    member=quoted_identifier(member),
                )
            )
    print(f"  granted (INHERIT FALSE) {', '.join(groups)} -> {member}")


def grant_audit_log_writes(conn: psycopg.Connection) -> None:
    """Grant the audit log INSERT/SELECT, schema USAGE and sequence to writer."""
    with conn.cursor() as cur:
        cur.execute("GRANT USAGE ON SCHEMA public TO app_audit_writer")
        cur.execute(
            "GRANT INSERT, SELECT ON public.supervisor_audit_log "
            "TO app_audit_writer"
        )
        cur.execute(
            "GRANT USAGE ON SEQUENCE supervisor_audit_log_id_seq "
            "TO app_audit_writer"
        )
    print("  granted audit log write access to app_audit_writer")


def apply_sql_file(conn: psycopg.Connection, path: Path) -> None:
    print(f"  applying {path.name}")
    sql_text = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql_text)


def main() -> int:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    agent_pwd = os.getenv("APP_AGENT_PASSWORD")
    audit_pwd = os.getenv("APP_AUDIT_WRITER_PASSWORD")

    missing = [
        name
        for name, value in (
            ("DATABASE_URL", database_url),
            ("APP_AGENT_PASSWORD", agent_pwd),
            ("APP_AUDIT_WRITER_PASSWORD", audit_pwd),
        )
        if not value
    ]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"Connecting to {database_url.split('@')[-1]}")
    with psycopg.connect(database_url, autocommit=False) as conn:
        for sql_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            apply_sql_file(conn, sql_path)
            conn.commit()

        # Login roles - separate step so passwords never enter the SQL text.
        upsert_login_role(conn, "app_agent", agent_pwd)
        upsert_login_role(conn, "app_audit_writer", audit_pwd)
        grant_membership(
            conn,
            "app_agent",
            [
                "app_role_admin",
                "app_role_analyst",
                "app_role_sales",
                "app_role_support",
                "app_role_guest",
            ],
        )
        grant_audit_log_writes(conn)
        conn.commit()

    print("Migrations applied successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
