"""Authentication for AiSQL.

Demo users live in `users` table (created on first run, seeded with bcrypt
hashes of fixed demo passwords). Authentication issues a short-lived JWT
that carries (user_id, role, exp). The Web UI sends the token in the
`Authorization: Bearer ...` header; the FastAPI dependency `current_user`
validates the token and returns a UserContext.

For production deployments, the demo seeding step should be replaced by an
admin-provisioned user table, and `JWT_SECRET` must be read from a real
secrets manager (see secrets.py).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import psycopg
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _jwt_secret() -> str:
    value = os.getenv("JWT_SECRET", "")
    if not value:
        raise RuntimeError(
            "JWT_SECRET is not set. Add it to .env "
            "(use `openssl rand -hex 32` to generate one)."
        )
    return value


def _jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _jwt_expires_minutes() -> int:
    return int(os.getenv("JWT_EXPIRES_MINUTES", "60"))


# Demo seed data. Roles must match db.ROLE_NAME_BY_APP_ROLE keys.
DEMO_USERS: dict[str, tuple[str, str]] = {
    # username     -> (plaintext password, app role)
    "admin":        ("admin_demo_pw",   "admin"),
    "analyst_user": ("analyst_demo_pw", "analyst"),
    "sales_user":   ("sales_demo_pw",   "sales"),
    "support_user": ("support_demo_pw", "support"),
    "guest":        ("guest_demo_pw",   "guest"),
}


@dataclass(frozen=True)
class UserContext:
    user_id: str
    role: str
    jti: str = ""  # token id, used for server-side revocation


def hash_password(plain: str) -> str:
    # bcrypt has a hard 72-byte limit on the input; truncate defensively.
    pw_bytes = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8")[:72], hashed.encode("utf-8")
        )
    except Exception:
        return False


def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_jwt_expires_minutes())
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_algorithm())


def decode_token(token: str) -> UserContext:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_jwt_algorithm()])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    role = payload.get("role")
    jti = payload.get("jti", "")
    exp = payload.get("exp")
    if not user_id or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing claims",
        )

    if jti and _is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return UserContext(user_id=user_id, role=role, jti=jti)


def _is_revoked(jti: str) -> bool:
    """Server-side check against the revoked_tokens table.

    Fails OPEN: a transient DB hiccup should not lock everyone out. Pair this
    with monitoring on the admin pool if stricter behaviour is desired.
    """
    try:
        from db import get_admin_pool
        with get_admin_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM revoked_tokens WHERE jti = %s",
                    (jti,),
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def revoke_token(jti: str, user_id: str, expires_at_ts: float) -> None:
    """Mark a JWT as revoked; future requests with the same token fail 401."""
    from db import get_admin_pool
    from datetime import datetime, timezone
    expires_at = datetime.fromtimestamp(expires_at_ts, tz=timezone.utc)
    with get_admin_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO revoked_tokens (jti, user_id, expires_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (jti) DO NOTHING
                """,
                (jti, user_id, expires_at),
            )


def cleanup_expired_revocations() -> int:
    """Delete revoked_tokens rows whose tokens have already expired.

    Once a token's `exp` is in the past, the JWT decode step rejects it on
    its own (ExpiredSignatureError) — the revocation row no longer serves a
    purpose. Returns the number of rows removed. Safe to run on any schedule.
    """
    from db import get_admin_pool
    try:
        with get_admin_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM revoked_tokens WHERE expires_at < NOW()"
                )
                return cur.rowcount or 0
    except Exception:
        return 0


def current_user(token: str | None = Depends(oauth2_scheme)) -> UserContext:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(token)


# ---------------------------------------------------------------------------
# Demo user seeding (runs once via migrations/seed_users.py)
# ---------------------------------------------------------------------------

USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.app_users (
    user_id       TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def seed_demo_users(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(USERS_TABLE_DDL)
        for user_id, (plain, role) in DEMO_USERS.items():
            cur.execute(
                """
                INSERT INTO app_users (user_id, password_hash, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    role          = EXCLUDED.role
                """,
                (user_id, hash_password(plain), role),
            )


def authenticate(conn: psycopg.Connection, user_id: str, password: str) -> UserContext:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT password_hash, role FROM app_users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    password_hash = row[0] if isinstance(row, tuple) else row["password_hash"]
    role = row[1] if isinstance(row, tuple) else row["role"]
    if not verify_password(password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    return UserContext(user_id=user_id, role=role)
