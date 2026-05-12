"""AiSQL MCP server.

Exposes 7 tools over `streamable-http`. The security model:

- Every tool that runs user SQL pulls a connection from the `app_agent`
  pool and issues `SET ROLE app_role_<X>` for the duration of the query.
  The agent's PostgreSQL role has no direct grants, so without SET ROLE
  it cannot read anything (the schema isn't even visible).
- SQL is parsed with sqlglot and rejected unless it is a single,
  read-only SELECT. The previous regex/keyword check is gone.
- Audit log writes go through a separate `app_audit_writer` pool. That
  role only has INSERT/SELECT on supervisor_audit_log, so even a
  compromised agent connection cannot tamper with the audit trail.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from db import (
    ROLE_NAME_BY_APP_ROLE,
    get_agent_pool,
    get_audit_pool,
    run_as_role,
)
from sql_validator import extract_tables, validate


load_dotenv()

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MAX_ROWS = int(os.getenv("MAX_QUERY_ROWS", "50"))
STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))
AUDIT_LOG_TABLE = "supervisor_audit_log"


mcp = FastMCP(
    "AiSQL MCP Server",
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=True,
    json_response=True,
)


def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _resolve_role(app_role: str) -> str:
    """Map an unknown / empty role to the safest one."""
    return app_role if app_role in ROLE_NAME_BY_APP_ROLE else "guest"


@mcp.tool()
def list_tables(app_role: str = "guest") -> str:
    """List public tables visible to the given application role."""
    role = _resolve_role(app_role)
    try:
        with get_agent_pool().connection() as conn:
            with run_as_role(conn, role):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_type = 'BASE TABLE'
                          AND has_table_privilege(
                              current_user, table_schema || '.' || table_name, 'SELECT'
                          )
                        ORDER BY table_name;
                        """
                    )
                    rows = cur.fetchall()
        names = [r["table_name"] for r in rows]
        return to_json({"role": role, "tables": names, "table_count": len(names)})
    except Exception as exc:
        return to_json({"error": str(exc), "role": role})


@mcp.tool()
def get_table_schema(table_name: str, app_role: str = "guest") -> str:
    """Return column metadata for a table the role is allowed to read."""
    role = _resolve_role(app_role)
    requested = table_name.strip().lower()
    if not requested:
        return to_json({"error": "Table name is required."})

    try:
        with get_agent_pool().connection() as conn:
            with run_as_role(conn, role):
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT has_table_privilege(current_user, %s, 'SELECT') AS allowed",
                        (f"public.{requested}",),
                    )
                    row = cur.fetchone()
                    if not row or not row["allowed"]:
                        return to_json(
                            {
                                "error": (
                                    f"Role {role} cannot read table '{requested}' "
                                    "(or it does not exist)."
                                ),
                                "table": requested,
                            }
                        )
                    cur.execute(
                        """
                        SELECT column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                        ORDER BY ordinal_position;
                        """,
                        (requested,),
                    )
                    columns = cur.fetchall()
        return to_json({"role": role, "table": requested, "columns": columns})
    except Exception as exc:
        return to_json({"error": str(exc), "role": role, "table": requested})


@mcp.tool()
def validate_query(query: str) -> str:
    """Validate that a query is a single read-only SELECT (AST-based)."""
    result = validate(query)
    return to_json(
        {
            "is_valid": result.is_valid,
            "message": result.message,
            "normalized_query": result.normalized_query,
            "tables": sorted(extract_tables(query)) if result.is_valid else [],
        }
    )


def _audit_successful_query(
    *,
    user_id: str,
    user_role: str,
    query: str,
    row_count: int,
    duration_ms: int,
) -> None:
    """Write a successful_query event to the audit log.

    Compliance answer for "who saw what when". Never raises — audit logging
    must not break the user-facing query.
    """
    try:
        with get_audit_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {AUDIT_LOG_TABLE} (
                        event_type, user_prompt, user_id, user_role,
                        supervisor_model, approved, risk_level, category,
                        reason, query_text, tables, row_count, duration_ms
                    )
                    VALUES (
                        'successful_query', '', %s, %s,
                        '', TRUE, 'none', 'safe_read',
                        '', %s, '', %s, %s
                    )
                    """,
                    (user_id, user_role, query, row_count, duration_ms),
                )
    except Exception:
        pass


_COLUMN_ERROR_RE = re.compile(
    r'column\s+"?([^"\s]+)"?\s+does not exist', re.IGNORECASE
)
_RELATION_ERROR_RE = re.compile(
    r'relation\s+"?([^"\s]+)"?\s+does not exist', re.IGNORECASE
)


def _build_correction_hint(role: str, error_message: str) -> str:
    """Turn a PostgreSQL "X does not exist" error into actionable guidance.

    Uses the superuser DATABASE_URL (not the role-switched agent connection)
    so introspection queries against information_schema/pg_catalog don't fail
    on stripped role privileges. We then filter results by the role's actual
    SELECT permission via `has_table_privilege(role_name, ...)`.

    Hints never reveal data — only column/table names — and only ones a role
    with similar visibility would have discovered anyway via list_tables.
    """
    import psycopg
    from psycopg.rows import dict_row

    db_role = ROLE_NAME_BY_APP_ROLE.get(role)
    superuser_url = os.getenv("DATABASE_URL", "")
    if not superuser_url or not db_role:
        return ""

    # Use pg_catalog directly rather than information_schema views — the
    # post-migration role grants leave information_schema.* views unable to
    # read pg_statistic, but pg_catalog tables stay queryable.
    try:
        with psycopg.connect(superuser_url, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                col_match = _COLUMN_ERROR_RE.search(error_message)
                if col_match:
                    raw = col_match.group(1)
                    col_name = raw.split(".")[-1]
                    cur.execute(
                        """
                        SELECT c.relname AS table_name
                        FROM pg_attribute a
                        JOIN pg_class     c ON c.oid = a.attrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE a.attname = %s
                          AND n.nspname = 'public'
                          AND c.relkind = 'r'
                          AND NOT a.attisdropped
                          AND has_table_privilege(
                              %s, c.oid, 'SELECT'
                          )
                        ORDER BY c.relname
                        """,
                        (col_name, db_role),
                    )
                    owners = [r["table_name"] for r in cur.fetchall()]
                    if owners:
                        return (
                            f"Column '{col_name}' is NOT a column on every "
                            f"table — it lives in: {', '.join(owners)}. "
                            "Check your alias-to-table mapping and rewrite "
                            "the JOIN."
                        )
                    return (
                        f"No table your role can see has a column named "
                        f"'{col_name}'. Recheck the schema reference."
                    )

                rel_match = _RELATION_ERROR_RE.search(error_message)
                if rel_match:
                    cur.execute(
                        """
                        SELECT c.relname AS table_name
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relkind = 'r'
                          AND has_table_privilege(
                              %s, c.oid, 'SELECT'
                          )
                        ORDER BY c.relname
                        """,
                        (db_role,),
                    )
                    visible = [r["table_name"] for r in cur.fetchall()]
                    return (
                        f"Table '{rel_match.group(1)}' is not visible to your "
                        f"role. Tables you can read: {', '.join(visible)}."
                    )
    except Exception:
        return ""

    return ""


@mcp.tool()
def execute_query(
    query: str,
    app_role: str = "guest",
    user_id: str = "unknown",
) -> str:
    """Execute a safe SELECT query under the given application role.

    Two independent barriers protect this tool:
      1. sqlglot AST validation - rejects anything that isn't a single SELECT.
      2. PostgreSQL role-level grants - even if the validator missed something,
         the underlying database role has no INSERT/UPDATE/DELETE permission.

    On success, the query is logged to the audit trail with row_count and
    duration so that compliance reports can answer "who saw what when".

    On failure, the response includes a `hint` field with actionable advice
    (e.g. "column X lives in table Y, fix your alias"). The LangChain agent
    is instructed to consume this hint and retry with a corrected query.
    """
    role = _resolve_role(app_role)
    validation = validate(query)
    if not validation.is_valid:
        return to_json({"error": validation.message, "role": role})

    started = time.perf_counter()
    try:
        with get_agent_pool().connection() as conn:
            with run_as_role(conn, role):
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    cur.execute(validation.normalized_query)
                    rows = cur.fetchmany(MAX_ROWS + 1)
    except Exception as exc:
        err = str(exc)
        hint = _build_correction_hint(role, err)
        return to_json(
            {
                "error": err,
                "hint": hint,
                "role": role,
                "query": validation.normalized_query,
                "retry_advice": (
                    "If a hint is present, use it to rewrite ONE corrected SQL "
                    "and call execute_query again. Do not repeat the same SQL."
                ) if hint else "",
            }
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    has_more = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]

    _audit_successful_query(
        user_id=user_id,
        user_role=role,
        query=validation.normalized_query,
        row_count=len(rows),
        duration_ms=duration_ms,
    )
    return to_json(
        {
            "role": role,
            "query": validation.normalized_query,
            "row_count_returned": len(rows),
            "max_rows": MAX_ROWS,
            "has_more_rows": has_more,
            "rows": rows,
        }
    )


@mcp.tool()
def get_database_info(app_role: str = "guest") -> str:
    """Database metadata visible to the given role."""
    role = _resolve_role(app_role)
    try:
        with get_agent_pool().connection() as conn:
            with run_as_role(conn, role):
                with conn.cursor() as cur:
                    cur.execute("SELECT current_database() AS db, version() AS v;")
                    info = cur.fetchone()
                    cur.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND has_table_privilege(
                              current_user, table_schema || '.' || table_name, 'SELECT'
                          );
                        """
                    )
                    tables = [r["table_name"] for r in cur.fetchall()]
        return to_json(
            {
                "role": role,
                "database_name": info["db"],
                "postgres_version": info["v"],
                "tables": tables,
                "table_count": len(tables),
                "max_query_rows": MAX_ROWS,
            }
        )
    except Exception as exc:
        return to_json({"error": str(exc), "role": role})


@mcp.tool()
def log_security_event(
    event_type: str,
    user_prompt: str,
    supervisor_model: str,
    approved: bool,
    risk_level: str,
    category: str,
    reason: str,
    user_id: str = "unknown",
    user_role: str = "unknown",
    query_text: str = "",
    tables: str = "",
) -> str:
    """Append a security decision to the audit log (audit-writer role)."""
    try:
        with get_audit_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {AUDIT_LOG_TABLE} (
                        event_type, user_prompt, user_id, user_role,
                        supervisor_model, approved, risk_level, category,
                        reason, query_text, tables
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at;
                    """,
                    (
                        event_type.strip() or "security_event",
                        user_prompt.strip(),
                        user_id.strip() or "unknown",
                        user_role.strip() or "unknown",
                        supervisor_model.strip(),
                        approved,
                        risk_level.strip() or "unknown",
                        category.strip() or "unknown",
                        reason.strip() or "No reason provided.",
                        query_text.strip(),
                        tables.strip(),
                    ),
                )
                row = cur.fetchone()
        return to_json(
            {
                "logged": True,
                "table": AUDIT_LOG_TABLE,
                "id": row["id"],
                "created_at": row["created_at"],
            }
        )
    except Exception as exc:
        return to_json({"logged": False, "error": str(exc), "table": AUDIT_LOG_TABLE})


@mcp.tool()
def get_security_events(limit: int = 10) -> str:
    """Return recent audit log rows (audit-writer role can read its own log)."""
    safe_limit = max(1, min(int(limit), 50))
    try:
        with get_audit_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, created_at, event_type, user_prompt, user_id,
                           user_role, supervisor_model, approved, risk_level,
                           category, reason, query_text, tables
                    FROM {AUDIT_LOG_TABLE}
                    ORDER BY id DESC
                    LIMIT %s;
                    """,
                    (safe_limit,),
                )
                rows = cur.fetchall()
        return to_json({"table": AUDIT_LOG_TABLE, "rows": rows, "row_count": len(rows)})
    except Exception as exc:
        return to_json({"error": str(exc), "table": AUDIT_LOG_TABLE})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
