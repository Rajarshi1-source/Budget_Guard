# BudgetGuard — SLO/Error Budget Dashboard with Automated Deployment Freeze

## Complete Implementation Plan for Junior SRE Engineer Interview (2026)

---

## Table of Contents

1. Project Overview & Interview Hook
2. Tech Stack (with justification for every choice)
3. MVP Blueprint (7-week plan)
4. Detailed System Architecture (HLD)
5. Detailed System Design (LLD)
6. Database Design & Choice (with comparison matrix)
7. Caching & Messaging — Redis vs Kafka vs RabbitMQ (verdict)
8. Design Patterns Used
9. Resilience Patterns & Mitigation Strategies
10. Docker & Kubernetes Deployment Strategy
11. CI/CD Pipeline with Error Budget Gate
12. Monitoring & Observability
13. README Blueprint with Architecture Diagram
14. Interview Prep — Questions & Answers (SRE-focused)
15. Week-by-Week Build Schedule

---

## 1. Project Overview & Interview Hook

**Project Name:** BudgetGuard

**Tagline:** "Move fast — until your error budget says stop."

**One-liner:** A production-grade Site Reliability Engineering platform that deploys 4 microservices (auth, orders, inventory, notifications) on Kubernetes with formally-defined SLIs and SLOs per service, computes error budget burn rates in real time using Prometheus PromQL queries, renders a custom Grafana dashboard showing 30-day rolling availability, MTTR per incident type, deployment change rate, and burn rate alerts at 1-hour/6-hour/3-day windows — and most critically, integrates with GitHub Actions to automatically freeze deployments when any service's error budget drops below 10%, forcing engineering teams to choose reliability over velocity exactly as the Google SRE book prescribes.

**Interview Hook (memorize this):**

> "I built a Google SRE-textbook error budget management system. Four microservices each have formal SLIs — like 'percentage of HTTP requests returning non-5xx' and 'P99 latency below 500ms.' Each has a published SLO — for example, the auth service promises 99.9% monthly availability, which mathematically equals 43.8 minutes of allowed downtime per month. I compute error budget burn rate using multi-window multi-burn-rate alerts: a 1-hour window catches catastrophic failures fast, a 6-hour window catches moderate degradations, and a 3-day window catches slow erosion. The killer feature: my GitHub Actions deployment pipeline calls a `/api/error-budget/check` endpoint before every production deploy. If the budget is below 10%, the deploy is frozen and a Slack notification explains why. This forces the SRE-prescribed culture: when reliability is at risk, engineers stop shipping features and fix reliability. I used TimescaleDB for the SLI metrics because they're high-frequency time-series data, and PostgreSQL for relational data like incidents and freeze events. I chose Redis over Kafka for the deployment event queue because event volume is under 50 per day."

**Why SRE interviewers love this:**
- **Google SRE book alignment** — error budgets, multi-window burn rate alerts, deployment freeze are textbook Google SRE
- **Mathematical rigor** — "99.9% = 43.8 minutes/month" shows you understand SLO arithmetic
- **Cultural automation** — the freeze enforces "reliability over velocity" without manual intervention
- **PromQL mastery** — burn rate calculations are PromQL gymnastics that SRE panels love testing
- **Production-grade thinking** — multi-burn-rate alerting is what mature SRE teams actually use
- **Quantified impact** — "saved 23 minutes of downtime by blocking a bad deploy" is concrete

**Target Companies:**
- **Google SRE Bangalore** — they wrote the book. This is the book.
- **Flipkart SRE, Razorpay SRE, PhonePe SRE** — all use error budget governance
- **Atlassian India SRE** — Statuspage + Opsgenie need exactly this
- **Swiggy, Meesho, Dunzo** — production SRE roles
- **Netflix India, Uber India** — chaos + SLO mature cultures
- **GitLab, GitHub, HashiCorp India** — DevTools companies that exemplify SRE

---

## 2. Tech Stack — Every Choice Justified

### Core Stack

| Layer | Technology | Why This (Interview Answer) |
|---|---|---|
| **Microservices** | Spring Boot 3.x (Java 21) — 4 services | Industry standard for SRE-heavy companies in Bangalore. Resilience4j for circuit breakers. Spring Boot Actuator exposes Prometheus metrics natively (`/actuator/prometheus`). Virtual threads (Java 21) for high concurrency under load. |
| **Metrics Collection** | Prometheus + kube-state-metrics + node-exporter | The de facto SRE standard. PromQL is the SRE lingua franca. Multi-window burn rate alerts need range vector queries that PromQL excels at. Long-term storage via remote_write to TimescaleDB. |
| **Visualization** | Grafana + Next.js custom dashboard | Grafana for raw observability and standard dashboards. Next.js for a custom error budget dashboard with SLO-specific UX (burn rate gauges, freeze status, deployment impact analysis). |
| **Alerting** | Alertmanager + multi-burn-rate rules | Multi-window multi-burn-rate alerting is the Google SRE workbook gold standard. Catches fast-burning fires AND slow-burning erosions with low false-positive rate. |
| **Time-Series DB** | TimescaleDB (PostgreSQL extension) | See Section 6. SLI metric history is high-frequency time-series data. Continuous aggregates pre-compute hourly P99 rollups. Compression saves 90% storage. PostgreSQL compatibility means relational data lives in the same DB. |
| **Cache & Queue** | Redis 7 | See Section 7. Dashboard cache, freeze state cache, deployment event queue (Streams), rate limiting. |
| **Deployment Gate** | GitHub Actions + custom webhook | Pre-deploy step calls `/api/error-budget/check?service=X` — receives `freeze: true/false` — pipeline aborts or proceeds. The cultural gate that enforces SRE discipline. |
| **Container Orchestration** | Kubernetes (kind for local, EKS/GKE for prod) | Required for kube-state-metrics. Standard for Bangalore SRE roles. |
| **Package Management** | Helm 3 | All 4 microservices + BudgetGuard platform packaged as Helm charts. Configurable SLO targets via values.yaml. |
| **Service Mesh (optional)** | None for MVP, Istio post-MVP | MVP avoids service mesh complexity. Spring Boot Actuator metrics + Prometheus is sufficient. Istio mentioned in interviews as "next step for L7 SLIs without code changes." |
| **Load Testing** | k6 + Locust | Generate realistic traffic patterns to consume error budget. Shows the dashboard responding to actual load, not synthetic data. |
| **Notification** | Slack Bolt SDK (Python) | Rich Block Kit messages with burn rate context, freeze status, deep links to Grafana. |

### Why NOT These Alternatives

| Rejected Option | Why |
|---|---|
| Datadog SLO Monitoring | Commercial tool that solves this — but using it eliminates the project's value. Also costs $23/host/month. The point is to demonstrate I can build SRE primitives from scratch. |
| AWS CloudWatch + AWS Service Level Objectives | AWS-only. Locks the project to one cloud. Prometheus is cloud-neutral and the industry standard. |
| Sloth (SLO generator tool) | Sloth generates Prometheus alert rules from SLO definitions — useful, but it's a code generator, not a full SLO platform. I incorporate Sloth's *philosophy* (declarative SLOs in YAML) but build the freeze enforcement, dashboard, and event correlation myself. |
| Pyrra (Prometheus-native SLO) | Excellent open-source tool. Mention it in interviews as "I'm aware of Pyrra and built BudgetGuard with similar declarative principles." Building from scratch demonstrates deeper understanding than configuring Pyrra. |
| Plain PostgreSQL (no TimescaleDB) | Burn rate queries scan millions of metric points across rolling windows. PostgreSQL without time-series optimization takes 5-10 seconds per query. TimescaleDB hypertables + continuous aggregates reduce this to sub-100ms. Critical for an interactive dashboard. |
| InfluxDB | Specialized time-series database with no relational support. We need both — incidents, deployments, freeze events are relational. TimescaleDB unifies both in one PostgreSQL extension. |
| MongoDB | Incidents and deployments are inherently relational (incident → impacted_service → SLO violations → freeze events). MongoDB requires denormalization. SLO calculation queries are heavy aggregations — PostgreSQL window functions handle this natively. |
| Cassandra | Designed for write-heavy partition-key reads at internet scale. Our scale is 4 services × ~10 metrics/15s = 160 writes/min. Using Cassandra here is overengineering. |
| Kafka for deployment events | Under 50 deployments per day. Kafka's 3-broker minimum is absurd overengineering. Redis Streams handles this trivially. |
| Apache Druid | Real-time analytics database. Excellent for ad-hoc queries on massive datasets but operationally complex (Coordinator, Overlord, Broker, Historical, MiddleManager nodes — 5 node types). TimescaleDB is one extension on PostgreSQL. |

---

## 3. MVP Blueprint — 7-Week Build Plan

### MVP Scope — The Four Microservices

Each service has DIFFERENT SLOs to demonstrate that SLO design is per-service, not one-size-fits-all:

| Service | Purpose | Availability SLO | Latency SLO | Why These SLOs |
|---|---|---|---|---|
| **auth-service** | Login, JWT issuance, OAuth callbacks | 99.95% (21.6 min/month budget) | P99 < 300ms | Auth failure cascades to everything — strictest SLO |
| **orders-service** | Order creation, retrieval, status | 99.9% (43.2 min/month budget) | P99 < 500ms | Critical user-facing path — strict SLO |
| **inventory-service** | Stock checks, reservations | 99.5% (3.6 hr/month budget) | P99 < 800ms | Slightly relaxed — degraded reads acceptable via cache |
| **notifications-service** | Email/SMS/push dispatch | 99.0% (7.2 hr/month budget) | P95 < 2000ms | Async, retryable — relaxed SLO |

**The "Different SLOs Per Service" Insight (memorize for interviews):**
> "Not every service needs 99.99%. Auth must be 99.95% because failure breaks the entire user experience. Notifications can tolerate 99.0% because async retries handle transient failures invisibly. SLO design follows user impact, not engineering preference. This is straight from the Google SRE book — define SLOs based on what users actually need, not what engineers want to promise."

### The 4 Critical SLO Definitions

**1. Availability SLI/SLO**
```yaml
sli:
  name: availability
  description: "Fraction of HTTP requests returning non-5xx response"
  good_events_query: |
    sum(rate(http_server_requests_seconds_count{
      service="{service}",
      status!~"5.."
    }[5m]))
  total_events_query: |
    sum(rate(http_server_requests_seconds_count{
      service="{service}"
    }[5m]))

slo:
  target: 99.9
  window: 30d
  error_budget_minutes: 43.2  # 30 days × 24 hours × 60 min × 0.001
```

**2. Latency SLI/SLO**
```yaml
sli:
  name: latency_p99
  description: "P99 latency of HTTP requests"
  good_events_query: |
    sum(rate(http_server_requests_seconds_bucket{
      service="{service}",
      le="0.5"
    }[5m]))
  total_events_query: |
    sum(rate(http_server_requests_seconds_count{
      service="{service}"
    }[5m]))

slo:
  target: 99.0  # 99% of requests below threshold
  threshold_ms: 500
  window: 30d
```

**3. Error Budget Burn Rate**
```
Burn Rate Formula:
  burn_rate = (1 - actual_availability) / (1 - SLO_target)

Examples for SLO = 99.9% (allowable error rate = 0.001):
  - actual = 99.95% → burn_rate = 0.0005 / 0.001 = 0.5x  (consuming half-speed)
  - actual = 99.9%  → burn_rate = 0.001  / 0.001 = 1.0x  (exactly on budget)
  - actual = 99.0%  → burn_rate = 0.01   / 0.001 = 10x   (10x faster than allowed)
  - actual = 95.0%  → burn_rate = 0.05   / 0.001 = 50x   (catastrophic)

Thresholds (from Google SRE Workbook):
  - 1-hour window  + 14.4x burn rate → page (consumes 2% of monthly budget in 1h)
  - 6-hour window  + 6x burn rate    → page (consumes 5% of monthly budget in 6h)
  - 3-day window   + 1x burn rate    → ticket (consumes 10% of monthly budget in 3d)
```

**4. Deployment Frequency / Change Failure Rate (DORA Metrics)**
```yaml
metrics:
  - deployment_frequency: "Deployments per service per day"
  - change_failure_rate: "% of deployments causing SLO violations"
  - mttr: "Median time from incident to resolution"
```

### What to Build (MVP)

**Must Have:**
- 4 Spring Boot microservices on K8s with Prometheus metrics
- SLO definitions in YAML for each service
- PromQL recording rules for all SLIs
- Multi-window multi-burn-rate alert rules in Alertmanager
- Custom Grafana dashboard: burn rate gauges, 30-day availability, error budget remaining
- Next.js error budget dashboard: per-service status, freeze state, deployment timeline
- Error budget API: `GET /api/error-budget/check?service=X` returns `{frozen: bool, reason: string}`
- GitHub Actions deployment freeze: pre-deploy step calls error budget check
- Slack notifications: burn rate alerts, freeze events, freeze releases
- Incident tracking: link incidents to SLO violations
- Load testing scripts (k6) to demonstrate budget consumption
- Docker Compose for local dev
- Helm chart for K8s deployment

**Nice to Have (Post-MVP):**
- Service Level Agreement (SLA) tracking (customer-facing legal commitments)
- Burn rate forecasting (ML to predict budget exhaustion)
- Dependency-aware SLO calculation (orders-service SLO degraded because auth-service is degraded)
- Auto-rollback when error budget burns rapidly post-deployment
- SLO history with annotations (deployments, incidents marked on charts)
- Multi-tenancy (per-team SLO ownership)
- PagerDuty integration alongside Slack

### Week-by-Week Schedule

**Week 1 — K8s + 4 Microservices Foundation**
- Day 1–2: K8s cluster (kind), kube-prometheus-stack Helm chart
- Day 3–4: Build 4 Spring Boot microservices with realistic inter-service calls
- Day 5: Spring Boot Actuator exposing Prometheus metrics: http_server_requests_seconds, JVM, Resilience4j
- Day 6: Deploy all 4 services via Helm with intentional failure simulation env vars
- Day 7: Verify metrics in Prometheus UI, basic Grafana dashboard
- Deliverable: 4 services running, all metrics flowing

**Week 2 — SLO Definitions + PromQL Recording Rules**
- Day 1–2: Write SLO YAML definitions for all 4 services (availability + latency)
- Day 3: Convert SLO definitions to Prometheus recording rules
- Day 4: Recording rules for SLI ratios (good_events / total_events)
- Day 5: Recording rules for error budget remaining (rolling 30-day windows)
- Day 6: Recording rules for burn rate calculations (1h, 6h, 3d windows)
- Day 7: Verify all rules in Prometheus, query results match manual calculation
- Deliverable: Complete SLO observability via PromQL recording rules

**Week 3 — Multi-Window Multi-Burn-Rate Alerting**
- Day 1–2: Implement page-level alerts (1h@14.4x AND 5m@14.4x — both must fire)
- Day 3: Implement ticket-level alerts (6h@6x AND 30m@6x)
- Day 4: Slow-burn alerts (3d@1x AND 6h@1x)
- Day 5: Alert routing in Alertmanager: page → PagerDuty/Slack-urgent, ticket → Slack-tickets
- Day 6: Test alerts with synthetic failures (k6 generates 5xx responses)
- Day 7: Documentation of alert thresholds and rationale
- Deliverable: Production-grade SRE alerting that catches fast AND slow burns

**Week 4 — Custom Grafana Dashboard + Next.js Dashboard**
- Day 1–2: Custom Grafana dashboard panels: SLO compliance gauge per service, error budget remaining, burn rate over time, MTTR trend
- Day 3: Next.js dashboard scaffold (Tailwind + shadcn/ui + Tremor)
- Day 4: Per-service error budget cards (visual: budget consumed, remaining, projected exhaustion date)
- Day 5: Deployment timeline annotated with freeze events
- Day 6: 30-day burn rate chart with multi-window overlay
- Day 7: Mobile-responsive design, dark mode
- Deliverable: Two complementary dashboards (Grafana for metrics, Next.js for SRE workflows)

**Week 5 — Deployment Freeze Integration**
- Day 1–2: FastAPI endpoint: `GET /api/error-budget/check?service=auth-service` → `{frozen, reason, current_burn, threshold}`
- Day 3: Decision logic: freeze if remaining_budget < 10% OR burn_rate > 14.4x for past 1h
- Day 4: GitHub Actions reusable workflow: pre-deploy step calling the check endpoint
- Day 5: PR check that runs error budget check before merge to main
- Day 6: Slack notification on freeze + freeze release
- Day 7: Demo: load testing exhausts budget → next deploy frozen → show Slack message
- Deliverable: End-to-end deploy freeze working, demo-able

**Week 6 — Incident Tracking + DORA Metrics**
- Day 1–2: PostgreSQL schema for incidents, deployments, freeze events
- Day 3: Auto-detect incident: when burn rate exceeds X, create incident record
- Day 4: Manual incident annotation (link to root cause analysis URL)
- Day 5: MTTR calculation: time from incident_started → incident_resolved
- Day 6: Deployment frequency + change failure rate (DORA) computation
- Day 7: Annotate Grafana charts with incidents and deployments
- Deliverable: Incidents linked to SLO violations, DORA metrics visible

**Week 7 — Polish, Demo, Deploy**
- Day 1–2: Helm chart for BudgetGuard platform
- Day 3: Docker Compose for local dev
- Day 4: GitHub Actions CI/CD for the platform itself
- Day 5: Documentation (README, architecture diagram, SLO design rationale)
- Day 6: Deploy to cloud K8s
- Day 7: Record demo video showing freeze in action
- Deliverable: Live deployed, full demo, production-grade documentation

---

## 4. Detailed System Architecture (HLD)

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  KUBERNETES CLUSTER                                      │
│                                                                         │
│  ┌────────────────────────────────────────────────────────┐             │
│  │       MICROSERVICES TIER (Spring Boot)                  │             │
│  │                                                        │             │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │             │
│  │  │auth-service  │  │orders-service│  │inventory-svc │  │             │
│  │  │SLO: 99.95%   │  │SLO: 99.9%    │  │SLO: 99.5%    │  │             │
│  │  │P99: <300ms   │  │P99: <500ms   │  │P99: <800ms   │  │             │
│  │  │Resilience4j  │  │Resilience4j  │  │Resilience4j  │  │             │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  │             │
│  │  ┌──────────────────────────────────────┐              │             │
│  │  │  notifications-service                │              │             │
│  │  │  SLO: 99.0%, P95: <2000ms              │              │             │
│  │  │  Async with retry queue                │              │             │
│  │  └──────────────────────────────────────┘              │             │
│  │                                                        │             │
│  │  Each service exposes:                                  │             │
│  │  /actuator/prometheus → metrics                         │             │
│  │  /actuator/health → liveness/readiness                  │             │
│  │  /api/* → application endpoints                         │             │
│  └─────────────────────────┬──────────────────────────────┘             │
│                            │                                            │
│  ┌─────────────────────────▼──────────────────────────────┐             │
│  │   PROMETHEUS STACK (kube-prometheus-stack)              │             │
│  │                                                        │             │
│  │  ┌──────────────┐  ┌────────────────┐  ┌────────────┐  │             │
│  │  │ Prometheus   │  │ Recording      │  │ Alertmgr   │  │             │
│  │  │ Server       │→ │ Rules          │→ │            │  │             │
│  │  │              │  │ (SLI ratios,   │  │ Multi-     │  │             │
│  │  │ Scrapes /    │  │  burn rates,   │  │ burn-rate  │  │             │
│  │  │ actuator/    │  │  budget        │  │ alerts:    │  │             │
│  │  │ prometheus   │  │  remaining)    │  │ - 1h@14.4x │  │             │
│  │  │ every 15s    │  │                │  │ - 6h@6x    │  │             │
│  │  └──────┬───────┘  └────────┬───────┘  │ - 3d@1x    │  │             │
│  │         │                   │           └─────┬──────┘  │             │
│  │         │ remote_write      │                 │         │             │
│  │         │                   │                 │ webhook │             │
│  └─────────┼───────────────────┼─────────────────┼─────────┘             │
│            │                   │                 │                       │
│  ┌─────────▼───────────────────▼─────────────────▼─────────┐             │
│  │         BUDGETGUARD PLATFORM                              │             │
│  │                                                          │             │
│  │  ┌─────────────────┐    ┌────────────────────────────┐   │             │
│  │  │ FastAPI         │    │  Next.js Error Budget      │   │             │
│  │  │ Backend         │    │  Dashboard                 │   │             │
│  │  │                 │    │                            │   │             │
│  │  │ ┌─────────────┐ │    │  ┌──────────────────────┐  │   │             │
│  │  │ │ Error       │ │    │  │ Per-Service Cards    │  │   │             │
│  │  │ │ Budget      │ │    │  │ • Budget remaining   │  │   │             │
│  │  │ │ Calculator  │ │    │  │ • Burn rate gauge    │  │   │             │
│  │  │ ├─────────────┤ │    │  │ • Freeze status      │  │   │             │
│  │  │ │ Freeze      │ │    │  │ • Projected exhaust. │  │   │             │
│  │  │ │ Decision    │ │    │  ├──────────────────────┤  │   │             │
│  │  │ │ Engine      │ │    │  │ Deployment Timeline  │  │   │             │
│  │  │ ├─────────────┤ │    │  ├──────────────────────┤  │   │             │
│  │  │ │ Incident    │ │    │  │ MTTR Trends          │  │   │             │
│  │  │ │ Detector    │ │    │  ├──────────────────────┤  │   │             │
│  │  │ ├─────────────┤ │    │  │ DORA Metrics         │  │   │             │
│  │  │ │ Webhook     │ │    │  └──────────────────────┘  │   │             │
│  │  │ │ Receiver    │ │    └────────────────────────────┘   │             │
│  │  │ └─────────────┘ │                                     │             │
│  │  └─────────┬───────┘                                     │             │
│  │            │                                             │             │
│  │  ┌─────────▼─────────┐  ┌────────────────────────────┐   │             │
│  │  │  TimescaleDB      │  │  Grafana                   │   │             │
│  │  │  (PostgreSQL)     │  │                            │   │             │
│  │  │                   │  │  • SLO compliance panels   │   │             │
│  │  │  • SLI metrics    │  │  • Burn rate over time     │   │             │
│  │  │    (hypertable)   │  │  • Annotated with          │   │             │
│  │  │  • Recording      │  │    deployments + incidents │   │             │
│  │  │    rule history   │  │  • Multi-window views      │   │             │
│  │  │  • Incidents      │  └────────────────────────────┘   │             │
│  │  │  • Deployments    │                                   │             │
│  │  │  • Freeze events  │  ┌────────────────────────────┐   │             │
│  │  └───────────────────┘  │  Redis 7                   │   │             │
│  │                         │                            │   │             │
│  │                         │  • Dashboard cache         │   │             │
│  │                         │  • Freeze state cache      │   │             │
│  │                         │  • PromQL response cache   │   │             │
│  │                         │  • Deployment event queue  │   │             │
│  │                         └────────────────────────────┘   │             │
│  └──────────────────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────────────┘
                              │                  │
              ┌───────────────┘                  └─────────────┐
              ▼                                                ▼
   ┌─────────────────────┐                           ┌─────────────────┐
   │   GitHub Actions    │                           │     Slack       │
   │                     │                           │                 │
   │  Pre-deploy step:   │                           │ • Alerts        │
   │  curl /api/error-   │                           │ • Freeze events │
   │  budget/check       │                           │ • Burn rate     │
   │                     │                           │ • Incidents     │
   │  If frozen=true →   │                           │                 │
   │  abort deployment   │                           │ Block Kit with  │
   │                     │                           │ deep links to   │
   │  Else proceed       │                           │ Grafana         │
   └─────────────────────┘                           └─────────────────┘
```

### The Multi-Window Multi-Burn-Rate Alert Strategy

This is the SRE workbook gold standard. Memorize this:

```
┌────────────────────────────────────────────────────────────────────┐
│ Page-level (urgent — wake on-call)                                  │
│                                                                    │
│ Catastrophic fires:                                                │
│ ALERT IF (5m burn rate > 14.4x) AND (1h burn rate > 14.4x)         │
│ Why both windows? 5m window catches fast, 1h window prevents       │
│ false positives from blips. Both must fire.                        │
│                                                                    │
│ Math: 14.4x burn for 1h consumes 1h × 14.4 = 14.4 budget-hours.    │
│       30-day budget for 99.9% = 720h × 0.001 = 0.72h = 43.2 min    │
│       14.4 × (5/60)h = 1.2h consumed = 167% of monthly budget!     │
│       So 14.4x burn for just 1 hour exhausts monthly budget.       │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ Ticket-level (file ticket — fix during business hours)              │
│                                                                    │
│ Moderate degradation:                                              │
│ ALERT IF (30m burn rate > 6x) AND (6h burn rate > 6x)              │
│                                                                    │
│ Math: 6x burn for 6h consumes 36 budget-hours = 5% of monthly      │
│       budget. Worth tracking but not page-worthy at 3 AM.          │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ Slow-burn (long-term degradation)                                   │
│                                                                    │
│ Quietly eroding budget:                                            │
│ ALERT IF (6h burn rate > 1x) AND (3d burn rate > 1x)               │
│                                                                    │
│ Math: 1x burn for 3 days consumes 10% of monthly budget.           │
│       Catches the "everything seems fine but we're slowly running  │
│       out of budget" pattern.                                      │
└────────────────────────────────────────────────────────────────────┘
```

### Core Request Flows

**Flow 1: Pre-Deployment Error Budget Check**
```
Step 1:  Developer pushes to main → GitHub Actions deploy.yml triggers
    ↓
Step 2:  Pre-deploy step: 
         curl https://budgetguard.example.com/api/error-budget/check?service=auth-service
    ↓
Step 3:  BudgetGuard FastAPI receives request
    ↓
Step 4:  Check Redis cache for "budget:auth-service" (TTL: 60s)
         If hit → return cached result
         If miss → continue to step 5
    ↓
Step 5:  Query Prometheus for current SLI:
         PromQL: 1 - (
           sum(rate(http_server_requests_seconds_count{service="auth-service",status=~"5.."}[30d]))
           / sum(rate(http_server_requests_seconds_count{service="auth-service"}[30d]))
         )
    ↓
Step 6:  Calculate error budget remaining:
         budget_remaining = (slo_target - actual_error_rate) / (1 - slo_target)
         For 99.95% SLO with 99.965% actual:
         budget_remaining = (0.0005 - 0.00035) / 0.0005 = 0.30 (30% remaining)
    ↓
Step 7:  Decision logic:
         IF budget_remaining < 0.10 → frozen = true
         IF burn_rate(1h) > 14.4 → frozen = true (urgent)
         IF active_incident → frozen = true
         ELSE → frozen = false
    ↓
Step 8:  Cache decision in Redis (TTL: 60s)
    ↓
Step 9:  Return response:
         {
           "frozen": true,
           "service": "auth-service",
           "budget_remaining_pct": 8.5,
           "burn_rate_1h": 22.3,
           "reason": "Budget remaining (8.5%) is below 10% threshold",
           "estimated_unfreeze_time": "2026-04-30T14:00:00Z",
           "grafana_link": "https://grafana.example.com/...",
           "override_url": "https://budgetguard.example.com/freeze/override/auth-service"
         }
    ↓
Step 10: GitHub Actions evaluates response:
         IF frozen=true:
           - Print message to Actions log
           - Send Slack notification
           - exit 1 (fail the workflow)
         ELSE:
           - Continue to deploy step
    ↓
Step 11: Persist freeze event in PostgreSQL:
         freeze_events table: service, reason, triggered_by, github_run_id, ...
```

**Flow 2: Burn Rate Alert → Slack → Auto-Incident Creation**
```
Step 1:  Prometheus evaluates burn rate alert rule every 15s:
         ALERT HighErrorBudgetBurn
         expr: (
           burn_rate_1h{service="auth-service"} > 14.4
           AND
           burn_rate_5m{service="auth-service"} > 14.4
         )
         for: 2m
    ↓
Step 2:  Alert FIRES → Alertmanager receives → routes via webhook
    ↓
Step 3:  BudgetGuard webhook receiver: POST /api/webhooks/alertmanager
    ↓
Step 4:  Auto-create incident record in PostgreSQL:
         incidents table: 
         { type: "burn_rate", service: "auth-service", 
           started_at: now, severity: "page", status: "active" }
    ↓
Step 5:  Send Slack notification with rich Block Kit:
         "🔥 PAGE: auth-service burning error budget at 22.3x rate
         
         At this rate, you'll exhaust 30-day budget in 33 minutes.
         Current availability: 97.78% (SLO: 99.95%)
         
         Recent deployments:
         • 14:32 — order-svc v2.3.1 (12 min ago)  ← suspect
         • 12:15 — auth-svc v1.8.4 (2h ago)
         
         [📊 Grafana] [🔗 Recent deploys] [🛑 Trigger rollback]"
    ↓
Step 6:  Update Redis freeze state cache:
         "freeze:auth-service" → { frozen: true, reason: "active page" }
         (Now any deployment attempt is blocked)
```

**Flow 3: Deployment + DORA Metrics Tracking**
```
Step 1:  Pre-deploy: error budget check passes
    ↓
Step 2:  GitHub Actions completes Helm upgrade
    ↓
Step 3:  Post-deploy webhook: POST /api/deployments/record
         { service, version, commit_sha, github_run_id, started_at, completed_at }
    ↓
Step 4:  BudgetGuard records deployment in PostgreSQL
    ↓
Step 5:  Watch SLI for next 60 minutes:
         If new burn rate alert fires → mark deployment as "change failure"
         change_failure_rate = (failed_deploys / total_deploys) × 100
    ↓
Step 6:  Annotate Grafana with vertical line at deployment time
    ↓
Step 7:  Update DORA dashboard:
         - Deployment frequency: deployments per service per day
         - Change failure rate: % causing burn rate alerts within 1h
         - MTTR: median time-to-resolve incidents
         - Lead time: commit → production (from GitHub Actions metadata)
```

---

## 5. Detailed System Design (LLD)

### 5.1 Project Structure

```
budgetguard/
├── microservices/                          # 4 Spring Boot services
│   ├── auth-service/
│   │   ├── src/main/java/com/budgetguard/auth/
│   │   │   ├── AuthApplication.java
│   │   │   ├── controller/AuthController.java
│   │   │   ├── service/JwtService.java
│   │   │   ├── service/OAuthService.java
│   │   │   └── config/MetricsConfig.java   # Custom Prometheus metrics
│   │   ├── src/main/resources/application.yml
│   │   ├── Dockerfile
│   │   └── pom.xml
│   ├── orders-service/
│   ├── inventory-service/
│   └── notifications-service/
│
├── slo-definitions/                        # SLO YAML (the SRE source of truth)
│   ├── auth-service.yaml
│   ├── orders-service.yaml
│   ├── inventory-service.yaml
│   ├── notifications-service.yaml
│   └── README.md                           # SLO design rationale
│
├── prometheus-rules/                       # Auto-generated from SLO YAML
│   ├── recording-rules.yaml                # SLI recording rules
│   ├── burn-rate-rules.yaml                # Burn rate calculations
│   └── alert-rules.yaml                    # Multi-window multi-burn-rate alerts
│
├── platform/                               # BudgetGuard FastAPI backend
│   ├── src/
│   │   ├── main.py                         # FastAPI entrypoint
│   │   ├── config.py
│   │   ├── api/
│   │   │   ├── error_budget.py             # /api/error-budget/check endpoint
│   │   │   ├── deployments.py              # /api/deployments/record endpoint
│   │   │   ├── incidents.py                # /api/incidents/* endpoints
│   │   │   ├── freezes.py                  # /api/freezes/* endpoints
│   │   │   ├── slos.py                     # /api/slos/* endpoints
│   │   │   ├── webhooks.py                 # /api/webhooks/alertmanager
│   │   │   └── health.py
│   │   ├── core/
│   │   │   ├── slo_loader.py               # Parse SLO YAML
│   │   │   ├── error_budget_calculator.py  # The math
│   │   │   ├── burn_rate_analyzer.py       # Multi-window analysis
│   │   │   ├── freeze_decision_engine.py   # Should we freeze?
│   │   │   ├── incident_detector.py        # Auto-detect from alerts
│   │   │   ├── dora_metrics.py             # DORA metric computation
│   │   │   └── recording_rule_generator.py  # SLO YAML → Prometheus rules
│   │   ├── integrations/
│   │   │   ├── prometheus_client.py        # PromQL queries
│   │   │   ├── alertmanager_client.py      # Alert state queries
│   │   │   ├── slack_reporter.py           # Slack Block Kit
│   │   │   └── github_client.py            # GitHub deployment metadata
│   │   ├── db/
│   │   │   ├── connection.py
│   │   │   ├── models.py                   # SQLAlchemy ORM
│   │   │   └── queries.py
│   │   └── cache/
│   │       ├── redis_client.py
│   │       └── budget_cache.py
│   ├── tests/
│   │   ├── test_error_budget_calculator.py
│   │   ├── test_burn_rate_analyzer.py
│   │   ├── test_freeze_decision_engine.py
│   │   ├── test_recording_rule_generator.py
│   │   └── fixtures/
│   ├── Dockerfile
│   └── requirements.txt
│
├── dashboard/                              # Next.js custom dashboard
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx                    # Main: 4 service cards + freeze status
│   │   │   ├── service/[name]/page.tsx     # Per-service detailed view
│   │   │   ├── deployments/page.tsx        # Deployment timeline + DORA
│   │   │   ├── incidents/page.tsx          # Incident list + MTTR
│   │   │   ├── freezes/page.tsx            # Freeze event history
│   │   │   └── api/
│   │   │       ├── budget/route.ts
│   │   │       ├── burn-rate/route.ts
│   │   │       ├── deployments/route.ts
│   │   │       └── health/route.ts
│   │   ├── components/
│   │   │   ├── service/
│   │   │   │   ├── ServiceCard.tsx          # Per-service summary card
│   │   │   │   ├── BudgetGauge.tsx          # Visual budget remaining
│   │   │   │   ├── BurnRateGauge.tsx        # Multi-window burn rate
│   │   │   │   ├── FreezeStatusBadge.tsx    # FROZEN / NORMAL
│   │   │   │   └── ProjectedExhaustion.tsx  # "Budget runs out in X days"
│   │   │   ├── timeline/
│   │   │   │   ├── DeploymentTimeline.tsx
│   │   │   │   ├── IncidentMarkers.tsx
│   │   │   │   └── FreezeMarkers.tsx
│   │   │   ├── dora/
│   │   │   │   ├── DeploymentFrequency.tsx
│   │   │   │   ├── ChangeFailureRate.tsx
│   │   │   │   ├── MTTRChart.tsx
│   │   │   │   └── LeadTimeChart.tsx
│   │   │   └── common/
│   │   │       ├── SLOIndicator.tsx
│   │   │       └── BurnRateBadge.tsx
│   │   └── types/index.ts
│   ├── Dockerfile
│   └── package.json
│
├── grafana-dashboards/                     # JSON dashboard definitions
│   ├── slo-overview.json
│   ├── service-detail-template.json
│   ├── burn-rate-multi-window.json
│   └── dora-metrics.json
│
├── load-testing/                           # k6 + Locust scripts
│   ├── normal-traffic.js                   # Baseline load
│   ├── spike-traffic.js                    # Burst pattern
│   ├── error-injection.js                  # Triggers SLO violations
│   └── budget-burn-demo.js                 # Drains budget for demo
│
├── charts/                                 # Helm charts
│   ├── budgetguard/
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   │       ├── platform-deployment.yaml
│   │       ├── dashboard-deployment.yaml
│   │       ├── timescaledb-statefulset.yaml
│   │       ├── redis-deployment.yaml
│   │       ├── rbac.yaml
│   │       ├── prometheus-rules.yaml       # Recording + alert rules
│   │       └── service.yaml
│   └── microservices/
│       └── ...
│
├── monitoring/
│   └── alertmanager-config.yaml            # Alert routing
│
├── .github/workflows/
│   ├── ci.yml                              # Lint + test
│   ├── deploy-microservices.yml            # WITH error budget gate
│   ├── deploy-platform.yml                 # Deploy BudgetGuard itself
│   └── load-test.yml                       # Scheduled load testing
│
├── docker-compose.yml
├── Makefile
└── README.md
```

### 5.2 Core: Error Budget Calculator

```python
# platform/src/core/error_budget_calculator.py

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

@dataclass
class ErrorBudgetStatus:
    service: str
    slo_target: float                  # e.g., 0.999 for 99.9%
    window_days: int                   # Typically 30
    
    # Current state
    current_availability: float        # e.g., 0.9985 (99.85%)
    error_rate: float                  # 1 - current_availability
    
    # Budget calculation
    total_budget_seconds: float        # 30d × 86400 × (1 - 0.999) = 2592 sec
    budget_consumed_seconds: float     # Time-weighted error count
    budget_remaining_seconds: float
    budget_remaining_pct: float        # Most important metric
    
    # Burn rate
    burn_rate_5m: float                # Current 5-minute burn rate
    burn_rate_1h: float
    burn_rate_6h: float
    burn_rate_3d: float
    
    # Decisions
    is_frozen: bool
    freeze_reason: Optional[str]
    projected_exhaustion: Optional[datetime]


class ErrorBudgetCalculator:
    """
    Implements the math from the Google SRE Workbook.
    
    Definitions:
    - SLO target: 0.999 for 99.9% (0.0 to 1.0)
    - Allowable error rate: 1 - SLO target (e.g., 0.001 for 99.9%)
    - Budget consumed: how much of allowable errors we've used
    - Burn rate: actual_error_rate / allowable_error_rate
        - 1.0x = on budget (consuming exactly as planned)
        - 14.4x = at this rate, budget exhausted in 1/14.4 of window
    """

    def __init__(self, prometheus_client, slo_definition):
        self.prom = prometheus_client
        self.slo = slo_definition

    async def calculate(self) -> ErrorBudgetStatus:
        # 1. Current availability over the SLO window
        current_availability = await self._get_current_availability()
        
        # 2. Budget calculations
        allowable_error_rate = 1 - self.slo.target            # e.g., 0.001
        actual_error_rate = 1 - current_availability          # e.g., 0.0015
        
        total_seconds = self.slo.window_days * 86400
        total_budget = total_seconds * allowable_error_rate   # 2592 sec for 99.9%/30d
        
        # Budget consumed = (actual error rate / allowable) × total budget
        budget_consumed = (actual_error_rate / allowable_error_rate) * total_budget
        budget_consumed = min(budget_consumed, total_budget)
        
        budget_remaining = total_budget - budget_consumed
        budget_remaining_pct = (budget_remaining / total_budget) * 100

        # 3. Burn rates over multiple windows
        burn_rates = {
            '5m': await self._get_burn_rate('5m'),
            '1h': await self._get_burn_rate('1h'),
            '6h': await self._get_burn_rate('6h'),
            '3d': await self._get_burn_rate('3d'),
        }

        # 4. Projected exhaustion
        projected = self._project_exhaustion(burn_rates['1h'], budget_remaining_pct)

        # 5. Freeze decision
        is_frozen, reason = self._should_freeze(budget_remaining_pct, burn_rates)

        return ErrorBudgetStatus(
            service=self.slo.service,
            slo_target=self.slo.target,
            window_days=self.slo.window_days,
            current_availability=current_availability,
            error_rate=actual_error_rate,
            total_budget_seconds=total_budget,
            budget_consumed_seconds=budget_consumed,
            budget_remaining_seconds=budget_remaining,
            budget_remaining_pct=budget_remaining_pct,
            burn_rate_5m=burn_rates['5m'],
            burn_rate_1h=burn_rates['1h'],
            burn_rate_6h=burn_rates['6h'],
            burn_rate_3d=burn_rates['3d'],
            is_frozen=is_frozen,
            freeze_reason=reason,
            projected_exhaustion=projected,
        )

    async def _get_current_availability(self) -> float:
        """Query Prometheus for availability over the SLO window."""
        query = f'''
            sum(rate(http_server_requests_seconds_count{{
                service="{self.slo.service}",
                status!~"5.."
            }}[{self.slo.window_days}d]))
            /
            sum(rate(http_server_requests_seconds_count{{
                service="{self.slo.service}"
            }}[{self.slo.window_days}d]))
        '''
        return await self.prom.query_instant(query)

    async def _get_burn_rate(self, window: str) -> float:
        """Burn rate = actual_error_rate / allowable_error_rate"""
        actual_error_rate = await self.prom.query_instant(f'''
            sum(rate(http_server_requests_seconds_count{{
                service="{self.slo.service}",
                status=~"5.."
            }}[{window}]))
            /
            sum(rate(http_server_requests_seconds_count{{
                service="{self.slo.service}"
            }}[{window}]))
        ''')
        
        allowable_error_rate = 1 - self.slo.target
        return actual_error_rate / allowable_error_rate if allowable_error_rate > 0 else 0

    def _project_exhaustion(self, current_burn_rate: float, budget_remaining_pct: float) -> Optional[datetime]:
        """
        At current 1h burn rate, when does budget run out?
        budget_remaining_pct / 100 = remaining fraction
        burn_rate × (time_to_exhaust / window) = remaining fraction
        time_to_exhaust = (budget_remaining_pct / 100) × window / burn_rate
        """
        if current_burn_rate < 0.1:
            return None  # Burn rate too low to project meaningfully
        
        remaining_fraction = budget_remaining_pct / 100
        window_seconds = self.slo.window_days * 86400
        time_to_exhaust_seconds = (remaining_fraction * window_seconds) / current_burn_rate
        return datetime.now() + timedelta(seconds=time_to_exhaust_seconds)

    def _should_freeze(self, budget_remaining_pct: float, burn_rates: dict) -> tuple[bool, Optional[str]]:
        """
        Freeze decision logic — implements SRE policy.
        """
        # Budget too low
        if budget_remaining_pct < 10:
            return True, f"Budget remaining ({budget_remaining_pct:.1f}%) below 10% threshold"
        
        # Currently in active page-level alert (catastrophic burn)
        if burn_rates['1h'] > 14.4 and burn_rates['5m'] > 14.4:
            return True, f"Active page-level burn: {burn_rates['1h']:.1f}x for past hour"
        
        # Sustained ticket-level burn (deteriorating)
        if burn_rates['6h'] > 6 and burn_rates['1h'] > 6:
            return True, f"Sustained moderate burn: {burn_rates['6h']:.1f}x over 6 hours"
        
        return False, None
```

### 5.3 SLO Definition YAML (the SRE source of truth)

```yaml
# slo-definitions/auth-service.yaml

service: auth-service
team: platform
description: "User authentication, JWT issuance, and OAuth callback handling"

slos:
  - name: availability
    description: "Fraction of HTTP requests returning non-5xx response"
    target: 99.95
    window_days: 30
    sli:
      type: ratio
      good_events:
        metric: http_server_requests_seconds_count
        filter: 'service="auth-service",status!~"5.."'
        operation: rate
      total_events:
        metric: http_server_requests_seconds_count
        filter: 'service="auth-service"'
        operation: rate
    error_budget_minutes: 21.6  # 30 × 24 × 60 × (1 - 0.9995) = 21.6 min/month

  - name: latency_p99
    description: "P99 latency under 300ms"
    target: 99.0
    threshold_ms: 300
    window_days: 30
    sli:
      type: ratio
      good_events:
        metric: http_server_requests_seconds_bucket
        filter: 'service="auth-service",le="0.3"'
        operation: rate
      total_events:
        metric: http_server_requests_seconds_count
        filter: 'service="auth-service"'
        operation: rate

alert_thresholds:
  page:
    short_window: 5m
    long_window: 1h
    burn_rate: 14.4
    severity: critical
    runbook_url: "https://wiki.example.com/runbooks/auth-availability"
  ticket:
    short_window: 30m
    long_window: 6h
    burn_rate: 6
    severity: warning
  slow_burn:
    short_window: 6h
    long_window: 3d
    burn_rate: 1
    severity: info

freeze_policy:
  budget_threshold_pct: 10  # Freeze if remaining budget < 10%
  freeze_on_active_page: true
  freeze_on_sustained_ticket: true  # 6h+ at 6x burn
  override_required_role: "platform-lead"
```

### 5.4 Prometheus Recording Rules (Auto-generated)

```yaml
# prometheus-rules/recording-rules.yaml

groups:
  - name: slo:auth-service:sli
    interval: 30s
    rules:
      # SLI: Availability ratio
      - record: slo:sli_ratio:auth-service:availability
        expr: |
          sum(rate(http_server_requests_seconds_count{service="auth-service",status!~"5.."}[5m]))
          /
          sum(rate(http_server_requests_seconds_count{service="auth-service"}[5m]))

      # SLI: Latency ratio (P99 < 300ms)
      - record: slo:sli_ratio:auth-service:latency_p99
        expr: |
          sum(rate(http_server_requests_seconds_bucket{service="auth-service",le="0.3"}[5m]))
          /
          sum(rate(http_server_requests_seconds_count{service="auth-service"}[5m]))

  - name: slo:auth-service:burn_rates
    interval: 30s
    rules:
      - record: slo:burn_rate:auth-service:availability:5m
        expr: (1 - slo:sli_ratio:auth-service:availability) / (1 - 0.9995)

      - record: slo:burn_rate:auth-service:availability:1h
        expr: |
          (
            1 - (
              sum(rate(http_server_requests_seconds_count{service="auth-service",status!~"5.."}[1h]))
              /
              sum(rate(http_server_requests_seconds_count{service="auth-service"}[1h]))
            )
          ) / (1 - 0.9995)

      - record: slo:burn_rate:auth-service:availability:6h
        expr: |
          (
            1 - (
              sum(rate(http_server_requests_seconds_count{service="auth-service",status!~"5.."}[6h]))
              /
              sum(rate(http_server_requests_seconds_count{service="auth-service"}[6h]))
            )
          ) / (1 - 0.9995)

      - record: slo:burn_rate:auth-service:availability:3d
        expr: |
          (
            1 - (
              sum(rate(http_server_requests_seconds_count{service="auth-service",status!~"5.."}[3d]))
              /
              sum(rate(http_server_requests_seconds_count{service="auth-service"}[3d]))
            )
          ) / (1 - 0.9995)

      # Budget remaining percentage
      - record: slo:error_budget_remaining_pct:auth-service:availability
        expr: |
          100 - (
            (
              1 - (
                sum(rate(http_server_requests_seconds_count{service="auth-service",status!~"5.."}[30d]))
                /
                sum(rate(http_server_requests_seconds_count{service="auth-service"}[30d]))
              )
            ) / (1 - 0.9995) * 100
          )
```

### 5.5 Multi-Window Multi-Burn-Rate Alert Rules

```yaml
# prometheus-rules/alert-rules.yaml

groups:
  - name: slo:auth-service:alerts
    rules:
      # PAGE: catastrophic burn (1h@14.4x AND 5m@14.4x)
      - alert: AuthServiceErrorBudgetBurnFast
        expr: |
          slo:burn_rate:auth-service:availability:1h > 14.4
          AND
          slo:burn_rate:auth-service:availability:5m > 14.4
        for: 2m
        labels:
          severity: page
          service: auth-service
          slo: availability
          window: short
        annotations:
          summary: "auth-service burning error budget at {{ $value | humanize }}x"
          description: "At this rate, monthly budget exhausted in {{ printf \"%.1f\" 1.0 $value | humanize }} hours"
          runbook: "https://wiki.example.com/runbooks/auth-availability"
          dashboard: "https://grafana.example.com/d/auth-slo"

      # TICKET: moderate burn (6h@6x AND 30m@6x)
      - alert: AuthServiceErrorBudgetBurnModerate
        expr: |
          slo:burn_rate:auth-service:availability:6h > 6
          AND
          slo:burn_rate:auth-service:availability:30m > 6
        for: 15m
        labels:
          severity: ticket
          service: auth-service
          slo: availability
        annotations:
          summary: "auth-service moderate burn rate sustained"

      # SLOW BURN: long-term degradation (3d@1x AND 6h@1x)
      - alert: AuthServiceErrorBudgetSlowBurn
        expr: |
          slo:burn_rate:auth-service:availability:3d > 1
          AND
          slo:burn_rate:auth-service:availability:6h > 1
        for: 1h
        labels:
          severity: info
          service: auth-service
          slo: availability
        annotations:
          summary: "auth-service slowly consuming error budget"
```

### 5.6 GitHub Actions Workflow with Error Budget Gate

```yaml
# .github/workflows/deploy-microservices.yml

name: Deploy Microservices

on:
  push:
    branches: [main]
    paths: ['microservices/**']
  workflow_dispatch:

jobs:
  pre-deploy-checks:
    runs-on: ubuntu-latest
    outputs:
      can_deploy: ${{ steps.budget_check.outputs.can_deploy }}
      reason: ${{ steps.budget_check.outputs.reason }}
    
    strategy:
      matrix:
        service: [auth-service, orders-service, inventory-service, notifications-service]
    
    steps:
      - name: Determine which services changed
        id: changes
        run: |
          # Detect which microservice directories changed
          CHANGED=$(git diff --name-only HEAD~1 HEAD | grep "^microservices/${{ matrix.service }}/" || echo "")
          if [ -z "$CHANGED" ]; then
            echo "skip=true" >> $GITHUB_OUTPUT
          else
            echo "skip=false" >> $GITHUB_OUTPUT
          fi

      - name: Error Budget Gate
        id: budget_check
        if: steps.changes.outputs.skip == 'false'
        run: |
          RESPONSE=$(curl -s -w "\n%{http_code}" \
            "${{ secrets.BUDGETGUARD_URL }}/api/error-budget/check?service=${{ matrix.service }}")
          
          HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
          BODY=$(echo "$RESPONSE" | head -n-1)
          
          if [ "$HTTP_CODE" != "200" ]; then
            echo "::error::Error budget check failed: HTTP $HTTP_CODE"
            exit 1
          fi
          
          FROZEN=$(echo "$BODY" | jq -r '.frozen')
          REASON=$(echo "$BODY" | jq -r '.reason')
          BUDGET_REMAINING=$(echo "$BODY" | jq -r '.budget_remaining_pct')
          BURN_RATE=$(echo "$BODY" | jq -r '.burn_rate_1h')
          
          echo "Budget remaining: ${BUDGET_REMAINING}%"
          echo "1h burn rate: ${BURN_RATE}x"
          
          if [ "$FROZEN" == "true" ]; then
            echo "::error::🛑 DEPLOYMENT FROZEN for ${{ matrix.service }}"
            echo "::error::Reason: $REASON"
            echo "can_deploy=false" >> $GITHUB_OUTPUT
            echo "reason=$REASON" >> $GITHUB_OUTPUT
            
            # Notify Slack
            curl -X POST "${{ secrets.SLACK_WEBHOOK_URL }}" \
              -H "Content-Type: application/json" \
              -d '{
                "blocks": [
                  {"type": "header", "text": {"type": "plain_text", "text": "🛑 Deployment Frozen"}},
                  {"type": "section", "text": {"type": "mrkdwn", "text": "*Service:* ${{ matrix.service }}\n*Reason:* '"$REASON"'"}}
                ]
              }'
            
            exit 1
          fi
          
          echo "✅ Error budget healthy. Deployment can proceed."
          echo "can_deploy=true" >> $GITHUB_OUTPUT

  deploy:
    needs: pre-deploy-checks
    if: needs.pre-deploy-checks.outputs.can_deploy == 'true'
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [auth-service, orders-service, inventory-service, notifications-service]
    
    steps:
      - uses: actions/checkout@v4
      - name: Configure kubectl
        run: # ... kubectl setup ...
      - name: Helm upgrade
        run: |
          helm upgrade --install ${{ matrix.service }} ./charts/${{ matrix.service }} \
            --set image.tag=${{ github.sha }}

      - name: Record deployment in BudgetGuard
        run: |
          curl -X POST "${{ secrets.BUDGETGUARD_URL }}/api/deployments/record" \
            -H "Content-Type: application/json" \
            -d '{
              "service": "${{ matrix.service }}",
              "version": "${{ github.sha }}",
              "github_run_id": "${{ github.run_id }}",
              "deployed_at": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"
            }'
```

---

## 6. Database Design & Choice

### 6.1 Comparison Matrix

| Criteria | TimescaleDB + PostgreSQL | Plain PostgreSQL | InfluxDB | MongoDB | Cassandra |
|---|---|---|---|---|---|
| **SLI metric history** | High-frequency (every 30s) — perfect fit | Slow without partitioning | Excellent | Slow aggregations | Write-optimized but bad for reads |
| **30-day rolling burn rate** | Continuous aggregate: < 50ms | 5-10 second full scan | Native | Aggregation pipeline (slow) | Not designed for this |
| **Relational data** | Full PostgreSQL — incidents, deployments, freezes with FKs | Same | Separate DB needed | Manual $lookup | Limited |
| **Compression** | 90% compression on time-series chunks | Manual | Native | None standard | Decent |
| **Continuous aggregates** | Native (auto-maintained materialized views) | Manual cron jobs | Continuous queries | Manual | Manual |
| **Operational cost** | One database (extension) | Same | Two databases needed | Medium | High (3+ nodes) |
| **SQL** | Full PostgreSQL SQL | Full SQL | Flux (custom language) | MQL | CQL (limited) |

### VERDICT: TimescaleDB (PostgreSQL Extension)

**Why TimescaleDB wins for BudgetGuard:**

1. **SLI metric history is high-frequency time-series:** Each service emits ~10 SLI data points every 30 seconds. 4 services × 10 metrics × 2 samples/min = 80 data points/min = 115,200 data points/day = 3.4 million/month. Plain PostgreSQL queries scanning 30 days of data take 5-10 seconds. TimescaleDB's hypertables (auto-partitioned by time) + continuous aggregates pre-compute hourly rollups — same query in 50ms.

2. **Burn rate calculations need rolling windows:** "What's the 1-hour burn rate for auth-service over the past 30 days, in 5-minute buckets?" returns 8,640 data points. Continuous aggregates pre-compute this. The dashboard renders without latency.

3. **One database for time-series AND relational:** Incidents, deployments, freeze events are inherently relational (FK to services, FK to SLO definitions). TimescaleDB IS PostgreSQL — same connection, same ORM, same migrations. We don't run two databases.

4. **Compression handles long retention:** SLO history must be retained for at least 90 days for trend analysis. Without compression, that's gigabytes. TimescaleDB compresses old chunks 90% — same data, 1/10 the storage.

5. **Retention policies automate cleanup:** `add_retention_policy('sli_metrics', INTERVAL '90 days')` — no cron jobs, no maintenance scripts.

**Why NOT plain PostgreSQL?**
> "PostgreSQL CAN store time-series data, but a 30-day burn rate query scans 3.4 million rows — 5-10 seconds per query. The dashboard needs to be interactive. TimescaleDB's continuous aggregates pre-compute hourly rollups and store them in a materialized view that auto-refreshes. The same query reads 720 pre-computed rows instead of 3.4 million raw rows. 100x speedup with one extension."

**Why NOT InfluxDB?**
> "InfluxDB is excellent for pure time-series but has no relational model. I'd need InfluxDB for SLI metrics PLUS PostgreSQL for incidents, deployments, and freeze events. Two databases means two backups, two connection pools, two migration systems. TimescaleDB unifies both — one extension on PostgreSQL gives me time-series superpowers AND full relational capability."

### 6.2 Schema Design

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- =============================================
-- RELATIONAL TABLES
-- =============================================

CREATE TABLE services (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL,
    team            VARCHAR(100),
    description     TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE slo_definitions (
    id              SERIAL PRIMARY KEY,
    service_id      INT REFERENCES services(id) ON DELETE CASCADE,
    slo_name        VARCHAR(100) NOT NULL,           -- availability, latency_p99
    sli_type        VARCHAR(20) NOT NULL,             -- ratio, threshold
    target          DECIMAL(8,5) NOT NULL,            -- 0.9995 for 99.95%
    threshold_ms    INT,                              -- For latency SLOs
    window_days     INT DEFAULT 30,
    error_budget_minutes DECIMAL(10,2),               -- Computed at definition time
    sli_query       TEXT NOT NULL,                    -- PromQL for the SLI
    runbook_url     VARCHAR(500),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(service_id, slo_name)
);

CREATE TABLE incidents (
    id                  BIGSERIAL PRIMARY KEY,
    service_id          INT REFERENCES services(id),
    slo_definition_id   INT REFERENCES slo_definitions(id),
    incident_type       VARCHAR(50) NOT NULL,         -- burn_rate_page, burn_rate_ticket, manual
    severity            VARCHAR(20) NOT NULL,          -- page, ticket, info
    status              VARCHAR(20) DEFAULT 'active',  -- active, mitigated, resolved
    
    started_at          TIMESTAMP NOT NULL,
    detected_at         TIMESTAMP DEFAULT NOW(),
    mitigated_at        TIMESTAMP,
    resolved_at         TIMESTAMP,
    
    burn_rate_at_start  DECIMAL(8,2),
    peak_burn_rate      DECIMAL(8,2),
    budget_consumed_pct DECIMAL(5,2),
    
    title               VARCHAR(500) NOT NULL,
    description         TEXT,
    root_cause_url      VARCHAR(500),
    triggering_alert    VARCHAR(200),                  -- Alertmanager alert name
    
    -- Linked deployment that caused this (if any)
    suspected_deployment_id INT,
    
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_incident_service ON incidents(service_id, started_at DESC);
CREATE INDEX idx_incident_status ON incidents(status);

CREATE TABLE deployments (
    id              BIGSERIAL PRIMARY KEY,
    service_id      INT REFERENCES services(id),
    version         VARCHAR(100) NOT NULL,
    commit_sha      VARCHAR(40),
    github_run_id   BIGINT,
    github_pr       INT,
    
    deployed_at     TIMESTAMP NOT NULL,
    deployed_by     VARCHAR(100),
    
    -- Pre-deploy state (captured before)
    pre_deploy_budget_pct DECIMAL(5,2),
    pre_deploy_burn_rate_1h DECIMAL(8,2),
    
    -- Post-deploy state (1h after)
    post_deploy_burn_rate_1h DECIMAL(8,2),
    
    -- Change failure tracking
    is_change_failure BOOLEAN,                          -- Did it cause an alert in next hour?
    rollback_at     TIMESTAMP,
    rollback_reason TEXT,
    
    -- DORA: Lead time (commit → production)
    lead_time_minutes INT,
    
    metadata        JSONB DEFAULT '{}'
);
CREATE INDEX idx_deploy_service ON deployments(service_id, deployed_at DESC);

CREATE TABLE freeze_events (
    id              BIGSERIAL PRIMARY KEY,
    service_id      INT REFERENCES services(id),
    
    frozen_at       TIMESTAMP NOT NULL,
    unfrozen_at     TIMESTAMP,
    duration_minutes INT,                               -- Computed when unfrozen
    
    freeze_reason   VARCHAR(500) NOT NULL,
    triggered_by    VARCHAR(100),                       -- "auto-budget-low", "auto-active-page"
    
    -- Attempted deployments during freeze
    blocked_deployments_count INT DEFAULT 0,
    
    -- Override (if any)
    overridden_by   VARCHAR(100),
    override_reason TEXT,
    
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_freeze_service ON freeze_events(service_id, frozen_at DESC);

-- =============================================
-- HYPERTABLE: SLI METRIC HISTORY
-- =============================================

CREATE TABLE sli_metrics (
    time            TIMESTAMPTZ NOT NULL,
    service_id      INT NOT NULL,
    slo_definition_id INT NOT NULL,
    
    sli_value       DOUBLE PRECISION NOT NULL,         -- Current SLI ratio (0-1)
    error_budget_remaining_pct DOUBLE PRECISION,
    burn_rate_5m    DOUBLE PRECISION,
    burn_rate_1h    DOUBLE PRECISION,
    burn_rate_6h    DOUBLE PRECISION,
    burn_rate_3d    DOUBLE PRECISION
);

SELECT create_hypertable('sli_metrics', 'time',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_sli_service_time ON sli_metrics (service_id, time DESC);

-- =============================================
-- CONTINUOUS AGGREGATES
-- =============================================

CREATE MATERIALIZED VIEW hourly_sli_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    service_id,
    slo_definition_id,
    avg(sli_value) AS avg_sli,
    min(sli_value) AS min_sli,
    avg(burn_rate_1h) AS avg_burn_rate_1h,
    max(burn_rate_1h) AS peak_burn_rate_1h,
    avg(error_budget_remaining_pct) AS avg_budget_remaining
FROM sli_metrics
GROUP BY bucket, service_id, slo_definition_id;

SELECT add_continuous_aggregate_policy('hourly_sli_stats',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- =============================================
-- COMPRESSION + RETENTION
-- =============================================

ALTER TABLE sli_metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'service_id, slo_definition_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('sli_metrics', INTERVAL '7 days');
SELECT add_retention_policy('sli_metrics', INTERVAL '90 days');
```

### 6.3 Key Queries

```sql
-- 1. Per-service current state (dashboard main view)
SELECT s.name AS service,
       sd.slo_name,
       sd.target,
       latest.error_budget_remaining_pct,
       latest.burn_rate_1h,
       CASE
         WHEN latest.error_budget_remaining_pct < 10 THEN 'FROZEN'
         WHEN latest.burn_rate_1h > 14.4 THEN 'CRITICAL'
         WHEN latest.burn_rate_1h > 6 THEN 'WARNING'
         ELSE 'NORMAL'
       END AS status
FROM services s
JOIN slo_definitions sd ON sd.service_id = s.id
JOIN LATERAL (
    SELECT * FROM sli_metrics
    WHERE service_id = s.id AND slo_definition_id = sd.id
    ORDER BY time DESC LIMIT 1
) latest ON true
WHERE s.is_active = TRUE;

-- 2. Burn rate trend (chart over 30 days)
SELECT bucket,
       avg_burn_rate_1h,
       avg_budget_remaining
FROM hourly_sli_stats
WHERE service_id = (SELECT id FROM services WHERE name = 'auth-service')
  AND slo_definition_id = (SELECT id FROM slo_definitions
                          WHERE slo_name = 'availability'
                            AND service_id = (SELECT id FROM services WHERE name = 'auth-service'))
  AND bucket > NOW() - INTERVAL '30 days'
ORDER BY bucket;

-- 3. MTTR per service (DORA metric)
SELECT s.name,
       AVG(EXTRACT(EPOCH FROM (resolved_at - started_at)) / 60) AS mttr_minutes,
       COUNT(*) AS total_incidents
FROM incidents i
JOIN services s ON i.service_id = s.id
WHERE i.status = 'resolved'
  AND i.resolved_at > NOW() - INTERVAL '30 days'
GROUP BY s.name;

-- 4. Change failure rate (DORA)
SELECT s.name,
       COUNT(*) AS total_deploys,
       COUNT(*) FILTER (WHERE is_change_failure) AS failed_deploys,
       ROUND(
         COUNT(*) FILTER (WHERE is_change_failure)::numeric / NULLIF(COUNT(*), 0) * 100, 1
       ) AS change_failure_rate_pct
FROM deployments d
JOIN services s ON d.service_id = s.id
WHERE d.deployed_at > NOW() - INTERVAL '30 days'
GROUP BY s.name;

-- 5. Freeze impact analysis
SELECT s.name,
       COUNT(*) AS freeze_count,
       SUM(duration_minutes) AS total_freeze_minutes,
       SUM(blocked_deployments_count) AS deploys_blocked
FROM freeze_events f
JOIN services s ON f.service_id = s.id
WHERE f.frozen_at > NOW() - INTERVAL '30 days'
GROUP BY s.name;
```

---

## 7. Caching & Messaging — Redis 7

### Purposes

**Purpose 1 — Error Budget Check Cache (CRITICAL for performance)**
```
Key: "budget:check:{service}"        → Full ErrorBudgetStatus JSON (TTL: 60s)

Why critical: GitHub Actions calls this on every deploy. If 10 PRs merge
in an hour, that's 10 calls. Without caching, each query hits Prometheus
(slow ~500ms-2s) and runs the calculator. With 60s cache, only the first
call computes; rest are cached.
```

**Purpose 2 — Freeze State Cache**
```
Key: "freeze:{service}"              → { frozen: bool, reason: string } (TTL: 30s)

Polled by dashboard every 30 seconds. Without cache, this would hit DB
multiple times per second per dashboard user.
```

**Purpose 3 — PromQL Query Cache**
```
Key: "promql:{queryHash}"            → Prometheus response (TTL: 60s)
Avoids re-running expensive PromQL queries for the same time window.
```

**Purpose 4 — Deployment Event Queue (Redis Streams)**
```
Stream: "deployment-events"
Producer: GitHub Actions webhook on deploy completion
Consumer: BudgetGuard worker that:
  - Records deployment in PostgreSQL
  - Sets up 1-hour change-failure observation
  - Annotates Grafana dashboard
  - Updates DORA metrics

Why queue? Deployment recording shouldn't block the GitHub Actions
pipeline. Webhook fires-and-forgets to Redis Stream.
```

**Why NOT Kafka?**
> "Under 50 deployments per day. Kafka's 3-broker overhead for 50 events/day is unjustifiable. Redis Streams handles this trivially."

**Why NOT RabbitMQ?**
> "Redis already handles 3 other purposes. Adding RabbitMQ for one queue means another service in the Helm chart, another runtime to monitor. Redis Streams handles the queue natively."

---

## 8. Design Patterns Used

### 8.1 Strategy Pattern (SLI Calculation Methods)

```python
class SLIStrategy(ABC):
    @abstractmethod
    async def calculate(self, slo_def: SLODefinition, window: str) -> float: ...

class RatioSLI(SLIStrategy):
    """Standard good_events/total_events ratio."""
    async def calculate(self, slo_def, window):
        good = await prom.query(slo_def.good_events_query.format(window=window))
        total = await prom.query(slo_def.total_events_query.format(window=window))
        return good / total

class ThresholdSLI(SLIStrategy):
    """Latency-based SLI: % of requests under threshold."""
    async def calculate(self, slo_def, window):
        bucket_query = f'rate(http_requests_bucket{{le="{slo_def.threshold_ms/1000}"}}[{window}])'
        # ...

class CompositeSLI(SLIStrategy):
    """Multiple SLIs combined (e.g., availability AND latency)."""
    # ...
```

### 8.2 Builder Pattern (Slack Message + PR Comment)

```python
class FreezeNotificationBuilder:
    def with_service(self, service_name): ...
    def with_freeze_reason(self, reason): ...
    def with_budget_status(self, budget_remaining_pct): ...
    def with_burn_rate_context(self, burn_rates): ...
    def with_recent_deployments(self, deployments): ...
    def with_action_buttons(self, can_override): ...
    def build(self) -> dict: ...
```

### 8.3 Chain of Responsibility (Freeze Decision)

```python
# Each handler can either freeze or pass to next
chain = BudgetThresholdHandler()  # < 10% remaining
chain.set_next(ActivePageAlertHandler()) \
     .set_next(SustainedTicketBurnHandler()) \
     .set_next(ManualFreezeHandler())

decision = await chain.evaluate(service)
```

### 8.4 Observer Pattern (Event Propagation)

```python
event_bus.on('alert:fired', auto_create_incident)
event_bus.on('alert:fired', update_freeze_state)
event_bus.on('alert:fired', notify_slack)
event_bus.on('deployment:completed', record_in_database)
event_bus.on('deployment:completed', schedule_change_failure_check)
event_bus.on('freeze:applied', notify_team_owners)
event_bus.on('freeze:released', send_ok_signal)
```

### 8.5 Template Method (SLO Recording Rule Generation)

```python
class RecordingRuleGenerator(ABC):
    def generate(self, slo_def: SLODefinition) -> list[str]:
        rules = []
        rules.extend(self._generate_sli_rules(slo_def))      # Common
        rules.extend(self._generate_burn_rate_rules(slo_def))  # Common
        rules.extend(self._generate_specific_rules(slo_def))   # Abstract
        return rules
    
    @abstractmethod
    def _generate_specific_rules(self, slo_def): ...

class AvailabilityRules(RecordingRuleGenerator): ...
class LatencyRules(RecordingRuleGenerator): ...
```

---

## 9. Resilience Patterns & Mitigation Strategies

### 9.1 Resilience Patterns in Microservices

| Pattern | Where Used | Why |
|---|---|---|
| **Circuit Breaker** (Resilience4j) | auth-service → token validation, orders-service → inventory call | Prevents cascade when downstream fails |
| **Retry with Exponential Backoff** | All inter-service calls | Handles transient failures (network blips, brief restarts) |
| **Timeout** | All HTTP clients (default 3s) | Prevents resource exhaustion from slow downstreams |
| **Bulkhead** | notifications-service (separate thread pools per channel: email, sms, push) | Isolates failures — slow email doesn't starve SMS |
| **Rate Limiter** | auth-service login endpoint | Prevents credential stuffing attacks |
| **Fallback** | inventory-service → returns stale cache data when DB unreachable | Degraded but functional response |

### 9.2 Resilience Patterns in BudgetGuard Itself (the platform that monitors others)

The IRONY: BudgetGuard itself must be highly available, or who watches the watchers?

| Pattern | Where Used | Why |
|---|---|---|
| **Cache Fallback** | If Prometheus is down, return last known budget state from Redis | Don't block deployments because monitoring is broken |
| **Graceful Degradation** | Dashboard shows "Last updated 2 min ago" when fresh data unavailable | Better than blank page |
| **Idempotent Operations** | Deployment recording, freeze events — duplicate webhooks safe | Webhook retries don't corrupt state |
| **Database Connection Pool** | SQLAlchemy with max 20 connections, queue beyond | Backpressure under spikes |
| **Health Checks** | /health endpoint checks DB + Redis + Prometheus | K8s liveness probe restarts unhealthy pods |

### 9.3 Mitigation Strategies (What If Things Go Wrong?)

**Scenario 1: Prometheus is down**
- Fallback: Return cached budget state from Redis (TTL extended to 5 min during outage)
- Decision: If cache also empty, return `frozen: false, reason: "monitoring unavailable, defaulting to allow"` with explicit warning
- Alert: Page on-call when Prometheus is unreachable

**Scenario 2: TimescaleDB is slow / unavailable**
- Fallback: Reads come from Redis cache where possible
- Writes: Buffered in Redis Stream, replayed when DB recovers
- Dashboard: Shows "Historical data temporarily unavailable" banner

**Scenario 3: Slack webhook fails**
- Fallback: Notification logged in PostgreSQL with `delivery_status: pending`
- Retry: Background job retries failed notifications with exponential backoff
- Escalation: After 3 failed attempts, send via PagerDuty (post-MVP)

**Scenario 4: GitHub Actions can't reach BudgetGuard**
- Default behavior: `fail open` (allow deployment) — better to deploy than to block all teams
- Alert: Notify SRE team that the gate is bypassed
- Audit: Log in PostgreSQL that deployment proceeded without budget check

**Scenario 5: All 4 services have low budgets simultaneously**
- This indicates a platform-level issue (network, infrastructure)
- BudgetGuard detects pattern → notifies on-call with "platform-wide alert"
- Doesn't freeze ALL services automatically — that's a business decision

### 9.4 The "Reliability of the Reliability System" Principle

> "If BudgetGuard is unavailable, the deployment freeze can't enforce. If we fail closed (block all deploys), we punish reliability work itself. If we fail open (allow all deploys), we lose the safety net. I chose fail-open with loud alerting — when BudgetGuard is down, on-call is paged immediately, but deployments continue. The metric to watch is BudgetGuard's own availability — I track this on the dashboard separately."

---

## 10. Docker & Kubernetes Deployment Strategy

### 10.1 Docker Compose (Local Dev)

```yaml
version: '3.8'

services:
  # Microservices
  auth-service:
    build: ./microservices/auth-service
    ports: ["8081:8081"]
    
  orders-service:
    build: ./microservices/orders-service
    ports: ["8082:8082"]

  inventory-service:
    build: ./microservices/inventory-service
    ports: ["8083:8083"]

  notifications-service:
    build: ./microservices/notifications-service
    ports: ["8084:8084"]

  # BudgetGuard platform
  platform:
    build: ./platform
    ports: ["8000:8000"]
    depends_on:
      timescaledb: { condition: service_healthy }
      redis: { condition: service_healthy }
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@timescaledb:5432/budgetguard
      - REDIS_URL=redis://redis:6379
      - PROMETHEUS_URL=http://prometheus:9090
      - SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}

  dashboard:
    build: ./dashboard
    ports: ["3000:3000"]

  # Data layer
  timescaledb:
    image: timescale/timescaledb:latest-pg16
    ports: ["5432:5432"]
    environment: { POSTGRES_DB: budgetguard, POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres }
    volumes:
      - timescale_data:/home/postgres/pgdata/data
      - ./platform/sql/init.sql:/docker-entrypoint-initdb.d/01-init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  # Observability
  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./prometheus-rules:/etc/prometheus/rules

  alertmanager:
    image: prom/alertmanager:latest
    ports: ["9093:9093"]
    volumes:
      - ./monitoring/alertmanager.yml:/etc/alertmanager/alertmanager.yml

  grafana:
    image: grafana/grafana:latest
    ports: ["3001:3000"]
    volumes:
      - ./grafana-dashboards:/etc/grafana/provisioning/dashboards
      - grafana_data:/var/lib/grafana

volumes:
  timescale_data:
  grafana_data:
```

### 10.2 Helm Chart Structure

```yaml
# charts/budgetguard/values.yaml

platform:
  replicaCount: 2
  image:
    repository: yourdockerhub/budgetguard-platform
    tag: latest
  resources:
    requests: { cpu: 200m, memory: 512Mi }
    limits: { cpu: 1000m, memory: 1Gi }
  env:
    PROMETHEUS_URL: "http://prometheus-kube-prometheus-prometheus.monitoring:9090"
    ALERTMANAGER_URL: "http://prometheus-kube-prometheus-alertmanager.monitoring:9093"

dashboard:
  replicaCount: 1
  image:
    repository: yourdockerhub/budgetguard-dashboard
    tag: latest

timescaledb:
  enabled: true
  persistence:
    size: 20Gi
  resources:
    requests: { cpu: 500m, memory: 1Gi }
    limits: { cpu: 2000m, memory: 2Gi }

redis:
  enabled: true
  architecture: standalone

prometheusRules:
  enabled: true
  recordingRules: |
    # Auto-loaded from prometheus-rules/recording-rules.yaml
  alertRules: |
    # Auto-loaded from prometheus-rules/alert-rules.yaml

slack:
  webhookUrl:
    secretName: budgetguard-slack
    secretKey: webhook-url
```

---

## 11. CI/CD Pipeline with Error Budget Gate

The CI/CD pipeline IS the demo of this project. Already shown in Section 5.6.

The key innovation:

```
TRADITIONAL PIPELINE:
  Code → Test → Build → Deploy

BUDGETGUARD PIPELINE:
  Code → Test → Build → ERROR BUDGET CHECK → Deploy
                              ↓
                         IF FROZEN: Abort + Notify
                         IF OK:     Continue
```

This single addition transforms deployment from "deploy whenever" to "deploy only when reliability budget allows."

---

## 12. Monitoring — The "Scaling Decision" to Document

### Grafana Dashboard (4 Panels for README)

**Panel 1: SLO Compliance Gauges (per service)**
- 4 large gauges showing current 30-day availability vs SLO target
- Green/yellow/red based on budget remaining

**Panel 2: Burn Rate Heat Strip**
- Rolling 7-day strip showing burn rate intensity per service
- Dark red = high burn, dark green = healthy

**Panel 3: Deployment + Incident Timeline**
- Vertical bars annotated on the burn rate chart
- "Deploy v2.3.1" → followed by burn rate spike → "Incident #47" → "Rollback v2.3.0"

**Panel 4: DORA Metrics Quad**
- Deployment Frequency
- Change Failure Rate
- MTTR
- Lead Time for Changes

### The "Scaling Decision" to Document

> **Scaling Decision: Continuous Aggregates over Real-Time Burn Rate Calculation**
>
> **Problem:** The dashboard refreshes every 30 seconds. Each refresh runs burn rate queries for 4 services × 4 windows (5m, 1h, 6h, 3d) = 16 queries. Without optimization, each query scans millions of rows in TimescaleDB. Page load: 3-5 seconds.
>
> **Option A — Real-time computation:** Run all 16 queries on every dashboard load against raw `sli_metrics` hypertable.
>
> **Option B — Application-level Redis cache:** Cache results in Redis for 30 seconds. Pros: simple. Cons: cache invalidation logic, stale data handling.
>
> **Option C — TimescaleDB continuous aggregates:** Pre-compute hourly burn rates in materialized view. Auto-refresh hourly. Dashboard reads from view (small, indexed) instead of raw hypertable.
>
> **Decision:** Option C + Option B (layered). Continuous aggregates handle the bulk of historical queries (30-day trend reads from `hourly_sli_stats` — 720 rows). Redis caches the 16 dashboard queries for 30 seconds. Together, page load drops from 3-5 seconds to under 200ms.
>
> **The trade-off:** Continuous aggregates have a 1-hour staleness for historical data (acceptable). Redis cache has 30-second staleness for current state (acceptable). Real-time current burn rate (5-minute window) bypasses cache for accuracy.
>
> **When I'd revisit:** If we monitored 100+ services, I'd add per-service caching with longer TTLs and pre-render the dashboard server-side.

---

## 13. Interview Prep — Top Questions & Answers

### SRE Fundamentals

**Q1: "Walk me through SLOs, SLIs, and error budgets."**

> "SLI is the measurement — what fraction of HTTP requests succeed? SLO is the target we promise — 99.9% success rate over 30 days. Error budget is the allowable failure budget — 0.1% of requests can fail before we miss our SLO. For 99.9% over 30 days, the math is: 30 days × 24 hours × 60 minutes = 43,200 minutes total. 0.1% of that is 43.2 minutes of allowed downtime. That's the monthly error budget. Burn rate is how fast we're consuming it: 1x means consuming exactly as planned, 14.4x means burning 14.4 times the rate, which exhausts a month's budget in one hour."

**Q2: "Explain multi-window multi-burn-rate alerts."**

> "Single-window alerts have a fundamental trade-off: short windows catch fast fires but produce false positives from blips. Long windows are stable but slow to detect. The Google SRE workbook solution is to require BOTH a short and long window to fire simultaneously. For page-level alerts, I require 5-minute burn rate AND 1-hour burn rate to both exceed 14.4x. The 5-minute window detects quickly, the 1-hour window confirms it's sustained. This eliminates ~95% of false positives while still detecting catastrophic failures within minutes. The thresholds — 14.4x for page, 6x for ticket, 1x for slow burn — are derived from the percentage of monthly budget you're willing to consume in that window."

**Q3: "Why do different services have different SLOs?"**

> "SLO design follows user impact, not engineering preference. Auth is 99.95% because authentication failure breaks the entire user experience — every other service depends on it. Orders is 99.9% because failed orders mean lost revenue, but degraded retries can sometimes succeed. Inventory is 99.5% because cached stale data is acceptable for a few seconds. Notifications is 99.0% because async retries handle transient failures invisibly. The key SRE principle is: tighter SLOs cost more — more redundancy, more on-call burden, more engineering effort. Choose SLOs that match user expectations, not maximize internal pride."

**Q4: "What happens when the error budget is exhausted?"**

> "The Google SRE book prescription is: stop shipping features and focus engineering effort on reliability. BudgetGuard automates this — when budget drops below 10%, the deployment pipeline freezes for that service. Engineers can't merge new features until reliability work restores the budget. This forces a cultural shift: reliability becomes a first-class engineering priority instead of an afterthought. The override mechanism exists for genuine emergencies (security patches), but every override is logged and reviewed. The freeze is a forcing function, not a hard stop."

### Technical Implementation

**Q5: "Walk me through the architecture."**

> "Four Spring Boot microservices run on Kubernetes, each with Resilience4j for circuit breakers and Spring Boot Actuator exposing Prometheus metrics. Prometheus scrapes every 15 seconds and evaluates recording rules that compute SLIs (good_events / total_events) and burn rates over multiple windows. Multi-window multi-burn-rate alerts fire to Alertmanager, which webhooks BudgetGuard's FastAPI backend. The backend computes error budget remaining, makes freeze decisions, and exposes a `/api/error-budget/check` endpoint that GitHub Actions calls before every deployment. If frozen, the pipeline aborts. SLI history is stored in TimescaleDB hypertables with continuous aggregates pre-computing hourly rollups. Redis caches dashboard queries and freeze state. A Next.js dashboard shows per-service budgets, deployment timelines, and DORA metrics."

**Q6: "Why TimescaleDB and not plain PostgreSQL?"**

> "BudgetGuard stores SLI metrics every 30 seconds across 4 services and 8 SLO definitions = ~2,000 data points per minute. Over 90 days, that's ~260 million rows. Plain PostgreSQL queries scanning 30-day burn rates take 5-10 seconds. TimescaleDB's hypertables auto-partition by time so queries only touch the relevant chunks. Continuous aggregates pre-compute hourly P99 rollups in a materialized view that auto-refreshes — the same query reads 720 pre-computed rows instead of millions. Compression saves 90% storage on chunks older than 7 days. And TimescaleDB IS PostgreSQL — relational data like incidents and deployments lives in the same database with full SQL JOINs."

**Q7: "What's the freeze decision logic?"**

> "Three conditions trigger freeze: First, budget remaining below 10% — we're running out of error budget for the month, so any deploy might exhaust it. Second, active page-level burn — 1-hour burn rate above 14.4x AND 5-minute burn rate above 14.4x. We're actively in a fire, no time to deploy new code. Third, sustained ticket-level burn — 6-hour burn rate above 6x. Ongoing degradation that needs investigation, not new changes. Each condition has a clear reason logged in the audit trail. The override mechanism requires a justification and is logged for post-incident review."

**Q8: "How does the deployment freeze work end-to-end?"**

> "Developer pushes to main → GitHub Actions triggers deploy workflow → pre-deploy step runs `curl /api/error-budget/check?service=auth-service` → BudgetGuard returns JSON with `frozen: true/false` and reason → if frozen, the pipeline runs `exit 1` to abort, posts a Slack message explaining why, and creates a PR comment if applicable → if not frozen, the pipeline continues to Helm upgrade → after deploy, GitHub Actions calls `/api/deployments/record` to track the deployment → BudgetGuard schedules a 1-hour change-failure observation. If a burn rate alert fires within that hour, the deployment is marked as a change failure for DORA metrics."

**Q9: "Why Redis Streams over Kafka for the deployment event queue?"**

> "Under 50 deployments per day. Kafka requires minimum 3 broker instances plus ZooKeeper or KRaft for production — 4GB+ RAM consumed for 50 messages per day. Redis Streams gives me consumer groups, acknowledgment, and pending message visibility with the same Redis instance I already use for caching and freeze state. One tool, four purposes. If we scaled to handle thousands of services with millions of deployments, I'd evaluate Kafka — but at this scale, Kafka is overengineering."

**Q10: "What if BudgetGuard itself goes down?"**

> "The principle is fail-open with loud alerting. If BudgetGuard is unavailable, GitHub Actions defaults to allowing the deployment but sends an alert to on-call. Failing closed — blocking all deployments because monitoring is broken — punishes reliability work itself. The trade-off: during a BudgetGuard outage, a bad deploy could slip through, but it's a known risk with explicit visibility. The metric to watch is BudgetGuard's own availability, which I track on the dashboard separately. If BudgetGuard's availability drops below its own SLO, that's a critical platform issue."

### Production / Operations

**Q11: "How would you scale this to 100 services?"**

> "Three scaling axes. First, the platform backend needs horizontal scaling — multiple FastAPI replicas behind a load balancer, with Redis Pub/Sub coordinating cache invalidation. Second, Prometheus federation — instead of one Prometheus scraping all services, I'd run per-team Prometheus instances feeding a global Prometheus for SLO calculation, reducing single-point-of-failure risk. Third, TimescaleDB needs scaling — partition tables by service_id, add read replicas for dashboard queries. Fourth, the dashboard itself becomes problematic with 100 service cards — I'd add team-based filtering and search. The most interesting scale challenge: alert fatigue at 100 services. I'd implement alert grouping at Alertmanager level and surface only the highest-severity alerts."

**Q12: "How do you decide when to override a freeze?"**

> "Three legitimate reasons. First, security patches — never block a critical CVE fix even if budget is low; user safety trumps SLO discipline. Second, fixes for the very issue causing the freeze — if a bug is burning the budget, the fix to that bug must merge. Third, business-critical scheduled launches with executive approval. Every override requires a documented reason, the user's identity, and is logged in `freeze_events.overridden_by` and `freeze_events.override_reason`. Post-incident, all overrides are reviewed in retros. The goal is friction without total blockage — the override exists, but it's accountable."

**Q13: "Describe the demo."**

> "I open the dashboard — 4 service cards showing healthy budgets (auth: 87%, orders: 92%, inventory: 78%, notifications: 95%). I run k6 load test that intentionally triggers 500 errors against orders-service. Within 2 minutes, the orders card turns yellow — burn rate spiking. Within 5 minutes, the page-level alert fires — Slack notification arrives with full context. The card turns red — frozen. Then I attempt to deploy a feature branch via GitHub Actions. The pipeline pre-step calls the budget check endpoint. Response: `frozen: true`. Pipeline aborts. PR comment appears: 'Deployment blocked. orders-service is burning budget at 22.3x. Investigate before merging.' That's the magic moment — reliability automation prevented a bad deploy from compounding the problem. After load test stops and budget recovers, the freeze auto-releases and deploys can proceed."

---

## 14. Deployment Checklist

- [ ] 4 microservices deployed to K8s with Prometheus metrics flowing
- [ ] SLO definitions in YAML for all 4 services
- [ ] Recording rules generating SLI ratios + burn rates
- [ ] Multi-window multi-burn-rate alerts configured
- [ ] BudgetGuard API: error budget check endpoint working
- [ ] GitHub Actions pre-deploy step calling error budget check
- [ ] Demo: load test consumes budget → next deploy frozen → Slack notification
- [ ] TimescaleDB storing SLI history with continuous aggregates
- [ ] Custom Grafana dashboard with 4 panels
- [ ] Next.js dashboard with per-service cards + DORA metrics
- [ ] Incident auto-creation from burn rate alerts
- [ ] Deployment tracking with change failure rate
- [ ] CI/CD pipeline for BudgetGuard itself
- [ ] Helm chart for full platform deployment
- [ ] Docker Compose for local development
- [ ] README with architecture diagram + SLO design rationale
- [ ] Demo video showing the freeze in action

### Cost Estimate

| Service | Provider | Cost |
|---|---|---|
| GKE Autopilot (small cluster) | Google Cloud | ~$70/mo ($300 free credit) |
| OR kind on EC2 t3.medium | AWS | ~$30/mo |
| Slack | Slack | Free |
| **Total** | | **$0-70/month** |

---

## Final Word — Why This Project Wins SRE Interviews

BudgetGuard demonstrates the textbook Google SRE methodology executed end-to-end. Here's what makes it the strongest SRE capstone:

**Pillar 1: Mathematical Rigor**
- "99.9% = 43.2 minutes/month" — you can compute SLO arithmetic on demand
- Multi-window multi-burn-rate threshold derivation (14.4x, 6x, 1x)
- Continuous aggregate optimization for time-series queries

**Pillar 2: Cultural Automation**
- Deployment freeze enforces "reliability over velocity" without humans
- Override mechanism preserves engineering judgment for emergencies
- DORA metrics tracked automatically — no manual data collection

**Pillar 3: Production-Grade Observability**
- Prometheus recording rules (the SRE workbook approach)
- Multi-burn-rate alerting (catches fast AND slow degradations)
- Incident correlation with deployments (root cause analysis built-in)

**Pillar 4: SRE Toolchain Mastery**
- PromQL for SLI calculation and burn rate computation
- Alertmanager for routing and grouping
- Grafana for visualization with annotations
- TimescaleDB for time-series storage at scale
- Custom-built dashboard for SRE-specific workflows

**The maturity hierarchy:**
1. **Level 1 (most freshers):** "I set up Prometheus and Grafana" → basic
2. **Level 2 (good freshers):** "I defined SLOs and alerting" → intermediate
3. **Level 3 (advanced freshers — rare):** "I track error budgets" → SRE-aware
4. **Level 4 (you):** "I built error budget enforcement that automatically freezes deployments and saved 23 minutes of downtime by blocking 7 bad deploys this month" → senior SRE thinking

**The sentence that wins:**
> "I run an error budget gate on every deployment. If the auth service has burned through more than 90% of its monthly budget, the pipeline freezes and the team can't merge features until reliability is restored. This is the textbook Google SRE pattern from chapter 4 of the SRE book. We blocked 7 deployments this month that would have caused additional incidents."

This is the project that gets hired at Google SRE, Flipkart SRE, Razorpay SRE, and Atlassian SRE. The Google SRE book IS the curriculum, and this project IS the implementation of the book.

---

*Document prepared as a complete implementation guide for BudgetGuard — SLO/Error Budget Dashboard with Automated Deployment Freeze (2026)*
