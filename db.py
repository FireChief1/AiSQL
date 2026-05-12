"""Database connection pools for AiSQL.

Two pools are exposed:

    agent_pool:  pool of `app_agent` connections. Every code path that runs
                 user-driven SQL must acquire a connection here and call
                 SET ROLE before running the query.
    audit_pool:  pool of `app_audit_writer` connections. The only thing this
                 pool is allowed to do is INSERT/SELECT supervisor_audit_log.

`run_as_role(conn, app_role)` issues SET ROLE inside the active session and
RESET ROLE on exit, so a misuse can't leak privileges between requests that
share a pooled connection.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _require_env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(
            f"{name} is not set. Did you create .env from .env.example and "
            "run `python migrations/apply.py`?"
        )
    return value


_agent_pool: ConnectionPool | None = None
_audit_pool: ConnectionPool | None = None
_admin_pool: ConnectionPool | None = None


def get_agent_pool() -> ConnectionPool:
    global _agent_pool
    if _agent_pool is None:
        _agent_pool = ConnectionPool(
            conninfo=_require_env("AGENT_DATABASE_URL"),
            min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
            max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
            timeout=float(os.getenv("DB_POOL_TIMEOUT", "10")),
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _agent_pool


def get_audit_pool() -> ConnectionPool:
    global _audit_pool
    if _audit_pool is None:
        _audit_pool = ConnectionPool(
            conninfo=_require_env("AUDIT_DATABASE_URL"),
            min_size=1,
            max_size=4,
            timeout=float(os.getenv("DB_POOL_TIMEOUT", "10")),
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )
    return _audit_pool


def get_admin_pool() -> ConnectionPool:
    """Pool for endpoints that need full DB access: user auth, chat thread
    bookkeeping, token revocation. Uses the superuser DATABASE_URL.

    This pool replaces ad-hoc psycopg.connect() calls scattered across
    web_ui.py and auth.py, so each request pays one cached connection instead
    of a fresh TLS+auth handshake.
    """
    global _admin_pool
    if _admin_pool is None:
        _admin_pool = ConnectionPool(
            conninfo=_require_env("DATABASE_URL"),
            min_size=1,
            max_size=int(os.getenv("ADMIN_POOL_MAX_SIZE", "5")),
            timeout=float(os.getenv("DB_POOL_TIMEOUT", "10")),
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )
    return _admin_pool


def close_pools() -> None:
    global _agent_pool, _audit_pool, _admin_pool
    if _agent_pool is not None:
        _agent_pool.close()
        _agent_pool = None
    if _audit_pool is not None:
        _audit_pool.close()
        _audit_pool = None
    if _admin_pool is not None:
        _admin_pool.close()
        _admin_pool = None


ROLE_NAME_BY_APP_ROLE: dict[str, str] = {
    "admin":   "app_role_admin",
    "analyst": "app_role_analyst",
    "sales":   "app_role_sales",
    "support": "app_role_support",
    "guest":   "app_role_guest",
}


@contextmanager
def run_as_role(
    conn: psycopg.Connection,
    app_role: str,
) -> Iterator[psycopg.Connection]:
    """SET ROLE for the duration of the with-block, then RESET ROLE.

    The cleanup path is defensive: if the wrapped query left the transaction
    in an aborted state, RESET ROLE would itself fail with "transaction is
    aborted" and that secondary failure would mask the real SQL error the
    caller wants to see. We ROLLBACK first, then RESET ROLE; both are
    best-effort so cleanup never raises.
    """
    db_role = ROLE_NAME_BY_APP_ROLE.get(app_role)
    if db_role is None:
        raise ValueError(f"Unknown application role: {app_role}")

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SET ROLE {role}").format(role=sql.Identifier(db_role))
        )
    try:
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            with conn.cursor() as cur:
                cur.execute("RESET ROLE")
        except Exception:
            pass
