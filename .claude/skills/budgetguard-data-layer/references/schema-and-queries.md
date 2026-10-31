# BudgetGuard — schema and queries (Rev 2 + data-layer additions)

PostgreSQL 18 + TimescaleDB 2.27.x (TSL build). Every timestamp is `TIMESTAMPTZ`.
Additions over the Rev 2 plan are marked **[added]** with the reason.

## Contents

1. DDL — migration `001_schema.sql`
2. Continuous aggregates and policies — `002_timeseries.sql`
3. Seed data — `003_seed.sql`
4. Queries — budget, ETA input, MTTR, DORA, chargeback, decisions, dashboard cards
5. Valkey key and stream catalog
6. Retention and backup

---

## 1. DDL — `001_schema.sql`

```sql
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
```

Why `UNIQUE (fingerprint, started_at)` instead of Rev 2's `UNIQUE (fingerprint)`: an
Alertmanager fingerprint is a hash of the alert's labels, so the *same* burn alert firing again
next week has the same fingerprint. A unique fingerprint would merge two incidents and corrupt
MTTR. `startsAt` from the webhook payload distinguishes occurrences.

`decisions.inputs` holds four keys: `gate_input` (the frozen `GateInput`), `facts` (the raw
`DeployFacts` the backtester replays), `lane_evidence`, and `github` (`repo`,
`installation_id`, `environment`, `callback_url`; an override re-runs the job with them). Storing `facts` at decision time
is what keeps the policy backtest free of look-ahead (see `budgetguard-mlops-evals`).

`eval_runs` (brief and backtest results, keyed by model and prompt SHA) is defined in
`budgetguard-mlops-evals/references/eval-harness.md` §6; add it as migration `004_eval_runs.sql`.

## 2. Continuous aggregates and policies — `002_timeseries.sql`

Run outside a transaction block.

```sql
ALTER TABLE sli_buckets SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'slo_id',
    timescaledb.compress_orderby   = 'bucket_start DESC'
);
SELECT add_compression_policy('sli_buckets', INTERVAL '7 days');
SELECT add_retention_policy('sli_buckets', INTERVAL '400 days');   -- 13 months

CREATE MATERIALIZED VIEW sli_hourly WITH (timescaledb.continuous) AS
SELECT slo_id,
       time_bucket(INTERVAL '1 hour', bucket_start) AS hour,
       sum(total) AS total, sum(bad) AS bad, sum(self_bad) AS self_bad,
       count(*) FILTER (WHERE integrity <> 'ok') AS degraded_buckets
FROM sli_buckets
GROUP BY slo_id, hour
WITH NO DATA;
SELECT add_continuous_aggregate_policy('sli_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '5 minutes');

-- [added] hierarchical daily rollup for reports and the backtester
CREATE MATERIALIZED VIEW sli_daily WITH (timescaledb.continuous) AS
SELECT slo_id,
       time_bucket(INTERVAL '1 day', hour) AS day,
       sum(total) AS total, sum(bad) AS bad, sum(self_bad) AS self_bad,
       sum(degraded_buckets) AS degraded_buckets
FROM sli_hourly
GROUP BY slo_id, day
WITH NO DATA;
SELECT add_continuous_aggregate_policy('sli_daily',
    start_offset => INTERVAL '10 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
```

## 3. Seed — `003_seed.sql`

```sql
INSERT INTO services (name, owner, failure_mode) VALUES
  ('auth-service','team-identity','fail_open'),
  ('orders-service','team-commerce','fail_open'),
  ('inventory-service','team-commerce','fail_open'),
  ('notifications-service','team-messaging','fail_open')
ON CONFLICT (name) DO NOTHING;

-- slos rows are upserted by `bg sync-slos` from slo/*.yaml (spec_hash detects drift).
-- policy_versions: the platform inserts the running policy version on boot (git log -1 --format=%H -- policy/freeze-policy.yaml, baked into the image as BG_POLICY_VERSION):
--   INSERT INTO policy_versions (version) VALUES (:sha) ON CONFLICT DO NOTHING;
```

## 4. Queries

### 4.1 30-day budget remaining per SLO (request-weighted, may be negative)

```sql
SELECT s.name AS service, o.name AS slo, o.objective,
       sum(b.total) AS events,
       100 * (1 - (sum(b.bad) / NULLIF(sum(b.total), 0)) / (1 - o.objective)) AS remaining_pct,
       100 * (1 - (sum(b.self_bad) / NULLIF(sum(b.total), 0)) / (1 - o.objective)) AS remaining_if_self_only_pct
FROM sli_buckets b
JOIN slos o ON o.id = b.slo_id
JOIN services s ON s.id = o.service_id
WHERE b.bucket_start >= now() - INTERVAL '30 days'
GROUP BY s.name, o.name, o.objective;
```

`remaining_if_self_only_pct` is the D-3 view: what the budget would be if dependency-caused
errors were charged to the dependency. Which one the gate reads depends on the policy's
`attribution` switch (see `budgetguard-admission-gate`, "Third flag"): `charge_origin`
(recommended, what D-3 promises) gates on the self-only number; `charge_victim` (Rev 2's
code as written) gates on `remaining_pct`. Dashboards show both, because users felt every
error either way.

### 4.2 ETA input — the last 30 days of buckets, oldest first

```sql
SELECT bucket_start, bad, total
FROM sli_buckets
WHERE slo_id = :slo_id AND bucket_start >= :now - INTERVAL '30 days' AND bucket_start < :now
ORDER BY bucket_start;
```

Fill gaps with zero-total buckets in Python so the deque has exactly 8,640 entries.

### 4.3 MTTR per incident type — median and p90, not mean

```sql
SELECT incident_type,
       count(*) AS incidents,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY resolved_at - started_at) AS mttr_p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY resolved_at - started_at) AS mttr_p90
FROM incidents
WHERE resolved_at IS NOT NULL AND started_at >= now() - INTERVAL '30 days'
GROUP BY incident_type
ORDER BY mttr_p50 DESC;
```

### 4.4 DORA — the four keys, per service, last 30 days

```sql
WITH d AS (
    SELECT * FROM deployments
    WHERE started_at >= now() - INTERVAL '30 days' AND status <> 'in_progress'
),
recovery AS (                       -- failed-deployment recovery time
    SELECT f.service_id,
           min(n.finished_at) - f.finished_at AS recovery
    FROM d f
    JOIN deployments n ON n.service_id = f.service_id
                      AND n.started_at > f.started_at
                      AND n.status = 'success'
                      AND n.is_change_failure IS FALSE
    WHERE f.is_change_failure
    GROUP BY f.id, f.service_id, f.finished_at
)
SELECT s.name,
       count(d.*) / 30.0 AS deploys_per_day,
       round(100.0 * count(*) FILTER (WHERE d.is_change_failure)
             / NULLIF(count(*) FILTER (WHERE d.is_change_failure IS NOT NULL), 0), 1) AS cfr_pct,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY d.finished_at - d.first_commit_at) AS lead_time_p50,
       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r.recovery)
          FROM recovery r WHERE r.service_id = s.id) AS recovery_p50
FROM services s
LEFT JOIN d ON d.service_id = s.id
GROUP BY s.id, s.name;
```

### 4.5 Deploy change rate per week (Grafana time series)

```sql
SELECT time_bucket(INTERVAL '7 days', d.started_at) AS time, s.name AS metric, count(*) AS value
FROM deployments d JOIN services s ON s.id = d.service_id
WHERE $__timeFilter(d.started_at)
GROUP BY 1, 2 ORDER BY 1;
```

### 4.6 Chargeback — self vs dependency share of bad events (D-3)

```sql
SELECT s.name,
       round((100.0 * sum(b.self_bad) / NULLIF(sum(b.bad), 0))::numeric, 1) AS self_share_pct,
       sum(b.bad) - sum(b.self_bad) AS dependency_bad_events
FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
WHERE o.name = 'availability' AND b.bucket_start >= now() - INTERVAL '30 days'
GROUP BY s.name;
```

`bad` and `self_bad` are `DOUBLE PRECISION`, and PostgreSQL has no `round(double precision,
integer)`, so the cast to `numeric` is required (Rev 2 §5 query 4 has the same bug; found by
running every query in this file against `timescale/timescaledb:2.27.0-pg18` and `2.31.0-pg18`
on 25 Oct 2026).

### 4.7 Pricing inputs — deploy history per service

```sql
SELECT count(*) FILTER (WHERE is_change_failure IS NOT NULL) AS deploys,
       count(*) FILTER (WHERE is_change_failure) AS failures,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY change_failure_budget_pct)
           FILTER (WHERE is_change_failure) AS median_failure_cost_pct
FROM deployments
WHERE service_id = :service_id AND started_at >= now() - INTERVAL '180 days';
```

### 4.8 Rollback-lane proof

```sql
SELECT id, sha, finished_at
FROM deployments
WHERE service_id = :service_id AND service_tree_sha = :tree_sha
  AND status = 'success' AND is_change_failure IS FALSE
ORDER BY finished_at DESC LIMIT 1;
```

### 4.9 Change-failure marking (job, runs every 5 min)

```sql
-- deploys whose 60-minute window just closed: failure if a self-inflicted page fired in it
UPDATE deployments d
SET is_change_failure = EXISTS (
        SELECT 1 FROM incidents i
        WHERE i.service_id = d.service_id
          AND i.severity = 'page'
          AND (i.culprit_service_id IS NULL OR i.culprit_service_id = d.service_id)
          AND i.started_at BETWEEN d.finished_at AND d.finished_at + INTERVAL '60 minutes'),
    change_failure_budget_pct = (
        SELECT 100 * (sum(b.self_bad) / NULLIF(sum(b.total), 0)) / (1 - max(o.objective)) / 720   -- one availability SLO per service; max() keeps it a valid aggregate
        FROM sli_buckets b JOIN slos o ON o.id = b.slo_id
        WHERE o.service_id = d.service_id AND o.name = 'availability'
          AND b.bucket_start BETWEEN d.finished_at AND d.finished_at + INTERVAL '60 minutes')
WHERE d.is_change_failure IS NULL
  AND d.status = 'success'
  AND d.finished_at < now() - INTERVAL '60 minutes';
```

The budget-cost expression: self-inflicted error ratio in the hour ÷ allowed ratio = burn;
burn × 1 h ÷ 720 h = fraction of the 30-day budget; × 100 = percent. Example: 99.9% SLO,
2% self errors for the hour → burn 20 → 20/720 = 2.78% of the budget. This is the
`budget_spent(b, t_hours)` function from budgetguard-slo-math; keep the SQL and Python in
agreement with a test that runs both on the same fixture.

**As built (W6, `workers/deploy_events.py`):** the query above blames a deploy for any page in its
window, including a dependency's outage (B-15), and `incidents.culprit_service_id` is never set. The
repo's version requires the page **and** `sum(self_bad) / sum(bad) >= self_page_share` over the window,
uses a half-open window `[finished_at, finished_at + 60 min)`, and stores the self-inflicted cost for
every settled deploy (the pricing median reads failures only). A test checks the stored cost against
`100 * budget_spent(burn, 1)`.

### 4.10a Override-lane proof (on the re-run's evaluation)

```sql
SELECT o.id
FROM overrides o
JOIN decisions blocked ON blocked.id = o.decision_id
WHERE blocked.service_id = :service_id AND blocked.sha = :sha
  AND o.expires_at > now() AND o.consumed_by_decision_id IS NULL
ORDER BY o.created_at DESC
LIMIT 1
FOR UPDATE SKIP LOCKED;          -- two concurrent re-runs cannot both consume it
```

Then, in the same transaction as the new decision insert:
`UPDATE overrides SET consumed_by_decision_id = :new_decision_id WHERE id = :override_id`.

### 4.10 Decision explorer (dashboard)

```sql
SELECT d.id, s.name AS service, d.sha, d.verdict, d.reason_code, d.price_pct, d.affordable_pct,
       d.canary_profile, d.decided_at, d.policy_version, d.inputs_hash,
       o.approver AS overridden_by, o.reason AS override_reason
FROM decisions d
JOIN services s ON s.id = d.service_id
LEFT JOIN overrides o ON o.decision_id = d.id
WHERE (:service IS NULL OR s.name = :service)
ORDER BY d.decided_at DESC
LIMIT 50;
```

## 5. Valkey key and stream catalog

| Key / stream | Type | TTL / retention | Writer → reader | Purpose |
| --- | --- | --- | --- | --- |
| `bg:verdict:{service}:{gen}:{sha}` | string (JSON decision) | 30 s | gate worker → gate worker | Re-runs get the same answer without re-querying Prometheus |
| `bg:verdict-gen:{service}` | integer (`INCR`) | none | Alertmanager webhook → gate worker | Invalidation: any alert bumps it, orphaning every cached verdict for the service (no `SCAN`) |
| `bg:ghuser:{sha256(token)}` | string (GitHub login) | 5 min | overrides API | Resolve the approver without calling GitHub on every click; never stores the token |
| `bg:lock:eval:{service}` | `SET NX PX 15000` | 15 s | gate worker | Single-flight per service |
| `bg:delivery:{github_delivery_id}` | `SET NX EX 604800` | 7 days | webhook handler | Fast-path dedupe (Postgres `UNIQUE` is the backstop) |
| `bg:promq:{sha256(query,time)}` | string | 15 s | dashboard API | Dashboard query cache; the gate bypasses it |
| `bg:gate-evaluations` | stream, group `gate-workers` | `MAXLEN ~ 10000` | webhook → gate worker | Evaluation jobs; `XAUTOCLAIM` after 60 s |
| `bg:deploy-events` | stream, group `deploy-recorders` | `MAXLEN ~ 10000` | `deployment_status` webhook → recorder | Deploy rows, lead time, change-failure timers |
| `bg:briefs` | stream, group `brief-writers` | `MAXLEN ~ 10000` | gate worker → brief writer | Freeze Brief rendering + Slack/PR posting |
| `bg:records-pending` | stream | `MAXLEN ~ 10000` | gate worker (DB down) → replayer | Decisions buffered while PostgreSQL is unavailable |
| `bg:llm:spend:{YYYYMM}` | string (float, `INCRBYFLOAT`) | 40 days | brief writer | Monthly LLM spend; at `monthly_budget_usd` the writer serves the template |

Naming rule: every key starts `bg:`, segments separated by `:`, no user input in key names
except validated service names and hex SHAs.

## 6. Retention and backup

| Data | Retention | Mechanism |
| --- | --- | --- |
| Prometheus samples | 35 days | `--storage.tsdb.retention.time=35d` |
| `sli_buckets` | 400 days | retention policy; compressed after 7 days |
| `sli_hourly`, `sli_daily` | indefinite (small) | — |
| decisions, deployments, incidents, overrides | indefinite | audit trail |
| Backup | nightly `pg_dump -Fc` to object storage + WAL archiving | CronJob in `data` namespace; restore drill monthly |
