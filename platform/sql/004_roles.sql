-- 004: least-privilege roles (security-and-api.md §4.3, §7, T-10). Passwords are never in SQL:
-- migration 004 grants LOGIN with a password only when BG_DB_<ROLE>_PASSWORD is set.
-- In compose the database owner (POSTGRES_USER) runs migrations; bg_migrator is the k3s equivalent.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bg_app') THEN CREATE ROLE bg_app NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bg_migrator') THEN CREATE ROLE bg_migrator NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bg_grafana') THEN CREATE ROLE bg_grafana NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bg_auth') THEN CREATE ROLE bg_auth NOLOGIN; END IF;
END $$;

-- bg_app: the platform (API, workers). Read/write data, no DDL.
GRANT USAGE ON SCHEMA public TO bg_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO bg_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO bg_app;

-- bg_migrator: schema changes in k3s (Alembic job).
GRANT USAGE, CREATE ON SCHEMA public TO bg_migrator;

-- bg_grafana: anonymous Grafana viewers can send arbitrary SQL through /api/ds/query, so this role
-- reads an allowlist only, never overrides (approver reasons) or the auth schema, read-only, 5 s cap.
GRANT USAGE ON SCHEMA public TO bg_grafana;
GRANT SELECT ON services, slos, sli_buckets, sli_hourly, sli_daily, decisions, deployments, incidents,
                policy_versions TO bg_grafana;
ALTER ROLE bg_grafana SET default_transaction_read_only = on;
ALTER ROLE bg_grafana SET statement_timeout = '5s';

-- bg_auth: Better Auth's own tables (sessions, accounts) live in schema auth, owned by this role,
-- which cannot read BudgetGuard's tables; and no BudgetGuard role can read auth.
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION bg_auth;
REVOKE ALL ON SCHEMA auth FROM PUBLIC;
