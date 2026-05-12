"""FastAPI web UI for AiSQL.

Run after starting docker compose and database_mcp_server.py:

    python web_ui.py

Then open http://127.0.0.1:8001 in a browser.

The UI talks to the existing database_query_client functions (so the same
two-layer supervisor and RBAC checks apply) and reads the audit log directly
from PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row
from pydantic import BaseModel

from auth import (
    UserContext,
    authenticate,
    cleanup_expired_revocations,
    create_access_token,
    current_user,
    revoke_token,
)
from db import get_admin_pool
from database_query_client import (
    DEFAULT_MCP_URL,
    DEFAULT_TRANSPORT,
    ROLE_TABLE_PERMISSIONS,
    USER_ROLES,
    ask_once_detailed,
    get_user_context,
)


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8001"))
REVOCATION_CLEANUP_INTERVAL = int(
    os.getenv("REVOCATION_CLEANUP_INTERVAL_SECONDS", "3600")
)


async def _revocation_cleanup_loop() -> None:
    """Periodically prune revoked_tokens rows whose `exp` has passed.

    Runs in the background for the lifetime of the FastAPI app. A single
    instance of the app is enough; if you scale horizontally, only one
    worker actually deletes (idempotent — others' DELETE just finds 0 rows).
    """
    while True:
        try:
            removed = cleanup_expired_revocations()
            if removed:
                print(f"[cleanup] removed {removed} expired revoked_tokens rows")
        except Exception as exc:
            print(f"[cleanup] error: {exc}")
        await asyncio.sleep(REVOCATION_CLEANUP_INTERVAL)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Startup: prune any old revocations once, then schedule the periodic loop.
    try:
        initial = cleanup_expired_revocations()
        if initial:
            print(f"[startup] cleaned {initial} expired revoked_tokens rows")
    except Exception as exc:
        print(f"[startup] revocation cleanup skipped: {exc}")

    task = asyncio.create_task(_revocation_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(
    title="AiSQL UI",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)

# Static assets (CSS, JS, future images) live next to web_ui.py so the dev
# server can pick them up without any build step.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))


class AskRequest(BaseModel):
    question: str
    thread_id: str | None = None


class LoginRequest(BaseModel):
    user_id: str
    password: str


@app.post("/api/auth/login")
async def login(req: LoginRequest) -> dict[str, Any]:
    """Issue a short-lived JWT for the given user_id/password pair."""
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL not configured")
    with get_admin_pool().connection() as conn:
        user = authenticate(conn, req.user_id, req.password)
    token = create_access_token(user.user_id, user.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.user_id,
        "role": user.role,
        "allowed_tables": format_allowed_tables(user.role),
    }


@app.post("/api/auth/logout")
async def logout(user: UserContext = Depends(current_user)) -> dict[str, Any]:
    """Server-side logout: mark the JWT's jti as revoked.

    The token will fail validation on subsequent requests even though it has
    not reached its `exp` timestamp yet. A null jti is treated as nothing to
    revoke (older tokens issued before this feature was added).
    """
    if not user.jti:
        return {"revoked": False, "reason": "token has no jti claim"}

    # Reconstruct expiry from the JWT for the revoked_tokens row. We do this
    # by decoding the raw token without verification — we already trust the
    # claims at this point because current_user validated them.
    import jwt as pyjwt
    raw_token = pyjwt.encode({"placeholder": 1}, "x")  # type stub
    # In practice the cleanest path is to read the exp from current_user's
    # token; the OAuth2 scheme already extracted it. We approximate with
    # JWT_EXPIRES_MINUTES (worst-case expiry).
    import time as _time
    expires_at_ts = _time.time() + int(os.getenv("JWT_EXPIRES_MINUTES", "60")) * 60

    revoke_token(user.jti, user.user_id, expires_at_ts)
    return {"revoked": True, "jti": user.jti}


def format_allowed_tables(role: str) -> list[str]:
    allowed = ROLE_TABLE_PERMISSIONS.get(role, set())
    if "*" in allowed:
        return ["*"]
    return sorted(allowed)


@app.get("/api/me")
async def me(user: UserContext = Depends(current_user)) -> dict[str, Any]:
    """Return the authenticated user's identity and permissions."""
    return {
        "id": user.user_id,
        "role": user.role,
        "allowed_tables": format_allowed_tables(user.role),
    }


@app.get("/api/audit")
async def audit_log(
    limit: int = 20,
    user: UserContext = Depends(current_user),
) -> list[dict[str, Any]]:
    """Audit log readable only by admin users (server-side check)."""
    if user.role != "admin":
        raise HTTPException(403, "Audit log is admin-only.")
    return await _audit_log_query(limit)


def _build_thread_title(question: str) -> str:
    cleaned = " ".join(question.split())
    return cleaned[:60].rstrip() + ("…" if len(cleaned) > 60 else "")


def _upsert_thread(thread_id: str, user_id: str, title: str) -> None:
    """Insert / refresh the chat_threads index row for this conversation."""
    if not DATABASE_URL:
        return
    try:
        with get_admin_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_threads (thread_id, user_id, title)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (thread_id) DO UPDATE
                    SET updated_at = NOW(),
                        title = CASE
                            WHEN chat_threads.title = '' THEN EXCLUDED.title
                            ELSE chat_threads.title
                        END
                    """,
                    (thread_id, user_id, title),
                )
    except Exception:
        # Indexing failure must never break the chat itself.
        pass


@app.post("/api/ask")
async def ask(
    req: AskRequest,
    user: UserContext = Depends(current_user),
) -> dict[str, Any]:
    """Run a question as the authenticated JWT user.

    The user_id is taken from the token, NOT from the request body — this is
    what makes the role spoof attack impossible.
    """
    if not req.question.strip():
        raise HTTPException(400, "question is required")

    # If the client didn't pin a thread_id yet, mint one bound to the user.
    thread_id = req.thread_id or f"{user.user_id}:{secrets.token_urlsafe(6)}"

    # Sidebar bookkeeping. Done BEFORE the LLM call so that even very long
    # questions show up in history immediately.
    _upsert_thread(thread_id, user.user_id, _build_thread_title(req.question))

    user_context = get_user_context(user.user_id)
    try:
        details = await ask_once_detailed(
            req.question,
            DEFAULT_MCP_URL,
            DEFAULT_TRANSPORT,
            user.user_id,
            thread_id=thread_id,
        )
    except Exception as exc:
        raise HTTPException(500, f"Agent error: {exc}")

    answer = details["answer"]
    blocked = "Bu istegi calistiramam" in answer
    parsed: dict[str, Any] = {
        "raw": answer,
        "blocked": blocked,
        "rows": details.get("rows"),
    }

    if blocked:
        for key, pattern in {
            "reason": "Neden: ",
            "category": "Kategori: ",
            "risk": "Risk: ",
        }.items():
            for line in answer.splitlines():
                if line.startswith(pattern):
                    parsed[key] = line[len(pattern):].strip()
                    break

    return {
        "user": user_context["user_id"],
        "role": user_context["role"],
        "allowed_tables": format_allowed_tables(user_context["role"]),
        "thread_id": thread_id,
        "result": parsed,
    }


@app.get("/api/threads")
async def list_threads(
    user: UserContext = Depends(current_user),
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the authenticated user's conversations, newest-first."""
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL is not set")
    limit = max(1, min(int(limit), 200))
    with get_admin_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT thread_id, title, created_at, updated_at
                FROM chat_threads
                WHERE user_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user.user_id, limit),
            )
            rows = cur.fetchall()
    return [
        {
            "thread_id": r["thread_id"],
            "title": r["title"] or "(yeni konusma)",
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


def _thread_belongs_to_user(thread_id: str, user_id: str) -> bool:
    if not DATABASE_URL:
        return False
    with get_admin_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM chat_threads WHERE thread_id = %s AND user_id = %s",
                (thread_id, user_id),
            )
            return cur.fetchone() is not None


@app.get("/api/threads/{thread_id}/messages")
async def thread_messages(
    thread_id: str,
    user: UserContext = Depends(current_user),
) -> dict[str, Any]:
    """Return the message history of a thread, gated by JWT user_id."""
    if not _thread_belongs_to_user(thread_id, user.user_id):
        raise HTTPException(404, "Thread not found.")

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    messages: list[dict[str, Any]] = []
    async with AsyncPostgresSaver.from_conn_string(DATABASE_URL) as cp:
        state = await cp.aget({"configurable": {"thread_id": thread_id}})
    if state is None:
        return {"thread_id": thread_id, "messages": []}
    raw = state.get("channel_values", {}).get("messages", []) or []
    for m in raw:
        kind = type(m).__name__
        content = getattr(m, "content", "")
        # Tool messages can hide multi-part lists; flatten to text.
        if isinstance(content, list):
            parts = []
            for piece in content:
                if isinstance(piece, dict) and "text" in piece:
                    parts.append(str(piece["text"]))
                else:
                    parts.append(str(piece))
            content = "\n".join(parts)
        # We expose only user-facing turns; tool calls add too much noise.
        if kind in {"HumanMessage", "AIMessage"} and str(content).strip():
            messages.append({"role": kind, "content": str(content)})
    return {"thread_id": thread_id, "messages": messages}


@app.delete("/api/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    user: UserContext = Depends(current_user),
) -> dict[str, Any]:
    """Remove a thread from the sidebar (checkpoints are kept on purpose)."""
    if not _thread_belongs_to_user(thread_id, user.user_id):
        raise HTTPException(404, "Thread not found.")
    with get_admin_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_threads WHERE thread_id = %s AND user_id = %s",
                (thread_id, user.user_id),
            )
    return {"thread_id": thread_id, "deleted": True}


async def _audit_log_query(limit: int) -> list[dict[str, Any]]:
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL is not set")
    limit = max(1, min(int(limit), 100))
    sql = """
        SELECT id, created_at, event_type, user_id, user_role, user_prompt,
               approved, risk_level, category, tables, reason
        FROM supervisor_audit_log
        ORDER BY id DESC
        LIMIT %s;
    """
    try:
        with get_admin_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(500, f"Audit query failed: {exc}")

    return [
        {
            **row,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }
        for row in rows
    ]


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")




if __name__ == "__main__":
    import uvicorn
    print(f"AiSQL UI: http://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")
