-- 002_chat_threads.sql
-- ChatGPT-style conversation index for AiSQL.
--
-- LangGraph's checkpointer already stores the full message history per
-- thread_id, but it has no notion of "which thread_ids belong to which user"
-- or "what should this thread be called in the sidebar". This table is the
-- minimal index that fills that gap.
--
-- Rows are upserted on every successful /api/ask so the sidebar can list a
-- user's conversations newest-first.

CREATE TABLE IF NOT EXISTS public.chat_threads (
    thread_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_user_updated
    ON public.chat_threads (user_id, updated_at DESC);

-- app_audit_writer rolu uygulama tarafindan yazma yapan rol oldugu icin
-- chat thread index'ini de o yazar. RLS / row-level isolation read tarafta
-- WHERE user_id = ... ile yapilir (login JWT'sinden geldigi icin spoof
-- edilemez).
GRANT INSERT, SELECT, UPDATE ON public.chat_threads     TO app_audit_writer;
GRANT SELECT                  ON public.chat_threads    TO app_role_admin;
