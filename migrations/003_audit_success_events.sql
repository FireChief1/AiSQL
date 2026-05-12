-- 003_audit_success_events.sql
-- Compliance-ready audit log: capture successful queries in addition to blocks.
--
-- Two new columns:
--   row_count   - how many rows the query returned (NULL for blocked events)
--   duration_ms - how long execute_query spent inside PostgreSQL (NULL too)
--
-- Idempotent.

ALTER TABLE public.supervisor_audit_log
    ADD COLUMN IF NOT EXISTS row_count   INT NULL,
    ADD COLUMN IF NOT EXISTS duration_ms INT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_user_event
    ON public.supervisor_audit_log (user_id, event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_created_at
    ON public.supervisor_audit_log (created_at DESC);
