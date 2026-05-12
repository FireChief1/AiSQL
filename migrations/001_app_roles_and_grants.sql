-- 001_app_roles_and_grants.sql
-- Production-grade RBAC for AiSQL.
--
-- Creates group roles (NOLOGIN) per application role and grants per-table
-- SELECT permissions matching the application's ROLE_TABLE_PERMISSIONS map.
-- No write privileges are ever granted to non-admin roles at the database
-- level.
--
-- The login roles `app_agent` and `app_audit_writer` are created by
-- migrations/apply.py using parameterized queries, so this file never
-- contains any password material.
--
-- This file is idempotent.

-- ===========================================================================
-- 1. Group roles (NOLOGIN) for application roles
-- ===========================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role_admin') THEN
        CREATE ROLE app_role_admin NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role_analyst') THEN
        CREATE ROLE app_role_analyst NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role_sales') THEN
        CREATE ROLE app_role_sales NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role_support') THEN
        CREATE ROLE app_role_support NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role_guest') THEN
        CREATE ROLE app_role_guest NOLOGIN;
    END IF;
END
$$;

-- ===========================================================================
-- 2. Schema-level permissions
-- ===========================================================================

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  USAGE  ON SCHEMA public TO app_role_admin, app_role_analyst,
                                  app_role_sales, app_role_support,
                                  app_role_guest;

-- ===========================================================================
-- 3. Per-role table grants
-- ===========================================================================

-- Admin: SELECT on every existing table. No write grant on purpose; writes
-- happen via migrations as the superuser only.
DO $$
DECLARE
    t record;
BEGIN
    FOR t IN
        SELECT tablename
        FROM   pg_tables
        WHERE  schemaname = 'public'
    LOOP
        EXECUTE format('GRANT SELECT ON public.%I TO app_role_admin', t.tablename);
    END LOOP;
END
$$;

-- Analyst: catalog-style tables
GRANT SELECT ON public.album          TO app_role_analyst;
GRANT SELECT ON public.artist         TO app_role_analyst;
GRANT SELECT ON public.genre          TO app_role_analyst;
GRANT SELECT ON public.media_type     TO app_role_analyst;
GRANT SELECT ON public.playlist       TO app_role_analyst;
GRANT SELECT ON public.playlist_track TO app_role_analyst;
GRANT SELECT ON public.track          TO app_role_analyst;

-- Sales: customer + invoice data
GRANT SELECT ON public.customer     TO app_role_sales;
GRANT SELECT ON public.invoice      TO app_role_sales;
GRANT SELECT ON public.invoice_line TO app_role_sales;

-- Support: customer only
GRANT SELECT ON public.customer TO app_role_support;

-- Guest: slim catalog subset
GRANT SELECT ON public.album  TO app_role_guest;
GRANT SELECT ON public.artist TO app_role_guest;
GRANT SELECT ON public.genre  TO app_role_guest;

-- ===========================================================================
-- 4. Audit log table
-- ===========================================================================

CREATE TABLE IF NOT EXISTS public.supervisor_audit_log (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type       TEXT NOT NULL,
    user_prompt      TEXT NOT NULL,
    user_id          TEXT NOT NULL DEFAULT 'unknown',
    user_role        TEXT NOT NULL DEFAULT 'unknown',
    supervisor_model TEXT NOT NULL,
    approved         BOOLEAN NOT NULL,
    risk_level       TEXT NOT NULL,
    category         TEXT NOT NULL,
    reason           TEXT NOT NULL,
    query_text       TEXT NOT NULL DEFAULT '',
    tables           TEXT NOT NULL DEFAULT ''
);

GRANT SELECT ON public.supervisor_audit_log TO app_role_admin;

-- ===========================================================================
-- 5. Default privileges for FUTURE tables
-- ===========================================================================

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO app_role_admin;
