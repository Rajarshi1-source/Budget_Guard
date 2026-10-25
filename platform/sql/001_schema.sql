CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE services (
    id            SMALLSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    owner         TEXT NOT NULL,
    failure_mode  TEXT NOT NULL DEFAULT 'fail_open'
                  CHECK (failure_mode IN ('fail_open','fail_closed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE slos (
    id           SERIAL PRIMARY KEY,
    service_id   SMALLINT NOT NULL REFERENCES services(id),
    name         TEXT NOT NULL,
    objective    NUMERIC(7,6) NOT NULL CHECK (objective > 0 AND objective < 1),
    window_days  SMALLINT NOT NULL DEFAULT 30,
    spec_hash    TEXT NOT NULL,                       -- sha256 of the YAML that defined it
    active       BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (service_id, name)
);

-- 5-minute request-weighted buckets: the only time-series table
CREATE TABLE sli_buckets (
    slo_id        INT NOT NULL REFERENCES slos(id),
    bucket_start  TIMESTAMPTZ NOT NULL,
    total         DOUBLE PRECISION NOT NULL CHECK (total >= 0),
    bad           DOUBLE PRECISION NOT NULL CHECK (bad >= 0),
    self_bad      DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (self_bad >= 0),
    integrity     TEXT NOT NULL DEFAULT 'ok'
                  CHECK (integrity IN ('ok','low_traffic','stale','no_data')),
    PRIMARY KEY (slo_id, bucket_start)
);
SELECT create_hypertable('sli_buckets', by_range('bucket_start', INTERVAL '7 days'));

CREATE TABLE policy_versions (
    version          TEXT PRIMARY KEY,                -- last commit SHA touching policy/freeze-policy.yaml
    backtest_report  JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE decisions (
    id               BIGSERIAL PRIMARY KEY,
    service_id       SMALLINT NOT NULL REFERENCES services(id),
    github_delivery  TEXT NOT NULL UNIQUE,            -- idempotency key
    github_deployment_id BIGINT UNIQUE,               -- [added] signed in the body; the delivery header is not (security-and-api.md T-1)
    github_run_id    BIGINT,
    sha              TEXT NOT NULL,
    policy_version   TEXT NOT NULL REFERENCES policy_versions(version),
    inputs_hash      TEXT NOT NULL,
    inputs           JSONB NOT NULL,                  -- full GateInput + lane_evidence
    verdict          TEXT NOT NULL CHECK (verdict IN ('admit','admit_unverified','block')),
    reason_code      TEXT NOT NULL,
    price_pct        NUMERIC(8,4),
    affordable_pct   NUMERIC(8,4),
    canary_profile   SMALLINT[] NOT NULL,
    brief_source     TEXT CHECK (brief_source IN ('llm','template')),
    callback_state   TEXT NOT NULL DEFAULT 'pending'
                     CHECK (callback_state IN ('pending','sent','failed')),   -- [added] retry visibility
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX decisions_service_time ON decisions (service_id, decided_at DESC);
CREATE INDEX decisions_run ON decisions (github_run_id);

CREATE TABLE overrides (
    id                       BIGSERIAL PRIMARY KEY,
    decision_id              BIGINT NOT NULL REFERENCES decisions(id),   -- the BLOCK being overridden
    approver_id              BIGINT,                 -- [added] numeric GitHub user id (authorization); NULL for reconciler rows
    approver                 TEXT NOT NULL,          -- GitHub login for display, or 'github-admin-bypass' / 'cluster-admin-drift'
    reason                   TEXT NOT NULL CHECK (length(reason) >= 20),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at               TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '30 minutes',  -- [added]
    consumed_by_decision_id  BIGINT UNIQUE REFERENCES decisions(id)   -- [added] single use: the re-run's ADMIT
);
CREATE INDEX overrides_open ON overrides (decision_id) WHERE consumed_by_decision_id IS NULL;

CREATE TABLE deployments (
    id                 BIGSERIAL PRIMARY KEY,
    service_id         SMALLINT NOT NULL REFERENCES services(id),
    decision_id        BIGINT REFERENCES decisions(id),
    sha                TEXT NOT NULL,
    service_tree_sha   TEXT,                          -- [added] proves the rollback lane
    image_digest       TEXT,                          -- set by deployment_status after rollout
    pr_number          INT,
    lines_changed      INT NOT NULL DEFAULT 0,
    touches_migration  BOOLEAN NOT NULL DEFAULT false,
    config_only        BOOLEAN NOT NULL DEFAULT false,
    dependency_bump    BOOLEAN NOT NULL DEFAULT false,
    first_commit_at    TIMESTAMPTZ,                   -- [added] lead time = finished_at - first_commit_at
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ,
    status             TEXT NOT NULL
                       CHECK (status IN ('in_progress','success','failure','rolled_back')),
    is_change_failure  BOOLEAN,                       -- NULL until the 60-minute window closes
    change_failure_budget_pct NUMERIC(8,4)            -- [added] feeds median failure cost in pricing
);
CREATE INDEX deployments_service_time ON deployments (service_id, started_at DESC);
CREATE INDEX deployments_tree ON deployments (service_id, service_tree_sha)
    WHERE status = 'success' AND is_change_failure IS FALSE;

CREATE TABLE incidents (
    id                       BIGSERIAL PRIMARY KEY,
    service_id               SMALLINT NOT NULL REFERENCES services(id),
    culprit_service_id       SMALLINT REFERENCES services(id),   -- D-3 attribution
    suspected_deployment_id  BIGINT REFERENCES deployments(id),
    fingerprint              TEXT NOT NULL,                       -- Alertmanager fingerprint
    alertname                TEXT NOT NULL,                       -- [added]
    slo                      TEXT,                                -- [added]
    incident_type            TEXT NOT NULL,
    severity                 TEXT NOT NULL CHECK (severity IN ('page','ticket')),
    started_at               TIMESTAMPTZ NOT NULL,
    resolved_at              TIMESTAMPTZ,
    budget_spent_pct         NUMERIC(8,4),
    UNIQUE (fingerprint, started_at)          -- [changed] same alert can recur after resolving
);
CREATE INDEX incidents_type_time ON incidents (incident_type, started_at DESC);
CREATE INDEX incidents_open ON incidents (service_id) WHERE resolved_at IS NULL;
