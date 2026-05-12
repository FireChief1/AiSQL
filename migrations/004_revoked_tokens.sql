-- 004_revoked_tokens.sql
-- Server-side JWT revocation table.
--
-- Without this, a stolen JWT works until its `exp` claim — up to 60 minutes.
-- With this, the /api/auth/logout endpoint inserts the token's `jti` here and
-- every protected request checks the jti against this table.
--
-- Rows expire naturally once we know the original token is past its exp.
-- A periodic cleanup job can remove rows where expires_at < NOW().

CREATE TABLE IF NOT EXISTS public.revoked_tokens (
    jti        TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires
    ON public.revoked_tokens (expires_at);

GRANT SELECT, INSERT, DELETE ON public.revoked_tokens TO app_audit_writer;
