# BudgetGuard Rev 2 — Implementation Plan

Oct 10, 2026 · @Ricky Enrico

## Executive summary

Rev 2 keeps BudgetGuard's core — four Spring Boot services, Prometheus SLIs, multi-window burn-rate alerts and a deploy freeze — but fixes the Rev 1 defects that an SRE panel would catch in the first five minutes, and turns the binary freeze into a priced, unbypassable admission control that no open-source SLO tool ships today.

The single most important fix: Rev 1 says "a 14.4x burn for one hour exhausts the monthly budget." It does not. A 14.4x burn spends 2% of a 30-day budget per hour and exhausts it in about 50 hours. If you say the Rev 1 sentence in a Google or Razorpay SRE loop, the interview is over. §B lists every defect with its fix.

**What changed from Rev 1**

| Area | Rev 1 | Rev 2 |
| --- | --- | --- |
| SLO math | Contradictory budgets (43.8 vs 43.2 min), wrong burn-rate claims, wrong budget formula | One canonical derivation, unit-tested against hand-computed fixtures |
| Gate location | `curl` step inside the workflow YAML — any developer can delete it | Server-side GitHub custom deployment protection rule plus an Argo Rollouts runtime backstop |
| Gate logic | Binary: freeze below 10% budget | Deploy pricing: each deploy's expected budget cost vs what the service can afford; rollbacks and incident fixes always admitted |
| SLI truthfulness | A crashed service emits no metrics, so its error rate reads 0 and the gate says healthy | NaN and no-traffic treated as UNKNOWN; low-traffic floor; synthetic-probe cross-check |
| Attribution | orders-service is frozen when auth-service causes its errors | Errors labelled by origin; budget charged to the culprit, not the victim |
| Unfreeze | "Estimated unfreeze time" with no method | Exact recovery time computed from when old bad minutes age out of the rolling window |
| Policy changes | Edit a threshold and hope | Policy backtest replays stored history in CI and blocks a policy PR that regresses |
| Database | TimescaleDB justified by "260 million rows" (real figure: about 2 million per 90 days) and Prometheus remote\_write into TimescaleDB (that adapter, Promscale, is discontinued) | TimescaleDB kept, justified honestly by long-retention SLO history and replay; Prometheus stays the live source of truth |
| LLM | Not considered | GPT-5.4-mini behind a provider adapter writes the human-readable Freeze Brief only; never in the decision path; template fallback; grounding eval gates CI |
| Stack | Spring Boot 3.x, `:latest` images | Pinned to the portfolio baseline (Spring Boot 4.x on Java 21, Node 24 LTS, Python 3.14) — §C |

**The new hook (memorize)**

> "BudgetGuard treats the error budget as a currency. Every deploy gets a price — its expected budget cost, from that service's own change-failure history — and a server-side GitHub protection rule only admits the deploy if the service can afford it. Rollbacks and fixes for the active incident always pass, so the freeze never blocks the repair. The math is exact: 99.9% over 30 days is 43.2 minutes. A 14.4x burn spends 2% of that every hour, which is why it pages."

## §A Audit grid — your brief vs Rev 1

Rev 1 covers 15 of 27 requirements fully; 7 are partial and 5 are missing outright, including the README, the public link, deployment strategies, availability/consistency patterns and any differentiator that beats Pyrra or Sloth.

| # | Requirement from your brief | Rev 1 status | Rev 2 action |
| --- | --- | --- | --- |
| 1 | 4 microservices: auth, orders, inventory, notifications | Present | Kept; Spring Boot 4.x on Java 21 (§C) |
| 2 | SLIs and SLOs defined per service | Partial — latency SLI reads a histogram bucket Spring Boot does not publish by default | Histogram + SLO boundaries configured in `application.yml` (§4) |
| 3 | Grafana: error budget burn rate per service | Present | Kept; recording rules fixed (§B-4) |
| 4 | Grafana: 30-day rolling availability | Present, but via a raw `rate()[30d]` query | Request-weighted counters over 30d (§4) |
| 5 | Grafana: MTTR per incident type | Partial — MTTR per service only | `incident_type` dimension + median, not mean (§5) |
| 6 | Grafana: deployment change rate | Partial — frequency only | Frequency, change-failure rate, lead time, failed-deploy recovery time (§5) |
| 7 | Freeze via GitHub Actions when budget below 10% | Present but bypassable and buggy (§B-6, B-7) | Server-side protection rule + priced admission (§D) |
| 8 | Prometheus, Alertmanager, Kubernetes | Present | Pinned versions (§C) |
| 9 | Exact tech-stack versions | Missing — Spring Boot 3.x, `:latest` everywhere | Version matrix (§C) |
| 10 | MVP blueprint | Present (7 weeks) | 8 weeks, MVP cut line explicit (§2) |
| 11 | HLD, LLD, system design, architecture | Present | Redrawn around the admission controller (§3, §4) |
| 12 | DB choice among PostgreSQL / NoSQL / TimescaleDB / Cosmos DB / Cassandra / MongoDB | Partial — Cosmos DB not argued, row counts wrong by \~100x | Full matrix with honest sizing (§5) |
| 13 | Cache/queue among Redis / Kafka / ZooKeeper / RabbitMQ | Partial — ZooKeeper never addressed, Kafka claim outdated | Full matrix (§6) |
| 14 | Design patterns | Present | Kept + Specification, Saga-lite, Outbox (§7) |
| 15 | Docker and Kubernetes | Present — compose uses `:latest` and obsolete `version:` key | Pinned, digest-locked (§9) |
| 16 | DevOps/MLOps wrapper | Missing | Policy backtest + LLM grounding eval gate CI (§10) |
| 17 | Resilience patterns | Present | Kept + SLI-integrity checks (§8) |
| 18 | Mitigation strategies | Present — fail-open contradicts the workflow, which fails closed | One policy, enforced in code (§8) |
| 19 | Availability and consistency patterns | Missing | CAP stance per data path (§8) |
| 20 | Deployment strategies | Missing | Budget-proportional canary with Argo Rollouts (§9) |
| 21 | Interview prep | Present — contains the wrong burn-rate claim and invented results | Rewritten, every number derivable (§12) |
| 22 | README with architecture diagram, setup, screenshots | Missing — in the table of contents, absent from the body | Full README blueprint (§11) |
| 23 | CI/CD: GitHub Actions → DockerHub → EC2 or Railway | Partial — no image build/push for the platform; Railway cannot run Kubernetes | Build → sign → push → deploy to k3s on a free VM (§10, §11) |
| 24 | docker-compose.yml | Present | Fixed (§9) |
| 25 | Monitoring dashboard | Present | Kept + BudgetGuard's own SLO (§11) |
| 26 | Documented scaling decision | Present | Kept, numbers corrected (§11) |
| 27 | Public live link | Missing | k3s on an always-free VM with a demo mode (§11) |
| 28 | Differentiators that beat existing open-source tools | Missing — Pyrra and Sloth are named, nothing beats them | Eight differentiators (§D) |
| 29 | Is GPT-5 better suited? | Missing | Decision with cost and role (§E) |

## §B Rev 1 defects, ranked by interview damage

Eighteen defects; the first five would sink an SRE loop on their own because an interviewer can disprove them with arithmetic or one PromQL query.

**The burn-rate arithmetic, done once and correctly.** For a 30-day window, the fraction of budget spent in a window of length t at burn rate b, and the time to exhaust a full budget, are:

```latex
\text{budget spent} = \frac{b \cdot t}{T_{SLO}} \qquad t_{exhaust} = \frac{T_{SLO}}{b} \qquad T_{SLO} = 720\,h
```

So 14.4x for 1 hour spends 14.4 / 720 = 2%; 6x for 6 hours spends 5%; 1x for 3 days spends 10%. At 14.4x the whole budget lasts 720 / 14.4 = 50 hours, not one hour. That 2% / 5% / 10% ladder is exactly where the Google SRE Workbook's 14.4 / 6 / 1 thresholds come from.

| # | Severity | Rev 1 defect | Rev 2 fix |
| --- | --- | --- | --- |
| B-1 | Critical | "14.4x burn for 1 hour exhausts the monthly budget" (summary, Q1, HLD box). The HLD box also computes 14.4 × 5/60 = 1.2 h = 167%, which is meaningless | 14.4x for 1 h spends 2%; full exhaustion takes 50 h. Derivation above; a unit test asserts it |
| B-2 | Critical | Hook says auth is 99.9% = 43.8 min; the SLO table says auth is 99.95% = 21.6 min | One table, 30-day rolling window: 99.9% = 43.2 min. Know that 43.8 min is the calendar-month average (30.44 days) — say which window you use |
| B-3 | Critical | Flow 1 writes budget remaining as (slo\_target − error\_rate) / (1 − slo\_target); the calculator clips consumed budget at 100% | Correct form is 1 − error\_rate / (1 − target). Allow negative remaining: overspend is information the freeze policy needs |
| B-4 | Critical | Ticket alert reads `slo:burn_rate:…:30m`, but no 30m recording rule exists. The alert evaluates to empty and never fires — silently | Rules generated from one spec by code; `promtool test rules` fixtures cover every window the alerts reference |
| B-5 | Critical | A crashed or unscraped service emits no samples: the ratio is NaN or absent, every `> 14.4` comparison is false, and the gate reports healthy. A dead service has a perfect error rate | Gate state is a tri-state HEALTHY / AT\_RISK / UNKNOWN; `up == 0`, `absent()` and low-traffic floors map to UNKNOWN; blackbox probes cross-check (§D-4) |
| B-6 | High | Gate is a `curl` step inside `deploy-microservices.yml`; anyone with write access can delete it or dispatch an edited workflow | Gate moves server-side: a GitHub App custom deployment protection rule on the `production` environment (§D-1) |
| B-7 | High | Workflow bugs: no `actions/checkout` before `git diff`; `HEAD~1` needs `fetch-depth: 2`; matrix job outputs overwrite each other so `can_deploy` is whichever service finished last; deploy matrix ships all 4 services; HTTP ≠ 200 exits 1, contradicting the stated fail-open policy; Slack JSON built by shell interpolation breaks on a quote in `reason` | Rewritten workflow (§10): per-service jobs via `dorny/paths-filter`, environment gate, `jq -n` for JSON, explicit fail-open branch |
| B-8 | High | `/check`, `/deployments/record` and `/freeze/override` are unauthenticated — anyone can record fake deploys or lift a freeze | GitHub OIDC tokens verified on every write (audience, repo, ref, environment claims); overrides need a signed-in approver with the `platform-lead` role |
| B-9 | High | Latency SLI reads `http_server_requests_seconds_bucket{le="0.3"}`, but Spring Boot publishes no histogram buckets by default, and Micrometer has no `service` label | `management.metrics.distribution.slo.http.server.requests: 300ms,500ms,800ms,2s` plus a `service` common tag (§4). Note Prometheus 3 normalizes `le` values to float form, so match `le="1.0"` not `le="1"` |
| B-10 | High | 30-day availability is `rate(…[30d])` on raw counters, but Prometheus' default retention is 15 days — the query silently covers half the window | Retention set to 35d; 30-day SLI computed from 5-minute recorded increases summed over 30d (request-weighted, §4) |
| B-11 | High | "260 million rows over 90 days" and "2,000 points per minute" — the real figure is 8 SLO series × 2 samples/min × 129,600 min ≈ 2.1 million rows. Also, Prometheus `remote_write` into TimescaleDB needs Promscale, which Timescale discontinued | Honest sizing and a different justification for TimescaleDB (§5); BudgetGuard's own snapshotter writes SLI snapshots, Prometheus stays the live source |
| B-12 | Medium | Alert tiers deviate from the Workbook without saying so (Workbook pages on 6x/6h; Rev 1 tickets it), `for: 2m` adds delay the short window already covers, and the annotation template `printf "%.1f" 1.0 $value` is invalid | Tiers documented as a deliberate policy in the SLO spec; `for:` dropped on page alerts; templates validated by `promtool check rules` |
| B-13 | Medium | Invented outcomes: "saved 23 minutes of downtime", "blocked 7 bad deploys this month", "hired on the spot" | Only numbers produced by the demo harness, labelled as demo numbers (§12). A counterfactual saving cannot be measured — never claim one |
| B-14 | Medium | Schema: `TIMESTAMP` without time zone; `suspected_deployment_id INT` with no FK while `deployments.id` is BIGINT; MTTR as a mean (skewed by one long incident); no `incident_type` breakdown | `TIMESTAMPTZ` everywhere, real FKs, median and p90 MTTR per `incident_type` (§5) |
| B-15 | Medium | Change-failure rule "any burn alert within 60 minutes of a deploy" blames a deploy for an outage caused by a dependency | Only self-inflicted burn (D-3 attribution) on the deployed service counts |
| B-16 | Medium | Spring Boot 3.x (3.5 OSS support ended 30 June 2026), `:latest` images, obsolete compose `version:` key, Redis 7 | Pinned matrix (§C), digests in Helm values |
| B-17 | Low | "Kafka needs 3 brokers plus ZooKeeper" — Kafka 4.0 removed ZooKeeper; a single KRaft node runs fine for dev; HA needs 3 controllers | Corrected wording in §6 |
| B-18 | Low | `datetime.now()` naive timestamps in exhaustion projections | `datetime.now(UTC)` and ISO-8601 with offset in every API response |

## §C Version matrix (checked 10 Oct 2026)

Pin minor lines, float patches, and lock image digests in Helm values; rows marked "portfolio pin" carry your August 2026 baseline and need a one-line re-check at kickoff.

| Layer | Pin | Status | Why this, and the trap to avoid |
| --- | --- | --- | --- |
| JVM | Java 21 LTS (Temurin or Oracle 21.0.x) | Portfolio pin | Matches your installed JDK 21.0.11; virtual threads for the 4 services |
| Microservices | Spring Boot 4.1.x (4.1.1 or newer) | [Verified](https://spring.io/blog/2026/06/10/spring-boot-4/) — 4.1 OSS support runs to 31 Jul 2027 ([status](https://isitpatched.com/spring-boot/4.1.1)) | Spring Boot 3.5 OSS support ended 30 Jun 2026. Same rule as ChaosProof: never 4.0.6; 4.0.7 only as a fallback |
| Build | Maven 3.9.x | Portfolio pin | Same as ChaosProof's target app — reuse its `pom.xml` conventions |
| Resilience | Resilience4j 3.x | Portfolio pin | Circuit breaker, retry, bulkhead in the services |
| Platform API | Python 3.14 + FastAPI 0.128.x + Pydantic 2 | Portfolio pin | Same stack as HealOps/IncidentPilot, so the admission engine and backtester share one language with NumPy/SciPy |
| Dashboard | Node 24 LTS + Next.js 16.3.x | Portfolio pin | Node 24 is your established baseline |
| Metrics | Prometheus 3.13.x LTS (3.14.0 is newest) | [Verified](https://github.com/prometheus/prometheus/releases) | 3.13 is the LTS line — pick it for a gate that must not regress. Set retention to 35d (default 15d breaks a 30-day SLO) |
| Alerting | Alertmanager 0.34.1 | [Verified](https://github.com/prometheus/alertmanager/releases) | Supersedes the portfolio's 0.33.1: 0.34.1 fixes alerts being wrongly un-muted by inhibition rules — matters when page alerts inhibit ticket alerts |
| Dashboards | Grafana 13.2.x (13.2.2 or newer) | [Verified](https://isitpatched.com/grafana/13.1.6) | 13.0 and 13.1 carry a HIGH CVE fixed in 13.2.0. Flag: HealOps and ChaosProof pinned 13.0.x and should move too |
| Orchestration | Kubernetes 1.36.x (1.37 released 26 Aug 2026) | [Verified](https://kubernetes.io/vi/releases/1.37/) | Run N-1: kube-prometheus-stack and Argo Rollouts lag new minors by weeks. k3s for the public deploy, kind for CI |
| Progressive delivery | Argo Rollouts 1.10.0 | [Verified](https://github.com/argoproj/argo-rollouts/releases) | 1.9.1+ patches CVE-2026-35469. Never expose the Rollouts dashboard publicly; use port-forward |
| Time-series + relational | PostgreSQL 18 + TimescaleDB 2.27.x or newer | [Verified](https://www.tigerdata.com/docs/get-started/timescaledb-supported-platforms) | TimescaleDB 2.23+ supports PG 18; Timescale now trades as Tiger Data. Use the Community (TSL) build — compression and continuous aggregates are not in the Apache-only build |
| Cache + stream | Valkey 9.1 (Redis 8.x is a drop-in alternative) | Portfolio pin | Streams, consumer groups and `SET NX PX` locks all exist in both; Valkey keeps the BSD licence |
| Packaging | Helm 4.2.x | Portfolio pin | Charts for services, platform and rules |
| Load | k6 2.2.x + k6 Operator 1.0 | Portfolio pin | Same load plane as ChaosProof |
| Chat | slack-bolt (Python) 1.28.x | Portfolio pin | Freeze Brief delivery and the override approval button |
| SLO interop | OpenSLO v1 spec; Sloth 0.16.0 and Pyrra 0.10.1 as parity references | [Sloth](https://github.com/slok/sloth/releases), [Pyrra](https://github.com/pyrra-dev/pyrra/releases) | CI generates rules from your spec and diffs them against Sloth's output for the same SLO |
| Gate | GitHub custom deployment protection rules (GitHub App) | [Verified](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) — public preview; public repos on the Free plan | Your portfolio repo is public, so this costs nothing. A private repo would need GitHub Enterprise |
| Supply chain | cosign keyless signing, SBOM via syft | Portfolio pin | Admission later verifies signatures |
| LLM (narrative only) | Adapter default `gpt-5.4-mini`; challenger `gpt-6-luna` | [gpt-5.4-mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [gpt-6-luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | §E explains the choice and the concern with keeping 5.4-mini as default |

## §D What already exists, and the eight differentiators that beat it

No maintained open-source tool enforces an error budget at deploy time today. Sloth and Pyrra generate excellent rules and dashboards but stop at observation; Keptn v1, which did SLO quality gates, reached end of life in December 2023; Datadog's gate is a commercial preview. BudgetGuard's wedge is enforcement plus economics: it decides, it prices, and it can prove why.

| Tool (current release) | What it does well | What it does not do |
| --- | --- | --- |
| [Sloth](https://github.com/slok/sloth/releases) v0.16.0, Apr 2026 | Generates SLI recording rules and multi-window alerts from a spec; plugin system; new built-in UI; reads OpenSLO | No deploy gate, no deploy/incident correlation, no attribution, no unfreeze math |
| [Pyrra](https://github.com/pyrra-dev/pyrra/releases) v0.10.1, Jun 2026 | Rules plus a polished UI; subquery "performance mode"; per-SLO alert severity overrides; Prometheus 3 `le` handling | Same: observes and alerts, never decides |
| [Keptn](https://github.com/keptn/keptn) v1 | Had SLO-based quality gates in a delivery control plane | Repository archived; v1 end of life 22 Dec 2023 |
| [Datadog Deployment Gates](https://docs.datadoghq.com/deployment_gates/) | Halts rollouts on monitors and APM anomalies | Commercial, in preview, Datadog-only, no budget pricing |
| Argo Rollouts AnalysisTemplate | Queries Prometheus during a canary and aborts on a static threshold | Thresholds are fixed per template; no notion of remaining budget |

**The eight differentiators**

| # | Differentiator | What it does | Why it beats the tools above | Build phase |
| --- | --- | --- | --- | --- |
| D-1 | Unbypassable admission | The gate is a GitHub App registered as a custom deployment protection rule on the `production` environment, plus an Argo Rollouts analysis inside the cluster | A workflow edit cannot remove it; a `kubectl` deploy that skips GitHub still meets the runtime check | MVP, week 5 |
| D-2 | Deploy pricing | Every deploy gets a price in budget-percent: its probability of failing times the budget a failure usually costs. Admitted only if the service can afford it | Turns the binary 10% freeze into a decision that lets small safe changes through and stops risky ones early | Week 6 |
| D-3 | Culprit, not victim | Each 5xx carries an `error_origin` label (`self` or `dep:<service>`). Budget is charged to the origin | When auth fails, orders is not frozen for auth's outage; auth is | Week 6 |
| D-4 | Truthful SLIs | Tri-state verdicts (HEALTHY / AT\_RISK / UNKNOWN), a minimum-events floor, staleness and `up` checks, blackbox probes as a cross-check | Kills the "dead service has a perfect error rate" bug that every naive rule set has | MVP, week 3 |
| D-5 | Exact unfreeze ETA | Replays stored 5-minute buckets forward to find when old bad minutes age out of the 30-day window | Engineers get "budget returns at 14:20 Thursday" instead of "try later" | MVP, week 5 |
| D-6 | Policy backtesting in CI | A change to `freeze-policy.yaml` replays 90 days of history plus a synthetic incident library and reports blocked deploys, prevented burn and false-freeze minutes; CI fails on regression | Policies become testable code — the eval harness for the gate itself | Week 7 |
| D-7 | Budget-proportional canary | Canary step weights and bake times are derived from remaining budget; the analysis compares canary burn to stable burn | Blast radius shrinks automatically as reliability gets scarce | Week 8 (stretch) |
| D-8 | Interop and replayable decisions | Reads OpenSLO, emits rules that CI diffs against Sloth's; every verdict is a decision record (inputs hash, policy version, verdict) that recomputes identically | Adopt-not-replace story for teams already on Sloth/Pyrra; auditable like a ledger | Week 8 (stretch) |

**How D-2 prices a deploy.** The probability of failure is a Beta-Binomial posterior over that service's own deploy history, scaled by a change-risk multiplier. The expected cost of a failure is the median budget burned by past change-failures on that service (5% until history exists).

```latex
\text{price} = \underbrace{\frac{\alpha_0 + f}{\alpha_0 + \beta_0 + n}}_{\text{posterior CFR}} \times m_{risk} \times \tilde{c}_{fail} \qquad \text{admit if } \text{price} \le k \cdot (B_{remaining} - B_{reserve})
```

Worked example on orders-service, with history n = 30 deploys and f = 3 failures, prior Beta(2, 18), median failure cost 4.2%, reserve 5%, k = 0.5:

| Remaining budget | Deploy | m\_risk | Price | Affordable | Verdict | Rev 1 verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 18% | 40-line handler change | 1.0 | 0.42% | 6.5% | Admit | Admit |
| 7% | 40-line handler change | 1.0 | 0.42% | 1.0% | Admit | Freeze |
| 7% | 900-line change with a DB migration | 3.0 | 1.26% | 1.0% | Block — split the migration, or wait for the D-5 ETA | Freeze |
| 7% | Rollback to the last healthy image digest | — | 0% (rollback lane) | 1.0% | Admit | Freeze |
| 3% | Fix linked to the active incident | — | Fix lane | 0% | Admit, forced canary at 5% | Freeze |

The posterior CFR here is (2 + 3) / (2 + 18 + 30) = 10%. The multipliers and prior are priors, not truths: D-6 backtests them, and you say so in the interview.

> Soundbite: "Sloth and Pyrra tell you the budget is burning. BudgetGuard decides what is still allowed to ship — and a rollback is always free."

## §E Is GPT-5 better suited? No — and the LLM never decides

A flagship model adds nothing here: every gate verdict must be deterministic, reproducible and auditable, so the LLM's only job is to turn a structured decision record into a readable Freeze Brief for Slack and the PR. That is a short summarization task, so the adapter defaults to `gpt-5.4-mini` and a template renders the same brief whenever the model is slow, failing or over budget.

**What the LLM does and does not touch**

| Task | Owner | Reason |
| --- | --- | --- |
| Freeze verdict, deploy price, attribution, unfreeze ETA | Deterministic Python | Must recompute identically from the decision record (D-8) |
| Alert thresholds and policy tuning | Backtester (D-6) | Tuned against history, not opinion |
| Freeze Brief: what is burning, the culprit, price vs affordable, ETA, what is still allowed | LLM, with template fallback | Plain-language explanation of numbers it is given |
| Policy-PR summary: explains the backtest diff in the PR comment | LLM, with template fallback | Same grounding rules |

**Model choice, priced on one Freeze Brief** (about 1,500 input and 250 output tokens; [OpenAI pricing](https://developers.openai.com/api/docs/pricing))

| Model | Price per 1M tokens in / out | Cost per brief | At 200 briefs/month | Verdict |
| --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | $0.75 / $4.50 | $0.00225 | $0.45 | Default — your portfolio baseline; structured outputs, 400K context |
| `gpt-6-luna` | $0.10 / $0.50 | $0.000275 | $0.06 | Challenger — run the grounding eval in week 7; promote if it passes |
| `gpt-6.1-sol` | $2.00 / $10.00 | $0.0055 | $1.10 | Not needed for summarization |
| `gpt-6-astra` (flagship) | $10.00 / $50.00 | $0.0275 | $5.50 | Rejected: more cost and latency, no accuracy gain on a grounded summary |

Cost is not the deciding argument — all four are under $6 a month. Latency in the PR comment path, determinism of the fallback, and the grounding pass rate decide.

**A concern I am flagging rather than silently fixing.** GPT-5.4-mini is your established baseline, and it is still a current model (snapshot `gpt-5.4-mini-2026-03-17`). But OpenAI's pricing page now leads with the GPT-6 family, and `gpt-6-luna` is listed at roughly 7x cheaper input and 9x cheaper output. Keep 5.4-mini as the default for portfolio consistency, and let the eval decide the switch — the adapter makes it a one-line config change, which is itself the interview story.

**The grounding eval that gates CI** (full harness in §10)

- Every number in the brief must appear in the decision record, within rounding tolerance
- Every service, version and incident ID must appear in the record
- Required facts present: verdict, lane, price, affordable, ETA
- Never recommends an override; never states a cause the record does not contain
- Thresholds on a 120-case golden set: grounding ≥ 99%, required-fact recall ≥ 95%, p95 latency ≤ 4 s; a miss keeps the template as default

```yaml
# platform/config/llm.yaml — the only place a model name appears
llm:
  provider: openai            # openai | anthropic | ollama | template
  model: gpt-5.4-mini
  challenger: gpt-6-luna      # evaluated nightly, never served
  timeout_ms: 4000
  max_output_tokens: 300
  monthly_budget_usd: 5
  on_failure: template        # deterministic fallback
```

> Soundbite: "The model writes the explanation, never the verdict. If OpenAI is down, the freeze still works — the brief just comes from a template."

## 1. Tech stack

Three languages, each where it is strongest: Java for the services under test, Python for the decision engine and its statistics, TypeScript for the human workflows. Versions are in §C.

| Layer | Choice | Why (interview answer) |
| --- | --- | --- |
| Services under test | 4 × Spring Boot 4.1 on Java 21, Resilience4j | The stack Bangalore SRE teams run; Actuator exposes Micrometer metrics with SLO histogram buckets |
| Error attribution | Spring `HandlerInterceptor` + exception mapper adding `error_origin` | D-3 needs the origin at the source; traces would work but add a tracing backend for one label |
| SLI source of truth | Prometheus 3.13 LTS, 35-day retention | PromQL is the SRE lingua franca; live SLIs stay where alerting already lives |
| Rule generation | BudgetGuard generator from OpenSLO-style YAML, Sloth parity check in CI | One spec drives recording rules, alerts, gate policy and dashboards — no drift |
| Alerting | Alertmanager 0.34 → webhook into the platform | Multi-window, multi-burn-rate routing; inhibition stops ticket noise during a page |
| Decision engine | FastAPI on Python 3.14 | NumPy/SciPy for the Beta posterior and the backtester; same stack as HealOps and IncidentPilot |
| Gate | GitHub App as custom deployment protection rule + Argo Rollouts AnalysisTemplate | Pre-deploy and in-flight enforcement that a workflow edit cannot remove |
| History | PostgreSQL 18 + TimescaleDB | SLI snapshots for replay, unfreeze ETA and 13-month reports, joined to deploys and incidents in SQL |
| Cache, locks, queue | Valkey 9.1 | Gate verdict cache, single-flight locks, deploy-event stream |
| Human workflows | Next.js 16 dashboard | Freeze explanations, override approvals, policy-diff review — workflows Grafana panels cannot host |
| Metrics views | Grafana 13.2 | Burn-rate, availability, MTTR and change-rate panels |
| Progressive delivery | Argo Rollouts 1.10 | Budget-proportional canary steps (D-7) |
| Load and failure injection | k6 2.2 + fault flags in the services | Drives the demo and the synthetic incident library |
| Narrative | LLM adapter, `gpt-5.4-mini` default, template fallback | §E |

**Why NOT the alternatives**

| Rejected | Why not |
| --- | --- |
| Datadog SLOs or Datadog Deployment Gates | Commercial and in preview; using it removes the engineering you are showcasing |
| Grafana Cloud SLO | Tied to Grafana Cloud; the point is a self-hosted, cloud-neutral gate |
| Pyrra or Sloth as the core | Both are generators and viewers; you interoperate with them (D-8) and add what they lack |
| A `curl` step in the workflow | Bypassable by editing YAML (B-6) |
| Flagger | Works, but Argo Rollouts' AnalysisTemplate maps one-to-one onto a PromQL budget query and has the larger Bangalore footprint |
| Istio for L7 SLIs | Sidecars and a control plane for 4 services; Micrometer gives the same request metrics. Named as the scale path |
| Thanos or Mimir for long-term metrics | Not needed at 4 services with 35-day retention; named as the 100-service path |
| Go for the platform | Ecosystem-native (Sloth and Pyrra are Go), but the backtester and Beta posterior are 30 lines of NumPy/SciPy; Go is the rewrite path if the gate ever needs sub-millisecond latency |
| Python microservices | The services are the system under test; Spring + Resilience4j is what the target companies run |
| Grafana alone, no Next.js | Grafana renders numbers; it cannot host an approval flow, a policy diff or a decision-record viewer |

## 2. MVP blueprint and 8-week schedule

The MVP is done at the end of week 5: four services with truthful SLIs, correct multi-window alerts, a server-side gate with an exact unfreeze ETA, and a public URL. Weeks 6–7 add the differentiators an interviewer has never seen; week 8 is stretch and polish.

**SLOs (30-day rolling window, request-based)**

| Service | Availability SLO | Budget as bad requests | Time-equivalent | Latency SLI | Why this target |
| --- | --- | --- | --- | --- | --- |
| auth-service | 99.95% | 1 in 2,000 | 21.6 min | 99% of requests < 300 ms | Every other journey depends on it |
| orders-service | 99.9% | 1 in 1,000 | 43.2 min | 99% < 500 ms | Revenue path; client retries absorb some blips |
| inventory-service | 99.5% | 1 in 200 | 3.6 h | 99% < 800 ms | Stale cached stock is acceptable for seconds |
| notifications-service | 99.0% | 1 in 100 | 7.2 h | 95% of messages delivered < 2 min after enqueue | Async: the user feels delivery delay, not the HTTP 202 |

Two things interviewers probe here. First, with a request-based SLI the budget is a count of bad requests; "43.2 minutes" is only the equivalent for a full outage at constant traffic, so say both. Second, an async service's SLI must measure end-to-end delivery from the queue, not the enqueue call, or the SLI stays green while messages rot.

**MVP cut line**

- [ ] Four services with fault flags (`FAULT_ERROR_RATE`, `FAULT_LATENCY_MS`, `FAULT_DEPENDENCY=auth`) and `error_origin` labels
- [ ] SLO specs, generated recording rules and alerts, `promtool test rules` fixtures
- [ ] Tri-state SLIs with traffic floors and blackbox probes (D-4)
- [ ] Snapshotter writing 5-minute good/total buckets to TimescaleDB
- [ ] Gate API, GitHub App protection rule, decision records (D-1)
- [ ] Exact unfreeze ETA (D-5)
- [ ] Grafana: burn rate per service, 30-day availability, MTTR per incident type, deploy change rate
- [ ] docker-compose for local, Helm on k3s for the public deploy, CI/CD to Docker Hub

**Week by week**

1. **Services and metrics.** Four Spring Boot services with realistic calls (orders → auth, orders → inventory, orders → notifications), Actuator histograms, `service` common tag, fault flags. Exit: every SLI series visible in Prometheus.
2. **SLO-as-code.** Spec schema, rule generator, `promtool` unit tests for every burn window, 35-day retention. Exit: the B-4 bug cannot recur because a test references every window an alert uses.
3. **Truthful SLIs and alerts.** Tri-state verdicts, traffic floors, blackbox exporter probes, Alertmanager routing with inhibition. Exit: killing a pod yields UNKNOWN, not HEALTHY.
4. **History and dashboards.** Snapshotter, TimescaleDB schema, continuous aggregates, Grafana panels, Next.js service cards. Exit: 30-day availability from TimescaleDB matches Prometheus within 0.01 percentage points.
5. **The gate (MVP complete).** GitHub App protection rule, decision records, OIDC auth, override flow, unfreeze ETA, public deploy. Exit: the demo freeze works end to end on the public URL.
6. **Pricing and attribution.** Beta posterior, change-risk features from the PR diff, lanes, `error_origin` chargeback. Exit: the §D worked example reproduces from real data.
7. **Backtester and evals.** Synthetic incident library, policy replay, CI gate, LLM grounding eval and Freeze Brief. Exit: a policy PR shows a backtest diff comment.
8. **Stretch and polish.** Budget-proportional canary, Sloth parity check, README, screenshots, 3-minute demo video, chaos drill of BudgetGuard itself. Exit: README checklist in §11 complete.

## 3. High-level design and request flows

BudgetGuard is four planes: the workload being measured, the signal plane that measures it, a decision plane that turns signals into verdicts, and two enforcement points that a developer cannot route around. Prometheus remains the live source of truth; TimescaleDB holds the history the decision plane replays.

&#91;embedded content: BudgetGuard architecture · 4 planes, 2 enforcement points\]

The protection rule pauses the deploy job and asks the decision plane; the verdict returns to GitHub before any image reaches Argo Rollouts, and the Freeze Brief reaches people asynchronously.

| Plane | Components | Responsibility |
| --- | --- | --- |
| Workload | auth, orders, inventory, notifications (Spring Boot) | Serve traffic; emit request metrics with `service`, `status`, `error_origin` |
| Signal | Prometheus, Alertmanager, blackbox exporter, kube-state-metrics | Recording rules, burn-rate alerts, synthetic probes, scrape health |
| Decision | BudgetGuard platform (FastAPI): snapshotter, gate engine, pricer, attributor, ETA calculator, backtester, brief writer | Verdicts, prices, decision records, Freeze Briefs |
| Enforcement | GitHub App (deployment protection rule), Argo Rollouts AnalysisTemplate | Pre-deploy admission; in-flight abort |
| State | TimescaleDB on PostgreSQL 18, Valkey 9.1 | History and relational records; verdict cache, locks, event stream |
| People | Next.js dashboard, Grafana, Slack | Explain, approve overrides, review policy diffs |

**Flow 1 — Pre-deploy admission (the core path)**

1. A deploy job declares `environment: production`. GitHub pauses it and sends a `deployment_protection_rule` webhook, with a callback URL, to the BudgetGuard App.
2. The platform verifies the webhook HMAC, acknowledges with 200 within a second, and enqueues an evaluation keyed by `(run_id, environment, service)` so retries are idempotent.
3. The engine assembles inputs: live burn rates (5m, 30m, 1h, 6h, 3d) from Prometheus, 30-day budget from TimescaleDB, SLI integrity, open incidents, the PR's diff stats, labels and linked incident, and the service's deploy history.
4. A chain evaluates in order: integrity (UNKNOWN?) → lanes (rollback, incident fix, security) → hard stops (active page on this service) → price vs affordable.
5. The decision record — inputs hash, policy version, verdict, price — is written before anything is sent.
6. The App posts `approved` or `rejected` with a comment to the callback URL. The comment carries the price, the culprit and the unfreeze ETA.
7. The Freeze Brief goes to Slack and the PR asynchronously; a slow LLM never delays the verdict.
8. If the verdict is UNKNOWN, the service's declared failure mode applies (§8): fail-open with an audit record and a page by default.

**Flow 2 — In-flight enforcement (canary)**

1. The approved verdict also returns a `canary_profile` (D-7): for example `[25, 100]` with a 2-minute bake when budget is plentiful, `[5, 25, 50, 100]` with 10-minute bakes when it is scarce.
2. Helm renders the Argo Rollout steps from that profile.
3. At each step an AnalysisTemplate queries Prometheus for canary versus stable self-inflicted burn; a ratio above 2 for two consecutive checks aborts and rolls back.

**Flow 3 — Alert to incident to freeze**

1. Alertmanager posts a firing burn-rate alert to `/v1/webhooks/alertmanager`.
2. The platform upserts an incident keyed on the alert fingerprint (duplicate deliveries are no-ops) with `incident_type` from the alert labels.
3. The service's cached verdict is invalidated, so the next deploy is re-evaluated against the active page.
4. A resolved notification closes the incident and records time to resolve for MTTR.

**Flow 4 — Snapshots and history**

1. Every 60 seconds the snapshotter queries `increase()` of good and total events for the last completed, aligned 5-minute bucket per SLO.
2. It upserts one row per `(bucket, slo_id)`; reruns overwrite the same row.
3. Continuous aggregates roll buckets into hourly and daily rows for dashboards, reports and the ETA calculator.

**Flow 5 — Deploy outcome and DORA**

1. GitHub's `deployment_status` webhook records success or failure and lead time (first commit → production).
2. For 60 minutes after a deploy, only self-inflicted burn on that service (D-3) can mark it a change failure.
3. Deploy frequency, change-failure rate, lead time and failed-deploy recovery time are computed in SQL from these rows.

## 4. Low-level design

One spec file per service drives everything downstream — recording rules, alerts, gate policy and dashboards — so the B-4 class of bug (an alert reading a rule nobody generated) cannot happen. The files below are complete; property names marked for re-check should be confirmed against the Spring Boot 4.1 reference at kickoff.

**4.1 Repository layout**

```text
budgetguard/
├── services/{auth,orders,inventory,notifications}-service/   # Spring Boot 4.1, Maven
├── slo/                       # one ServiceLevel spec per service (source of truth)
├── policy/freeze-policy.yaml  # gate policy; changes trigger the backtest (D-6)
├── platform/                  # FastAPI decision plane
│   ├── budgetguard/{budget,gate,pricing,eta,attribution,records}.py
│   ├── budgetguard/integrations/{prometheus,github_app,slack,llm}.py
│   ├── budgetguard/workers/{snapshotter,gate_worker,deploy_events}.py
│   ├── backtest/              # replay engine + synthetic incident library
│   └── evals/                 # brief grounding eval, golden cases
├── rules/generated/           # output of `bg generate`; never hand-edited
├── rules/tests/               # promtool unit tests
├── dashboard/                 # Next.js 16
├── grafana/                   # provisioned dashboards as JSON
├── deploy/{compose,helm,k3s}/
└── .github/workflows/
```

**4.2 SLO spec — `slo/auth-service.yaml`**

```yaml
apiVersion: budgetguard.dev/v1
kind: ServiceLevel
metadata:
  service: auth-service
  owner: team-identity
spec:
  window: 30d
  failure_mode: fail_open        # gate behaviour when the SLI is UNKNOWN
  min_events_per_hour: 600       # below this, short windows report LOW_TRAFFIC
  probe: https://auth.budgetguard.local/actuator/health/readiness
  slos:
    - name: availability
      objective: 0.9995
      sli:
        total: http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*"}
        bad: http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*",status=~"5.."}
        self_bad: http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*",status=~"5..",error_origin="self"}
      alerting:
        page:
          - {long: 1h, short: 5m, burn: 14.4}   # 2% of budget in 1h
          - {long: 6h, short: 30m, burn: 6}     # 5% of budget in 6h
        ticket:
          - {long: 3d, short: 6h, burn: 1}      # 10% of budget in 3d
    - name: latency
      objective: 0.99
      sli:
        total: http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*"}
        good: http_server_requests_seconds_bucket{service="auth-service",uri!~"/actuator.*",le="0.3"}
  gate:
    reserve_pct: 5
    spend_fraction: 0.5
    hard_stop_on: [page]
```

**4.3 Service instrumentation — `application.yml` (each service)**

```yaml
spring:
  application:
    name: auth-service
  threads:
    virtual:
      enabled: true
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  endpoint:
    health:
      probes:
        enabled: true
  metrics:
    tags:
      service: ${spring.application.name}        # re-check name in Boot 4.1 docs
    distribution:
      slo:
        http.server.requests: 300ms,500ms,800ms,2s # publishes the le buckets the latency SLI reads
```

**4.4 Error-origin label — `ErrorOriginObservationConvention.java`**

```java
package com.budgetguard.common.metrics;

import io.micrometer.common.KeyValues;
import org.springframework.http.server.observation.DefaultServerRequestObservationConvention;
import org.springframework.http.server.observation.ServerRequestObservationContext;
import org.springframework.stereotype.Component;

/** Adds error_origin = none | self | dep:<service> to http.server.requests. Bounded cardinality: 5 values. */
@Component
public class ErrorOriginObservationConvention extends DefaultServerRequestObservationConvention {

    public static final String ATTR = "bg.error_origin";

    @Override
    public KeyValues getLowCardinalityKeyValues(ServerRequestObservationContext context) {
        Object origin = context.getCarrier().getAttribute(ATTR);
        String value = (origin instanceof String s) ? s : "none";
        return super.getLowCardinalityKeyValues(context).and("error_origin", value);
    }
}
```

**4.5 Classifying failures — `ErrorOriginAdvice.java`**

```java
package com.budgetguard.common.metrics;

import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class ErrorOriginAdvice {

    /** Thrown by the shared RestClient wrapper on dependency 5xx or timeout. */
    public static final class DownstreamException extends RuntimeException {
        private final String dependency;
        public DownstreamException(String dependency, String message, Throwable cause) {
            super(message, cause);
            this.dependency = dependency;
        }
        public String dependency() { return dependency; }
    }

    @ExceptionHandler(DownstreamException.class)
    ResponseEntity<ProblemDetail> downstream(DownstreamException ex, HttpServletRequest req) {
        return fail(req, "dep:" + ex.dependency(), HttpStatus.BAD_GATEWAY, ex.getMessage());
    }

    @ExceptionHandler(CallNotPermittedException.class)
    ResponseEntity<ProblemDetail> breakerOpen(CallNotPermittedException ex, HttpServletRequest req) {
        // Breaker names are the dependency names by convention: "auth-service", "inventory-service"
        return fail(req, "dep:" + ex.getCausingCircuitBreakerName(), HttpStatus.SERVICE_UNAVAILABLE, ex.getMessage());
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ProblemDetail> own(Exception ex, HttpServletRequest req) {
        return fail(req, "self", HttpStatus.INTERNAL_SERVER_ERROR, "internal error");
    }

    private ResponseEntity<ProblemDetail> fail(HttpServletRequest req, String origin, HttpStatus status, String detail) {
        req.setAttribute(ErrorOriginObservationConvention.ATTR, origin);
        return ResponseEntity.status(status).body(ProblemDetail.forStatusAndDetail(status, detail));
    }
}
```

**4.6 Generated recording rules — `rules/generated/auth-service.availability.yaml` (excerpt of one SLO; the generator emits every window an alert references)**

```yaml
groups:
  - name: bg:auth-service:availability:ratios
    interval: 30s
    rules:
      - record: bg:error_ratio:rate5m
        labels: {slo: availability}
        expr: |
          sum by (service) (rate(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*",status=~"5.."}[5m]))
          /
          sum by (service) (rate(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*"}[5m]))
      - record: bg:self_error_ratio:rate5m
        labels: {slo: availability}
        expr: |
          sum by (service) (rate(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*",status=~"5..",error_origin="self"}[5m]))
          /
          sum by (service) (rate(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*"}[5m]))
      - record: bg:requests:rate1h
        labels: {slo: availability}
        expr: sum by (service) (rate(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*"}[1h]))
      # … identical shapes for 30m, 1h, 6h, 3d — one rule per window referenced below
  - name: bg:auth-service:availability:30d
    interval: 5m
    rules:
      - record: bg:error_ratio:increase30d
        labels: {slo: availability}
        expr: |
          sum by (service) (increase(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*",status=~"5.."}[30d]))
          /
          sum by (service) (increase(http_server_requests_seconds_count{service="auth-service",uri!~"/actuator.*"}[30d]))
```

**4.7 Generated alerts — Workbook tiers with a traffic floor**

```yaml
groups:
  - name: bg:auth-service:availability:alerts
    rules:
      - alert: ErrorBudgetBurnPage
        expr: |
          (
            (bg:error_ratio:rate1h{service="auth-service",slo="availability"} > (14.4 * 0.0005)
             and bg:error_ratio:rate5m{service="auth-service",slo="availability"} > (14.4 * 0.0005))
            or
            (bg:error_ratio:rate6h{service="auth-service",slo="availability"} > (6 * 0.0005)
             and bg:error_ratio:rate30m{service="auth-service",slo="availability"} > (6 * 0.0005))
          )
          and on (service, slo) (bg:requests:rate1h{service="auth-service",slo="availability"} * 3600 >= 600)
        labels: {severity: page, incident_type: availability_burn, service: auth-service, slo: availability}
        annotations:
          summary: "auth-service is spending error budget fast"
          budgetguard_url: "https://budgetguard.example.dev/services/auth-service"
      - alert: ErrorBudgetBurnTicket
        expr: |
          bg:error_ratio:rate3d{service="auth-service",slo="availability"} > (1 * 0.0005)
          and bg:error_ratio:rate6h{service="auth-service",slo="availability"} > (1 * 0.0005)
        labels: {severity: ticket, incident_type: availability_slow_burn, service: auth-service, slo: availability}
      - alert: SLIIntegrityLost          # the B-5 fix: silence is not health
        expr: |
          up{job="auth-service"} == 0
          or absent(http_server_requests_seconds_count{service="auth-service"})
          or probe_success{target="auth-service"} == 0
        for: 2m
        labels: {severity: page, incident_type: sli_integrity, service: auth-service}
```

**4.8 Rule unit test — `rules/tests/auth-page.test.yaml`**

```yaml
rule_files: [../generated/auth-service.availability.yaml, ../generated/auth-service.alerts.yaml]
evaluation_interval: 30s
tests:
  - interval: 1m
    input_series:
      # 1,000 requests/min, of which 10/min are 5xx: error ratio 1% = burn 20x on a 99.95% SLO
      - series: 'http_server_requests_seconds_count{service="auth-service",uri="/login",status="200",error_origin="none"}'
        values: '0+990x180'
      - series: 'http_server_requests_seconds_count{service="auth-service",uri="/login",status="500",error_origin="self"}'
        values: '0+10x180'
    alert_rule_test:
      - eval_time: 70m
        alertname: ErrorBudgetBurnPage
        exp_alerts:
          - exp_labels: {severity: page, incident_type: availability_burn, service: auth-service, slo: availability}
            exp_annotations:
              summary: "auth-service is spending error budget fast"
              budgetguard_url: "https://budgetguard.example.dev/services/auth-service"
```

**4.9 Budget math — `platform/budgetguard/budget.py`**

```python
"""Error-budget arithmetic. Pure functions, no I/O: every number in a decision record is recomputable."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

WINDOW_HOURS_30D = 720.0


class Integrity(StrEnum):
    OK = "ok"
    LOW_TRAFFIC = "low_traffic"   # fewer events than the spec's floor
    STALE = "stale"               # scrape down or series absent
    NO_DATA = "no_data"           # never seen


@dataclass(frozen=True, slots=True)
class Counts:
    bad: float
    total: float

    def error_ratio(self) -> float | None:
        if self.total <= 0 or math.isnan(self.total) or math.isnan(self.bad):
            return None                       # unknown, never "0% errors"
        return min(max(self.bad / self.total, 0.0), 1.0)


def allowed_error_ratio(objective: float) -> float:
    if not 0.0 < objective < 1.0:
        raise ValueError(f"objective must be in (0, 1), got {objective}")
    return 1.0 - objective


def burn_rate(error_ratio: float, objective: float) -> float:
    """1.0 = spending exactly on plan; 14.4 = spending 14.4x faster than plan."""
    return error_ratio / allowed_error_ratio(objective)


def budget_remaining(counts: Counts, objective: float) -> float | None:
    """Fraction of the window's budget left; negative means overspent (kept, not clipped)."""
    ratio = counts.error_ratio()
    if ratio is None:
        return None
    return 1.0 - ratio / allowed_error_ratio(objective)


def budget_spent(burn: float, hours: float, window_hours: float = WINDOW_HOURS_30D) -> float:
    """Fraction of the window's budget spent by `burn` sustained for `hours`. 14.4x for 1h -> 0.02."""
    return burn * hours / window_hours


def hours_to_exhaustion(remaining: float, burn: float, window_hours: float = WINDOW_HOURS_30D) -> float | None:
    """At a constant burn, hours until remaining budget hits zero. 14.4x from full -> 50h."""
    if burn <= 0:
        return None
    return max(remaining, 0.0) * window_hours / burn


def budget_minutes(objective: float, window_hours: float = WINDOW_HOURS_30D) -> float:
    """Time-equivalent budget for a full outage at constant traffic. 0.999 over 30d -> 43.2 min."""
    return allowed_error_ratio(objective) * window_hours * 60.0
```

```python
# platform/tests/test_budget.py — the interview arithmetic, pinned
import pytest
from budgetguard.budget import Counts, budget_minutes, budget_remaining, budget_spent, burn_rate, hours_to_exhaustion

def test_workbook_ladder():
    assert budget_spent(14.4, 1) == pytest.approx(0.02)
    assert budget_spent(6, 6) == pytest.approx(0.05)
    assert budget_spent(1, 72) == pytest.approx(0.10)

def test_14_4x_takes_50_hours_not_one():
    assert hours_to_exhaustion(1.0, 14.4) == pytest.approx(50.0)

def test_budget_minutes():
    assert budget_minutes(0.999) == pytest.approx(43.2)
    assert budget_minutes(0.9995) == pytest.approx(21.6)

def test_overspend_is_negative_not_clipped():
    assert budget_remaining(Counts(bad=30, total=10_000), 0.999) == pytest.approx(-2.0)

def test_no_traffic_is_unknown_not_healthy():
    assert budget_remaining(Counts(bad=0, total=0), 0.999) is None
    assert burn_rate(0.01, 0.9995) == pytest.approx(20.0)
```

**4.10 Deploy pricing — `platform/budgetguard/pricing.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChangeFeatures:
    lines_changed: int
    touches_migration: bool
    config_only: bool
    dependency_bump: bool


@dataclass(frozen=True, slots=True)
class PricingPolicy:
    prior_alpha: float = 2.0        # Beta(2, 18): prior mean CFR 10%
    prior_beta: float = 18.0
    default_failure_cost_pct: float = 5.0
    w_config_only: float = 0.5
    w_migration: float = 2.0
    w_dependency: float = 1.5
    size_tiers: tuple[tuple[int, float], ...] = ((200, 1.0), (500, 1.25))
    size_max: float = 1.5
    multiplier_cap: float = 5.0


def posterior_cfr(failures: int, deploys: int, p: PricingPolicy) -> float:
    return (p.prior_alpha + failures) / (p.prior_alpha + p.prior_beta + deploys)


def size_factor(lines: int, p: PricingPolicy) -> float:
    for limit, factor in p.size_tiers:
        if lines <= limit:
            return factor
    return p.size_max


def risk_multiplier(f: ChangeFeatures, p: PricingPolicy) -> float:
    m = size_factor(f.lines_changed, p)
    if f.config_only:
        m *= p.w_config_only
    if f.touches_migration:
        m *= p.w_migration
    if f.dependency_bump:
        m *= p.w_dependency
    return min(m, p.multiplier_cap)


def price_pct(f: ChangeFeatures, failures: int, deploys: int,
              median_failure_cost_pct: float | None, p: PricingPolicy) -> float:
    """Expected budget cost of this deploy, in percent of the 30-day budget."""
    cost = median_failure_cost_pct if median_failure_cost_pct is not None else p.default_failure_cost_pct
    return posterior_cfr(failures, deploys, p) * risk_multiplier(f, p) * cost


def affordable_pct(remaining_pct: float, reserve_pct: float, spend_fraction: float) -> float:
    return max(0.0, spend_fraction * (remaining_pct - reserve_pct))
```

**4.11 Gate engine — `platform/budgetguard/gate.py`**

```python
"""Chain-of-responsibility gate. Deterministic: same GateInput -> same Decision (D-8)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Callable, Literal

from .budget import Integrity
from .pricing import affordable_pct


class Verdict(StrEnum):
    ADMIT = "admit"
    ADMIT_UNVERIFIED = "admit_unverified"   # fail-open path: admitted, audited, on-call paged
    BLOCK = "block"


class Lane(StrEnum):
    ROLLBACK = "rollback"           # image digest that was previously healthy in production
    INCIDENT_FIX = "incident_fix"   # PR linked to an open incident on this service


@dataclass(frozen=True, slots=True)
class GateInput:
    service: str
    policy_version: str
    integrity: Integrity
    remaining_pct: float | None
    active_page: bool
    lane: Lane | None
    price_pct: float
    reserve_pct: float
    spend_fraction: float
    failure_mode: Literal["fail_open", "fail_closed"]

    def digest(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reason_code: str
    reason: str
    price_pct: float | None = None
    affordable_pct: float | None = None
    canary_profile: tuple[int, ...] = (25, 100)


Rule = Callable[[GateInput], Decision | None]


def canary_profile(remaining_pct: float | None) -> tuple[int, ...]:
    if remaining_pct is None or remaining_pct < 20:
        return (5, 25, 50, 100)
    if remaining_pct < 50:
        return (10, 50, 100)
    return (25, 100)


def rollback_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.ROLLBACK:
        return Decision(Verdict.ADMIT, "ROLLBACK_LANE", "Rollback to a previously healthy digest is free",
                        price_pct=0.0, canary_profile=(100,))
    return None


def integrity_check(i: GateInput) -> Decision | None:
    if i.integrity is Integrity.OK and i.remaining_pct is not None:
        return None
    if i.failure_mode == "fail_closed":
        return Decision(Verdict.BLOCK, "SLI_UNKNOWN", f"SLI integrity is {i.integrity}; service is fail-closed")
    return Decision(Verdict.ADMIT_UNVERIFIED, "SLI_UNKNOWN",
                    f"SLI integrity is {i.integrity}; admitted under fail-open, on-call paged",
                    canary_profile=(5, 25, 50, 100))


def incident_fix_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.INCIDENT_FIX:
        return Decision(Verdict.ADMIT, "INCIDENT_FIX_LANE", "Fix for the active incident; forced 5% canary",
                        price_pct=i.price_pct, canary_profile=(5, 25, 50, 100))
    return None


def hard_stop(i: GateInput) -> Decision | None:
    if i.active_page:
        return Decision(Verdict.BLOCK, "ACTIVE_PAGE", f"{i.service} has a firing page-level burn alert")
    return None


def priced_admission(i: GateInput) -> Decision:
    assert i.remaining_pct is not None
    afford = affordable_pct(i.remaining_pct, i.reserve_pct, i.spend_fraction)
    if i.price_pct <= afford:
        return Decision(Verdict.ADMIT, "WITHIN_BUDGET",
                        f"Price {i.price_pct:.2f}% <= affordable {afford:.2f}%",
                        price_pct=i.price_pct, affordable_pct=afford,
                        canary_profile=canary_profile(i.remaining_pct))
    return Decision(Verdict.BLOCK, "OVER_BUDGET",
                    f"Price {i.price_pct:.2f}% > affordable {afford:.2f}%",
                    price_pct=i.price_pct, affordable_pct=afford)


CHAIN: tuple[Rule, ...] = (rollback_lane, integrity_check, incident_fix_lane, hard_stop)


def evaluate(i: GateInput) -> Decision:
    for rule in CHAIN:
        decision = rule(i)
        if decision is not None:
            return decision
    return priced_admission(i)
```

**4.12 Exact unfreeze ETA — `platform/budgetguard/eta.py`**

```python
"""D-5: when does the rolling 30-day budget climb back above the threshold?

Replays the window forward one 5-minute bucket at a time. Each step drops the oldest
bucket and appends a projected one (recent traffic, assumed future error ratio).
O(n) with running sums: 8,640 buckets per 30 days.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

BUCKET = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Bucket:
    start: datetime
    bad: float
    total: float


def unfreeze_eta(window: list[Bucket], objective: float, threshold_pct: float,
                 future_error_ratio: float = 0.0, horizon_buckets: int = 8_640) -> datetime | None:
    """`window` = the last 30 days of buckets, oldest first, ending at now."""
    if not window:
        return None
    allowed = 1.0 - objective
    recent = window[-288:]                                  # last 24h sets projected traffic
    traffic = sum(b.total for b in recent) / len(recent)
    q = deque(window)
    bad = sum(b.bad for b in q)
    total = sum(b.total for b in q)
    t = window[-1].start

    def remaining_pct() -> float:
        return 100.0 * (1.0 - (bad / total) / allowed) if total > 0 else 100.0

    if remaining_pct() >= threshold_pct:
        return t + BUCKET
    for _ in range(horizon_buckets):
        old = q.popleft()
        bad -= old.bad
        total -= old.total
        t += BUCKET
        new = Bucket(t, future_error_ratio * traffic, traffic)
        q.append(new)
        bad += new.bad
        total += new.total
        if remaining_pct() >= threshold_pct:
            return t + BUCKET
    return None   # not within 30 days at this future error ratio: say so, don't guess
```

**4.13 GitHub App protection-rule handler — `platform/budgetguard/integrations/github_app.py`**

One GitHub environment per service (`production-auth-service`, …), so the gate knows the service from the environment name without trusting anything in the workflow.

```python
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from ..config import settings
from ..queue import enqueue_once

router = APIRouter()
ENV_PREFIX = "production-"


def verify_signature(body: bytes, signature: str | None) -> None:
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(401, "missing signature")
    expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "bad signature")


@router.post("/v1/github/webhook")
async def github_webhook(request: Request,
                         x_hub_signature_256: str | None = Header(default=None),
                         x_github_event: str = Header(...),
                         x_github_delivery: str = Header(...)) -> dict:
    body = await request.body()
    verify_signature(body, x_hub_signature_256)
    if x_github_event != "deployment_protection_rule":
        return {"ignored": x_github_event}
    payload = json.loads(body)
    environment: str = payload["environment"]
    if not environment.startswith(ENV_PREFIX):
        return {"ignored": environment}
    job = {
        "service": environment.removeprefix(ENV_PREFIX),
        "environment": environment,
        "callback_url": payload["deployment_callback_url"],
        "installation_id": payload["installation"]["id"],
        "sha": payload["deployment"]["sha"],
        "repo": payload["repository"]["full_name"],
    }
    # Idempotent on the delivery ID: GitHub redeliveries never evaluate twice.
    accepted = await enqueue_once("bg:gate-evaluations", key=x_github_delivery, job=job)
    return {"accepted": accepted}


async def post_review(job: dict, approved: bool, comment: str, token: str) -> None:
    """Called by the gate worker after the decision record is committed."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            job["callback_url"],
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"environment_name": job["environment"],
                  "state": "approved" if approved else "rejected",
                  "comment": comment[:1000]},
        )
        resp.raise_for_status()
```

## 5. Database design and choice

Verdict: PostgreSQL 18 with the TimescaleDB extension — chosen for its declarative data lifecycle and its scale path, not for volume. At 4 services the honest row count is small, and saying so in the interview is a strength.

**Sizing, stated honestly.** 8 SLO series × 288 five-minute buckets a day = 2,304 rows a day, about 0.9 million rows over 13 months. Plain PostgreSQL handles that easily. At the design target of 500 services × 4 SLOs, it is 576,000 rows a day, about 210 million a year — where hand-rolled partitioning, rollup cron jobs and retention scripts become the real cost. TimescaleDB replaces all three with declarations.

| Option | Fit for 5-min SLI buckets | Joins: incidents ↔ deploys ↔ decisions | Transactional decision records | Operational cost | Verdict |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL 18 + TimescaleDB | Hypertables, continuous aggregates that refresh correctly after late upserts, compression and retention policies | Full SQL, foreign keys | Yes | One database | Chosen |
| Plain PostgreSQL 18 | Fine at 4 services; needs manual partitions, rollup jobs and retention scripts at scale | Full SQL | Yes | One database, more code | Runner-up; say you would start here at a startup |
| InfluxDB | Strong time-series engine | None — incidents and deploys need a second database | No multi-table transactions | Two databases | Rejected |
| MongoDB | Workable documents | `$lookup` joins; decision records lose FK integrity | Multi-document transactions exist but add friction | Medium | Rejected |
| Cassandra | Built for very high write fan-out | No joins; denormalize per query | Lightweight transactions only | 3+ nodes, tuning | Rejected — 2,304 rows a day |
| Azure Cosmos DB | Managed, global | Per-container queries; no joins | Within a partition only | Cloud lock-in, RU billing | Rejected — breaks cloud neutrality |
| Prometheus alone | Live SLIs, alerts | No relational data | No | Already running | Kept as live source; not a system of record |

**Schema — `platform/sql/001_schema.sql`**

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE services (
    id            SMALLSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    owner         TEXT NOT NULL,
    failure_mode  TEXT NOT NULL DEFAULT 'fail_open' CHECK (failure_mode IN ('fail_open','fail_closed')),
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
    self_bad      DOUBLE PRECISION NOT NULL DEFAULT 0,
    integrity     TEXT NOT NULL DEFAULT 'ok',
    PRIMARY KEY (slo_id, bucket_start)
);
SELECT create_hypertable('sli_buckets', by_range('bucket_start', INTERVAL '7 days'));

ALTER TABLE sli_buckets SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'slo_id',
    timescaledb.compress_orderby   = 'bucket_start DESC'
);
SELECT add_compression_policy('sli_buckets', INTERVAL '7 days');
SELECT add_retention_policy('sli_buckets', INTERVAL '400 days');   -- 13 months for quarterly reports

CREATE MATERIALIZED VIEW sli_hourly WITH (timescaledb.continuous) AS
SELECT slo_id,
       time_bucket(INTERVAL '1 hour', bucket_start) AS hour,
       sum(total) AS total, sum(bad) AS bad, sum(self_bad) AS self_bad
FROM sli_buckets
GROUP BY slo_id, hour
WITH NO DATA;
SELECT add_continuous_aggregate_policy('sli_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '5 minutes');

CREATE TABLE policy_versions (
    version          TEXT PRIMARY KEY,                -- git SHA of policy/freeze-policy.yaml
    backtest_report  JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE decisions (
    id               BIGSERIAL PRIMARY KEY,
    service_id       SMALLINT NOT NULL REFERENCES services(id),
    github_delivery  TEXT NOT NULL UNIQUE,            -- idempotency key
    github_run_id    BIGINT,
    policy_version   TEXT NOT NULL REFERENCES policy_versions(version),
    inputs_hash      TEXT NOT NULL,
    inputs           JSONB NOT NULL,                  -- everything needed to recompute the verdict
    verdict          TEXT NOT NULL CHECK (verdict IN ('admit','admit_unverified','block')),
    reason_code      TEXT NOT NULL,
    price_pct        NUMERIC(8,4),
    affordable_pct   NUMERIC(8,4),
    canary_profile   SMALLINT[] NOT NULL,
    brief_source     TEXT CHECK (brief_source IN ('llm','template')),
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX decisions_service_time ON decisions (service_id, decided_at DESC);

CREATE TABLE overrides (
    id           BIGSERIAL PRIMARY KEY,
    decision_id  BIGINT NOT NULL REFERENCES decisions(id),
    approver     TEXT NOT NULL,                       -- GitHub login, verified via OAuth
    reason       TEXT NOT NULL CHECK (length(reason) >= 20),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE deployments (
    id                 BIGSERIAL PRIMARY KEY,
    service_id         SMALLINT NOT NULL REFERENCES services(id),
    decision_id        BIGINT REFERENCES decisions(id),
    sha                TEXT NOT NULL,
    image_digest       TEXT NOT NULL,
    pr_number          INT,
    lines_changed      INT NOT NULL DEFAULT 0,
    touches_migration  BOOLEAN NOT NULL DEFAULT false,
    config_only        BOOLEAN NOT NULL DEFAULT false,
    dependency_bump    BOOLEAN NOT NULL DEFAULT false,
    lead_time          INTERVAL,
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ,
    status             TEXT NOT NULL CHECK (status IN ('in_progress','success','failure','rolled_back')),
    is_change_failure  BOOLEAN                         -- NULL until the 60-minute window closes
);
CREATE INDEX deployments_service_time ON deployments (service_id, started_at DESC);

CREATE TABLE incidents (
    id                       BIGSERIAL PRIMARY KEY,
    service_id               SMALLINT NOT NULL REFERENCES services(id),
    culprit_service_id       SMALLINT REFERENCES services(id),   -- D-3 attribution
    suspected_deployment_id  BIGINT REFERENCES deployments(id),
    fingerprint              TEXT NOT NULL UNIQUE,                -- Alertmanager fingerprint
    incident_type            TEXT NOT NULL,
    severity                 TEXT NOT NULL CHECK (severity IN ('page','ticket')),
    started_at               TIMESTAMPTZ NOT NULL,
    resolved_at              TIMESTAMPTZ,
    budget_spent_pct         NUMERIC(8,4)
);
CREATE INDEX incidents_type_time ON incidents (incident_type, started_at DESC);
```

**Key queries**

```sql
-- 1. Request-weighted 30-day budget remaining per SLO (can go negative)
SELECT s.name AS service, o.name AS slo, o.objective,
       100 * (1 - (sum(b.bad) / NULLIF(sum(b.total), 0)) / (1 - o.objective)) AS remaining_pct
FROM sli_buckets b
JOIN slos o ON o.id = b.slo_id
JOIN services s ON s.id = o.service_id
WHERE b.bucket_start >= now() - INTERVAL '30 days'
GROUP BY s.name, o.name, o.objective;

-- 2. MTTR per incident type: median and p90, not mean
SELECT incident_type,
       count(*) AS incidents,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY resolved_at - started_at) AS mttr_p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY resolved_at - started_at) AS mttr_p90
FROM incidents
WHERE resolved_at IS NOT NULL AND started_at >= now() - INTERVAL '30 days'
GROUP BY incident_type;

-- 3. Deploy change rate and change-failure rate per service per week
SELECT s.name, time_bucket(INTERVAL '7 days', d.started_at) AS week,
       count(*) AS deploys,
       round(100.0 * count(*) FILTER (WHERE d.is_change_failure) / NULLIF(count(*) FILTER (WHERE d.is_change_failure IS NOT NULL), 0), 1) AS cfr_pct
FROM deployments d JOIN services s ON s.id = d.service_id
WHERE d.status <> 'in_progress'
GROUP BY s.name, week ORDER BY week DESC;

-- 4. Budget chargeback: self-inflicted vs dependency-inflicted share (D-3)
SELECT s.name,
       round(100.0 * sum(b.self_bad) / NULLIF(sum(b.bad), 0), 1) AS self_share_pct
FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
WHERE o.name = 'availability' AND b.bucket_start >= now() - INTERVAL '30 days'
GROUP BY s.name;
```

## 6. Cache and messaging choice

Verdict: Valkey 9.1 (Redis 8 is interchangeable) as an accelerator and work queue — never as a source of truth. If Valkey disappears, the gate still decides correctly from Prometheus and PostgreSQL, just slower, because idempotency is also enforced by a unique key in PostgreSQL.

| Key or stream | Type | TTL / retention | Purpose |
| --- | --- | --- | --- |
| `bg:verdict:{service}:{sha}` | String (JSON decision) | 30 s; deleted on any alert for the service | Re-runs of the same workflow get the same answer without re-querying Prometheus |
| `bg:lock:eval:{service}` | `SET NX PX 15000` | 15 s | Single-flight: one evaluation per service at a time, so 10 parallel merges cause 1 Prometheus fan-out |
| `bg:delivery:{github_delivery_id}` | `SET NX EX` | 7 days | Fast-path webhook dedupe (PostgreSQL `UNIQUE (github_delivery)` is the backstop) |
| `bg:promq:{sha256(query,time)}` | String | 15 s | Dashboard query cache; the gate path bypasses it for the 5m window |
| `bg:gate-evaluations` | Stream, consumer group `gate-workers` | `MAXLEN ~ 10000` | Webhook → worker hand-off; `XAUTOCLAIM` reclaims jobs from a crashed worker after 60 s |
| `bg:deploy-events` | Stream, consumer group `deploy-recorders` | `MAXLEN ~ 10000` | `deployment_status` events → deploy rows and 60-minute change-failure timers |
| `bg:briefs` | Stream | `MAXLEN ~ 10000` | Decision committed → Freeze Brief rendered and posted; a sweeper re-enqueues decisions with no brief after 60 s (outbox pattern using the decisions table) |

**Why NOT the alternatives**

| Option | Verdict | Reason |
| --- | --- | --- |
| Valkey / Redis | Chosen | Cache, locks and streams in one process; consumer groups and pending-entry lists cover at-least-once delivery |
| Kafka | Rejected | About 50 events a day. Kafka 4.0 removed ZooKeeper, so a single KRaft node would run, but replay, partitions and a schema registry solve problems this system does not have. Named as the path for org-wide deploy-event fan-out |
| RabbitMQ | Rejected | A second broker for one queue; Valkey is already needed for the cache and locks |
| ZooKeeper | Not applicable | A coordination service, not a cache or queue. The one coordination need — a single active snapshotter — uses a Kubernetes `Lease` |
| PostgreSQL `SELECT … FOR UPDATE SKIP LOCKED` | Credible runner-up | Would work at this volume; Valkey wins only because the cache and locks need it anyway. Say this out loud — it shows you sized the problem |

## 7. Design patterns

Each pattern below exists because it removes a specific failure; name the failure first in the interview, then the pattern.

| Pattern | Where | The problem it removes |
| --- | --- | --- |
| Chain of Responsibility | `gate.evaluate` (§4.11) | Order matters — rollback before integrity before page before price — and each rule is testable alone |
| Specification | `slo/*.yaml` and `policy/freeze-policy.yaml` | One declared truth generates rules, alerts, gate inputs and dashboards; no drift between them |
| Template Method | Rule generator: shared ratio/burn/alert skeleton, per-SLI-type hooks (ratio, latency bucket, async delivery) | Every SLI type gets every window an alert references (kills B-4) |
| Strategy | SLI sources (`PrometheusSource`, `TimescaleSource`); risk-multiplier feature extractors | Swap the live source for the replay source in the backtester without touching the engine |
| Adapter | LLM providers behind one `BriefWriter` protocol; GitHub App vs local CLI gate | No vendor or model name outside config (your standing rule) |
| Decorator | `CachedPromClient(PromClient)`, `CircuitBreakingPromClient` | Caching and breaking stack without editing the client |
| Value Object | Frozen dataclasses `GateInput`, `Decision`, `Counts` | Hashable inputs make `inputs_hash` and replay possible (D-8) |
| Idempotent Receiver | Webhooks keyed on GitHub delivery ID and Alertmanager fingerprint | Redeliveries never double-count deploys or reopen incidents |
| Transactional Outbox (lite) | Decision row committed first; brief sweeper re-sends missing briefs | A crash between "decided" and "notified" never loses the notification |
| Single-flight | `bg:lock:eval:{service}` | 10 simultaneous merges cost one Prometheus fan-out |
| Leader Election | Kubernetes `Lease` for the snapshotter | Two replicas, one writer, no duplicate buckets |
| Circuit Breaker + Bulkhead | Platform clients for Prometheus, GitHub, Slack, LLM (separate pools); Resilience4j in the services | A slow LLM cannot starve verdicts; a slow Slack cannot block GitHub replies |

**The adapter contract — `platform/budgetguard/integrations/llm.py`**

```python
from __future__ import annotations

from typing import Protocol

from ..briefs import BriefInput, FreezeBrief, render_template


class BriefWriter(Protocol):
    name: str
    async def write(self, record: BriefInput, timeout_s: float) -> FreezeBrief: ...


class TemplateWriter:
    name = "template"
    async def write(self, record: BriefInput, timeout_s: float) -> FreezeBrief:
        return render_template(record)            # deterministic, always available


class FallbackWriter:
    """Primary model with a hard timeout and grounding check; template on any failure."""
    def __init__(self, primary: BriefWriter, fallback: BriefWriter, grounding) -> None:
        self.primary, self.fallback, self.grounding = primary, fallback, grounding
        self.name = f"{primary.name}|{fallback.name}"

    async def write(self, record: BriefInput, timeout_s: float) -> FreezeBrief:
        try:
            brief = await self.primary.write(record, timeout_s)
            if self.grounding.passes(brief, record):   # every number must exist in the record
                return brief
        except Exception:
            pass
        return await self.fallback.write(record, timeout_s)
```

The concrete `OpenAIWriter`, `AnthropicWriter` and `OllamaWriter` live in their own modules and are chosen by `llm.yaml`; nothing else imports them.

## 8. Resilience, mitigation, availability and consistency

The gate has two different failure modes and Rev 1 conflated them: if Prometheus is down, BudgetGuard can still answer ("unknown", fail-open with audit); if BudgetGuard itself is down, GitHub holds the deploy, because a protection rule that never answers is fail-closed by design. Say both, and name the audited break-glass.

**Resilience patterns**

| Pattern | Where | Setting |
| --- | --- | --- |
| Timeout | Every outbound call: Prometheus 3 s, GitHub 10 s, Slack 5 s, LLM 4 s; service-to-service 2 s | No call without a deadline |
| Retry with jittered backoff | GitHub callback (up to 30 min), Slack, Alertmanager webhook receipt | Only idempotent operations retry |
| Circuit breaker | Platform clients (per dependency); Resilience4j between services | Breaker state exported as a metric and shown on the dashboard |
| Bulkhead | Separate worker pools for verdicts, briefs and deploy events; per-channel pools in notifications-service | A Slack outage cannot slow a verdict |
| Fallback | Template brief; Prometheus `increase30d` rule when TimescaleDB is down; inventory serves cached stock | Degrade, never block repairs |
| Idempotency | Delivery IDs, alert fingerprints, `(slo_id, bucket_start)` upserts | At-least-once delivery, exactly-once effect |
| Load shedding | Dashboard query cache; gate path never shares a pool with the dashboard | Viewing graphs cannot slow deploy decisions |

**Mitigation — what each dependency failure does to the gate**

| Failure | Gate behaviour | Signal |
| --- | --- | --- |
| Prometheus unreachable | Integrity = STALE → service `failure_mode` applies; fail-open admits as `admit_unverified`. Rollback lane still works because it runs before the integrity rule — so a team can choose fail-closed for auth without ever blocking a rollback | Page: `BudgetGuardBlind` |
| Alertmanager down | No effect on verdicts: active pages are read from Prometheus' own `ALERTS{alertstate="firing",severity="page"}` series | Page via Prometheus self-alert |
| TimescaleDB down | 30-day budget read from the `increase30d` recording rule; decision record buffered to the `bg:records-pending` stream and replayed; ETA reports "unavailable" | Ticket |
| Valkey down | Webhook handler evaluates inline (still under 10 s); PostgreSQL unique key dedupes | Ticket |
| GitHub API down | Verdict recorded; callback retried with backoff; deploy waits | Ticket |
| LLM or Slack down | Template brief; Slack retried; verdict unaffected | None — expected degradation |
| BudgetGuard itself down | GitHub holds the deploy (protection rule unanswered). Break-glass: repository admins can bypass protection rules on a public repo; GitHub logs the bypass, and BudgetGuard ingests it as an override on recovery | BudgetGuard's own SLO alert, probed every minute |
| Three or more services burning at once | Treated as a platform incident: page, freeze only services whose self-inflicted burn exceeds the page threshold | Page: `PlatformWideBurn` |

**Availability and consistency, per data path**

| Data path | Choice | Why |
| --- | --- | --- |
| Gate verdict | Availability with bounded staleness: 30 s cache, invalidated immediately by any alert for that service | A deploy decided on 20-second-old data is fine; one decided during a page is not |
| Decision record | Strong consistency: committed in a PostgreSQL transaction before GitHub is answered | The audit trail must never disagree with what GitHub was told |
| Override approval | Read-your-writes: row committed, then cache deleted, then callback posted | The approver sees the deploy proceed immediately |
| SLI history | Eventual: buckets land within 6 minutes; idempotent upserts let the snapshotter rerun any range | Backtests and ETAs tolerate minutes of lag |
| Dashboards | Eventual: continuous aggregates refresh every 5 minutes; live tiles read Prometheus | Humans read trends, not milliseconds |

**BudgetGuard's own SLO (meta-SLO).** 99.9% of synthetic protection-rule webhooks, sent every minute by a blackbox probe, get a verdict within 5 seconds over 30 days. Two platform replicas with a PodDisruptionBudget of `minAvailable: 1`; single PostgreSQL in the demo with nightly `pg_dump` plus WAL archiving to object storage, and CloudNativePG named as the HA path.

## 9. Docker, Kubernetes and deployment strategies

Local development runs on docker-compose with every image tag in one `.env.versions` file; the public deploy runs on k3s with Helm, and the four services ship as Argo Rollouts canaries whose steps come from the gate's `canary_profile`.

**9.1 `deploy/compose/.env.versions`** — the only place image tags live (re-check each at kickoff)

```bash
PROMETHEUS_TAG=v3.13.2
ALERTMANAGER_TAG=v0.34.1
GRAFANA_TAG=13.2.2
TIMESCALE_TAG=2.27.0-pg18      # confirm the exact tag on Docker Hub
VALKEY_TAG=9.1
BLACKBOX_TAG=pin-at-kickoff
```

**9.2 `deploy/compose/docker-compose.yml`**

```yaml
name: budgetguard
x-svc: &svc
  restart: unless-stopped
  environment:
    JAVA_TOOL_OPTIONS: "-XX:MaxRAMPercentage=75"
  healthcheck:
    test: ["CMD", "wget", "-qO-", "http://localhost:8080/actuator/health/readiness"]
    interval: 10s
    retries: 12

services:
  auth-service:          { <<: *svc, build: ../../services/auth-service, ports: ["8081:8080"] }
  inventory-service:     { <<: *svc, build: ../../services/inventory-service, ports: ["8083:8080"] }
  notifications-service: { <<: *svc, build: ../../services/notifications-service, ports: ["8084:8080"] }
  orders-service:
    <<: *svc
    build: ../../services/orders-service
    ports: ["8082:8080"]
    depends_on:
      auth-service: { condition: service_healthy }
      inventory-service: { condition: service_healthy }

  timescaledb:
    image: timescale/timescaledb:${TIMESCALE_TAG}
    environment: { POSTGRES_DB: budgetguard, POSTGRES_USER: bg, POSTGRES_PASSWORD: ${PG_PASSWORD:?set in .env} }
    volumes: ["pg:/home/postgres/pgdata/data", "../../platform/sql:/docker-entrypoint-initdb.d:ro"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U bg -d budgetguard"], interval: 5s, retries: 20 }

  valkey:
    image: valkey/valkey:${VALKEY_TAG}
    command: ["valkey-server", "--appendonly", "yes"]
    healthcheck: { test: ["CMD", "valkey-cli", "ping"], interval: 5s, retries: 20 }

  prometheus:
    image: prom/prometheus:${PROMETHEUS_TAG}
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=35d          # a 30-day SLO needs more than the 15d default
      - --web.enable-lifecycle
    volumes: ["./prometheus.yml:/etc/prometheus/prometheus.yml:ro", "../../rules/generated:/etc/prometheus/rules:ro", "prom:/prometheus"]
    ports: ["9090:9090"]

  alertmanager:
    image: prom/alertmanager:${ALERTMANAGER_TAG}
    volumes: ["./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro"]

  blackbox:
    image: prom/blackbox-exporter:${BLACKBOX_TAG}

  grafana:
    image: grafana/grafana:${GRAFANA_TAG}
    volumes: ["../../grafana:/etc/grafana/provisioning:ro", "grafana:/var/lib/grafana"]
    ports: ["3001:3000"]

  platform-api:
    build: ../../platform
    command: ["uvicorn", "budgetguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
    env_file: .env
    depends_on:
      timescaledb: { condition: service_healthy }
      valkey: { condition: service_healthy }
    ports: ["8000:8000"]
  gate-worker:   { build: ../../platform, command: ["python", "-m", "budgetguard.workers.gate_worker"], env_file: .env, depends_on: [platform-api] }
  snapshotter:   { build: ../../platform, command: ["python", "-m", "budgetguard.workers.snapshotter"], env_file: .env, depends_on: [platform-api] }

  dashboard:
    build: ../../dashboard
    environment: { BUDGETGUARD_API: http://platform-api:8000 }
    ports: ["3000:3000"]

volumes: { pg: {}, prom: {}, grafana: {} }
```

**9.3 Platform image — `platform/Dockerfile`**

```dockerfile
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY budgetguard ./budgetguard
RUN uv sync --frozen --no-dev

FROM python:3.14-slim
RUN useradd --uid 10001 --create-home bg
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "budgetguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Pin the `uv` image by digest in CI; `:latest` appears here only because the builder stage is discarded.

**9.4 Kubernetes layout**

| Namespace | Workloads | Notes |
| --- | --- | --- |
| `bg-apps` | 4 services as Argo `Rollout`s, 4 replicas each | PodMonitor with `podTargetLabels: [rollouts-pod-template-hash]` so canary and stable series are separable |
| `bg-platform` | `platform-api` (2 replicas, PDB), `gate-worker` (2), `snapshotter` (2, `Lease` leader), `dashboard` (2) | NetworkPolicy: only `/v1/github/webhook` and the dashboard are reachable from the ingress |
| `monitoring` | kube-prometheus-stack, blackbox exporter | Generated rules shipped as `PrometheusRule` CRs |
| `argo-rollouts` | Rollouts controller | Dashboard never exposed; port-forward only |
| `data` | TimescaleDB StatefulSet (1 replica, 20 Gi), Valkey | Nightly `pg_dump` CronJob to object storage |

**9.5 Budget-proportional canary — `AnalysisTemplate`**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: budget-burn-canary
  namespace: bg-apps
spec:
  args:
    - name: service
    - name: canary-hash
    - name: stable-hash
  metrics:
    - name: canary-has-traffic           # D-4 again: no traffic is not a pass
      interval: 1m
      count: 5
      failureLimit: 1
      successCondition: result[0] >= 30
      provider:
        prometheus:
          address: http://kube-prometheus-stack-prometheus.monitoring:9090
          query: sum(rate(http_server_requests_seconds_count{service="{{args.service}}",rollouts_pod_template_hash="{{args.canary-hash}}"}[2m])) * 60
    - name: canary-vs-stable-self-burn
      interval: 1m
      count: 10
      failureLimit: 2
      successCondition: result[0] <= 2
      provider:
        prometheus:
          address: http://kube-prometheus-stack-prometheus.monitoring:9090
          query: |
            (
              sum(rate(http_server_requests_seconds_count{service="{{args.service}}",rollouts_pod_template_hash="{{args.canary-hash}}",status=~"5..",error_origin="self"}[2m]))
              / sum(rate(http_server_requests_seconds_count{service="{{args.service}}",rollouts_pod_template_hash="{{args.canary-hash}}"}[2m]))
            )
            /
            clamp_min(
              sum(rate(http_server_requests_seconds_count{service="{{args.service}}",rollouts_pod_template_hash="{{args.stable-hash}}",status=~"5..",error_origin="self"}[2m]))
              / sum(rate(http_server_requests_seconds_count{service="{{args.service}}",rollouts_pod_template_hash="{{args.stable-hash}}"}[2m])),
              0.0005
            )
```

The Helm chart renders the Rollout's `steps` from `canary.weights` and `canary.bake`, which the deploy job sets from the gate's `canary_profile`. On k3s, exact weights need a traffic router (Traefik with the Rollouts Gateway API plugin); without one, weights are approximated by replica counts, which is why each service runs 4 replicas.

**9.6 Deployment strategy per component**

| Component | Strategy | Why |
| --- | --- | --- |
| The 4 services | Canary with budget-proportional steps and burn analysis | The system under test; the D-7 demo |
| BudgetGuard platform | Rolling, `maxUnavailable: 0`, `maxSurge: 1` | Stateless replicas; the gate must stay answerable during its own deploy |
| Database schema | Expand / migrate / contract with Alembic, one release apart | No release ever depends on a column the previous release lacks |
| Rules and policy | GitOps: generated in CI, applied as `PrometheusRule`; policy change gated by backtest | Rules are code with tests |
| Dashboard | Rolling | Stateless |
| Blue-green | Considered, rejected for services | Doubles capacity on a free-tier VM and gives one all-or-nothing switch instead of graded exposure |

## 10. CI/CD, the DevOps/MLOps wrapper and the eval harnesses

Three harnesses gate merges — rule correctness, policy backtest and brief grounding — and the deploy pipeline is GitHub Actions → Docker Hub → k3s, with BudgetGuard enforcing from outside the YAML. The trick that makes the gate unbypassable: cluster credentials are environment secrets, so a job that deletes its `environment:` line also loses the kubeconfig.

**10.1 Pipelines**

| Workflow | Trigger | Gates |
| --- | --- | --- |
| `ci.yml` | Every PR | Java and Python unit tests; `bg generate --check` (generated rules must match committed ones); `promtool check rules` and `promtool test rules`; Sloth parity diff; Helm lint |
| `policy-backtest.yml` | PR touching `policy/**` or `platform/budgetguard/{gate,pricing}.py` | Backtest vs `main`; regression thresholds below; report posted as a PR comment |
| `brief-eval.yml` | PR touching prompts, `llm.yaml` or `briefs.py`; nightly for the challenger model | Grounding ≥ 99%, fact recall ≥ 95%, p95 latency ≤ 4 s |
| `build-push.yml` | Push to `main` | Multi-arch images to Docker Hub tagged `sha-<commit>`; SBOM; cosign keyless signature |
| `deploy.yml` | After `build-push` | One job per changed service in `production-<service>`; BudgetGuard protection rule decides |

**10.2 `.github/workflows/deploy.yml`**

```yaml
name: deploy
on:
  workflow_run:
    workflows: [build-push]
    types: [completed]
    branches: [main]

permissions:
  contents: read
  id-token: write          # OIDC token for BudgetGuard reads

jobs:
  changes:
    if: github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-24.04
    outputs:
      services: ${{ steps.filter.outputs.changes }}
    steps:
      - uses: actions/checkout@v5
        with:
          ref: ${{ github.event.workflow_run.head_sha }}
          fetch-depth: 2
      - id: filter
        uses: dorny/paths-filter@v3
        with:
          base: ${{ github.event.workflow_run.head_sha }}~1
          filters: |
            auth-service: services/auth-service/**
            orders-service: services/orders-service/**
            inventory-service: services/inventory-service/**
            notifications-service: services/notifications-service/**

  deploy:
    needs: changes
    if: needs.changes.outputs.services != '[]'
    strategy:
      fail-fast: false
      max-parallel: 1                    # one service at a time keeps attribution clean
      matrix:
        service: ${{ fromJSON(needs.changes.outputs.services) }}
    environment: production-${{ matrix.service }}   # BudgetGuard's protection rule runs here;
                                                      # KUBECONFIG exists only in this environment
    runs-on: ubuntu-24.04
    env:
      SERVICE: ${{ matrix.service }}
      SHA: ${{ github.event.workflow_run.head_sha }}
      BUDGETGUARD_URL: ${{ vars.BUDGETGUARD_URL }}
    steps:
      - uses: actions/checkout@v5
        with:
          ref: ${{ env.SHA }}

      - name: Fetch this run's decision (canary profile)
        id: decision
        run: |
          set -euo pipefail
          OIDC=$(curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
                 "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=budgetguard" | jq -r .value)
          if ! curl -sS --fail --max-time 10 -H "Authorization: Bearer $OIDC" \
               "$BUDGETGUARD_URL/v1/decisions?service=$SERVICE&run_id=$GITHUB_RUN_ID" -o decision.json; then
            # Approved but profile unreadable: take the most conservative canary, never the fastest.
            jq -n '{canary_profile:[5,25,50,100], bake:"10m"}' > decision.json
          fi
          echo "weights=$(jq -c .canary_profile decision.json)" >> "$GITHUB_OUTPUT"
          echo "bake=$(jq -r .bake decision.json)" >> "$GITHUB_OUTPUT"

      - uses: azure/setup-helm@v4

      - name: Resolve image digest
        id: image
        run: |
          DIGEST=$(docker buildx imagetools inspect "${{ vars.DOCKERHUB_NS }}/$SERVICE:sha-$SHA" \
                   --format '{{json .Manifest}}' | jq -r .digest)
          echo "digest=$DIGEST" >> "$GITHUB_OUTPUT"

      - name: Canary rollout
        env:
          KUBECONFIG_DATA: ${{ secrets.KUBECONFIG }}
        run: |
          set -euo pipefail
          echo "$KUBECONFIG_DATA" > "$RUNNER_TEMP/kubeconfig"
          export KUBECONFIG="$RUNNER_TEMP/kubeconfig"
          helm upgrade --install "$SERVICE" deploy/helm/service \
            --namespace bg-apps \
            --set image.repository="${{ vars.DOCKERHUB_NS }}/$SERVICE" \
            --set image.digest="${{ steps.image.outputs.digest }}" \
            --set-json canary.weights='${{ steps.decision.outputs.weights }}' \
            --set canary.bake="${{ steps.decision.outputs.bake }}" \
            --wait --timeout 30m
```

No `curl` decides anything here: by the time this job starts, GitHub has already received BudgetGuard's verdict. A rejected deploy never starts, and the comment on the run carries the price, culprit and unfreeze ETA.

**10.3 Policy backtest — `platform/backtest/replay.py` (the D-6 harness)**

```python
"""Replays historical and synthetic deploys through a policy and scores it.

A blocked deploy is a true positive if that deploy (in history) or its synthetic twin
was a change failure; a false positive otherwise. Rollbacks must be admitted 100%.
"""
from __future__ import annotations

from dataclasses import dataclass

from budgetguard.gate import GateInput, Lane, Verdict, evaluate


@dataclass(frozen=True, slots=True)
class Case:
    gate_input: GateInput
    was_change_failure: bool
    budget_cost_pct: float          # budget the deploy actually burned (0 if healthy)


@dataclass(frozen=True, slots=True)
class Score:
    precision: float
    recall: float
    prevented_burn_pct: float
    blocked_healthy: int
    rollback_admit_rate: float


def score(cases: list[Case]) -> Score:
    tp = fp = fn = 0
    prevented = 0.0
    rollbacks = admitted_rollbacks = 0
    for c in cases:
        d = evaluate(c.gate_input)
        blocked = d.verdict is Verdict.BLOCK
        if c.gate_input.lane is Lane.ROLLBACK:
            rollbacks += 1
            admitted_rollbacks += not blocked
        if blocked and c.was_change_failure:
            tp += 1
            prevented += c.budget_cost_pct
        elif blocked:
            fp += 1
        elif c.was_change_failure:
            fn += 1
    return Score(
        precision=tp / (tp + fp) if tp + fp else 1.0,
        recall=tp / (tp + fn) if tp + fn else 1.0,
        prevented_burn_pct=prevented,
        blocked_healthy=fp,
        rollback_admit_rate=admitted_rollbacks / rollbacks if rollbacks else 1.0,
    )


def gate_ci(candidate: Score, baseline: Score, tolerance_pp: float = 2.0) -> list[str]:
    """Returns failure reasons; an empty list means the policy PR may merge."""
    failures = []
    if candidate.rollback_admit_rate < 1.0:
        failures.append("a rollback was blocked")
    if 100 * (baseline.precision - candidate.precision) > tolerance_pp:
        failures.append(f"precision fell {100*(baseline.precision-candidate.precision):.1f}pp")
    if 100 * (baseline.recall - candidate.recall) > tolerance_pp:
        failures.append(f"recall fell {100*(baseline.recall-candidate.recall):.1f}pp")
    if candidate.blocked_healthy > baseline.blocked_healthy * 1.2 + 1:
        failures.append("healthy deploys blocked rose more than 20%")
    return failures
```

**10.4 The synthetic incident library** — seeded, labelled scenarios the backtester and the demo share:

| Scenario | Shape | What it proves |
| --- | --- | --- |
| Fast burn | 20x for 15 min after a deploy | Page fires; next deploy blocked; rollback admitted |
| Slow burn | 1.5x for 4 days | Ticket, not page; pricing tightens as budget falls |
| Dependency outage | auth returns 5xx; orders fails through | Orders not frozen; auth charged (D-3) |
| Dead service | Pod crash loop, zero samples | UNKNOWN, never HEALTHY (D-4) |
| Scrape loss | Prometheus target removed | `SLIIntegrityLost` page; fail-open audit row |
| Flapping | 30 s bursts every 10 min | Two-window alerts stay quiet |
| Low traffic | 50 requests/hour at night | Floor suppresses a 1-error page |

**10.5 The brief grounding eval — `platform/evals/grounding.py`**

```python
import re

NUM = re.compile(r"-?\d+(?:\.\d+)?")

def numbers_grounded(brief: str, record: dict, tol: float = 0.051) -> bool:
    """Every number in the brief must match a number in the decision record (±0.05 for rounding)."""
    allowed = [float(x) for x in NUM.findall(str(record))]
    return all(any(abs(float(n) - a) <= tol for a in allowed) for n in NUM.findall(brief))

REQUIRED = ("verdict", "price_pct", "affordable_pct", "eta")

def facts_present(brief: str, record: dict) -> float:
    hits = sum(1 for k in REQUIRED if record.get(k) is None or str(record[k])[:4] in brief)
    return hits / len(REQUIRED)
```

Runs over 120 golden decision records (40 admit, 40 block, 20 lanes, 20 unknown) per model and prompt version; results land in an `eval_runs` table keyed by `(model, prompt_sha)`, and the CI job fails below the §E thresholds. The prompt file's SHA is part of the key, so a prompt edit is versioned exactly like a model change.

## 11. Monitoring, the scaling decision, README and public deployment

The Grafana dashboard answers the four questions in your brief plus two that prove the gate works; the documented scaling decision is "answer the gate's 30-day question from 5-minute buckets, not from raw Prometheus ranges"; and the live link runs on a single ARM VM with k3s.

**11.1 Grafana — "BudgetGuard / Service reliability"**

| Panel | Query source | Expression (abridged) |
| --- | --- | --- |
| Error-budget burn rate per service (5m, 1h, 6h) | Prometheus | `bg:error_ratio:rate1h / on(service,slo) group_left bg:slo_allowed_ratio` |
| 30-day rolling availability vs SLO | TimescaleDB `sli_hourly` | `1 - sum(bad)/sum(total)` over the last 30 days, per service |
| MTTR per incident type (p50, p90) | TimescaleDB | Query 2 in §5 |
| Deployment change rate and change-failure rate | TimescaleDB | Query 3 in §5 |
| Gate verdicts by reason (admit, block, unverified) | TimescaleDB `decisions` | Count by `reason_code` per day |
| Price vs affordable for recent deploys | TimescaleDB `decisions` | Scatter of `price_pct` against `affordable_pct` |
| Self vs dependency burn (chargeback) | TimescaleDB | Query 4 in §5 |
| SLI integrity minutes (UNKNOWN time) | Prometheus | `sum_over_time((up == 0)[30d:1m])` per service |
| BudgetGuard meta-SLO | Prometheus | Synthetic webhook success and latency |

Deploy annotations come from the `deployments` table and incident annotations from `incidents`, so every burn spike sits next to the deploy and incident it belongs to.

**11.2 The documented scaling decision**

> **Decision: the gate reads the 30-day budget from request-weighted 5-minute buckets in TimescaleDB, not from `increase(...[30d])` on raw Prometheus counters.**
>
> **Problem.** At a 15-second scrape, each counter series holds 172,800 samples per 30 days. With per-URI and per-status series, one service's 30-day query touches millions of samples, and the gate asks it on every deploy evaluation plus every dashboard refresh.
>
> **Options.** (A) Query raw counters on demand. (B) A Prometheus recording rule for `increase30d` every 5 minutes. (C) The snapshotter writes 8,640 buckets per SLO per 30 days; the gate sums them.
>
> **Chosen.** C for the gate, the unfreeze ETA and the backtester; B kept as the fallback when TimescaleDB is down. Short windows (5m, 1h, 6h) used for hard stops stay live from Prometheus.
>
> **Trade-off.** The 30-day number can lag by up to 6 minutes — irrelevant at a 30-day horizon. Hard stops are never stale.
>
> **Evidence to capture.** Record p95 verdict latency for A and C under the k6 merge-storm scenario and put both numbers in the README. Do not quote a speed-up you have not measured.
>
> **When to revisit.** At about 100 services, snapshot from a federated Prometheus or Mimir and partition buckets by service.

**11.3 README blueprint**

1. One-paragraph pitch and the 3-minute demo GIF (freeze → priced block → rollback admitted → unfreeze ETA).
2. Live links: dashboard, Grafana (read-only), a sample decision record.
3. Architecture diagram (export of §3) and the four planes in one table.
4. "Why not Sloth or Pyrra?" — the §D comparison, two sentences per tool.
5. SLO table with the arithmetic (§2) and the burn-rate ladder (§B).
6. Quick start: `make up` (compose), `make demo-fast-burn`, `make demo-dependency-outage`.
7. Kubernetes install: k3s, Helm values, GitHub App registration steps.
8. Decisions log: TimescaleDB, Valkey, protection rule vs curl, fail-open vs fail-closed, the scaling decision with measured numbers.
9. Screenshots: service cards, a rejected deploy with its comment, the Freeze Brief in Slack, the backtest PR comment, the canary abort.
10. Testing: rule tests, backtest report, grounding eval results.
11. Known limits: single PostgreSQL, approximate canary weights without a traffic router, priors in pricing.

**11.4 Public deployment**

| Option | Fit | Notes |
| --- | --- | --- |
| Oracle Cloud Always Free Ampere A1 VM + k3s | Recommended | The whole stack needs about 7–8 GB RAM (4 JVMs ≈ 2 GB, Prometheus ≈ 1 GB, TimescaleDB ≈ 1 GB, platform ≈ 1 GB, k3s and the rest ≈ 2 GB). Check the current free-tier limits and idle-reclamation policy before relying on it |
| AWS EC2 | Works, costs money above \~8 GB | A 4 GB instance cannot hold the stack; check current free-tier terms |
| Railway | Not suitable | Runs containers, not Kubernetes; Argo Rollouts and kube-prometheus-stack need a cluster |
| GKE or EKS | Over-budget for a portfolio | Name it as the production path |

Expose only the dashboard, read-only Grafana and `/v1/github/webhook` through a Cloudflare Tunnel, so the VM opens no inbound ports. Run a demo mode — continuous k6 traffic with a scheduled fault window every few hours — so a recruiter who opens the link at any time sees burn, verdicts and annotations, not a flat line. Label demo numbers as demo numbers on the page itself.

## 12. Interview prep and portfolio positioning

Every number you say must be derivable on a whiteboard or produced by the demo harness; that discipline is what separates this project from the Rev 1 claims a panel would have caught.

**60-second pitch**

> "BudgetGuard is an error-budget admission controller. Four Spring Boot services publish request-based SLIs; Prometheus computes multi-window burn rates; a GitHub App sits on each production environment as a deployment protection rule, so the gate can't be deleted from a workflow file. Instead of freezing at a flat 10%, it prices each deploy — its probability of failing, from that service's own history, times what a failure usually costs — and admits it only if the remaining budget can afford it. Rollbacks are always free, fixes for the active incident always pass, and when a service is frozen the engineer gets an exact time the budget comes back. A dead service doesn't count as healthy, and when auth breaks orders, auth pays. Policy changes are backtested in CI before they merge."

**3-minute demo script**

1. Dashboard: four healthy cards; open one decision record and show that recomputing it gives the same verdict.
2. `make demo-fast-burn` on orders-service: page fires within minutes; card turns red.
3. Merge a 900-line feature with a migration: the run shows "Waiting for BudgetGuard", then rejected — the comment shows price vs affordable and the unfreeze ETA.
4. Merge a 40-line fix linked to the incident: admitted on the fix lane with a forced 5% canary.
5. Trigger a rollback: admitted instantly at price 0.
6. `make demo-dependency-outage`: auth fails, orders fails through; orders is not frozen, auth is.
7. Open the policy PR that lowers the reserve to 3%: the backtest comment shows the precision and recall change, and CI blocks it.

**Questions and answers**

| Question | Answer |
| --- | --- |
| SLI, SLO, error budget? | SLI is a ratio of good to total events; SLO is the target over a window; the budget is 1 − SLO of events. At 99.9% over 30 days, 1 request in 1,000 may fail — 43.2 minutes if it were one full outage at constant traffic |
| Why 14.4, 6 and 1? | They spend 2%, 5% and 10% of a 30-day budget in 1 h, 6 h and 3 d. 14.4 × 1 / 720 = 2%. At 14.4x the whole budget lasts 50 hours |
| Why two windows per alert? | The long window proves it is significant; the short window proves it is still happening, so the alert also resets quickly after recovery |
| What is wrong with a naive burn-rate rule? | A dead service emits nothing, the ratio is NaN, every comparison is false, and the gate says healthy. BudgetGuard reports UNKNOWN and pages on lost integrity |
| What if BudgetGuard is down? | Two cases. Prometheus down: BudgetGuard answers "unknown" and fail-open admits with an audit row and a page. BudgetGuard down: GitHub holds the deploy; admins have an audited bypass that BudgetGuard ingests as an override |
| Why not a curl step? | Anyone with write access can delete it. The protection rule lives in repository settings, and the cluster credentials are environment secrets, so skipping the environment also loses access |
| Isn't the deploy price arbitrary? | The prior and multipliers are assumptions, stated as such. They are backtested on history and synthetic incidents; a policy change that blocks more healthy deploys or lowers recall fails CI. Rollbacks are always free, so the worst case of a bad price is a delayed feature |
| Why charge the culprit? | When auth fails, orders' error rate rises too. Freezing orders punishes the victim and slows its fixes. The `error_origin` label separates self-inflicted from dependency-inflicted burn |
| Why TimescaleDB for under a million rows? | At four services plain PostgreSQL is enough, and I would start there in a startup. I chose TimescaleDB for continuous aggregates that stay correct under late upserts, declarative compression and retention, and the path to 500 services at about 210 million rows a year |
| Why Valkey and not Kafka? | About 50 deploy events a day. Valkey already carries the verdict cache and locks; streams with consumer groups give at-least-once delivery. PostgreSQL SKIP LOCKED would also work at this volume |
| Low-traffic services? | A minimum-events floor turns short windows into LOW\_TRAFFIC instead of paging on one error; blackbox probes supply synthetic traffic; the 30-day window still counts every real request |
| Scale to 500 services? | Federated Prometheus or Mimir for SLIs; snapshotter sharded by service with Kubernetes Leases; buckets partitioned by service; per-team policy files; alert grouping by team in Alertmanager |
| Why an LLM, and why not a flagship model? | It only writes the explanation from a structured record; it never decides. A small model passes the grounding eval at a fraction of a cent per brief, and a template replaces it whenever it fails |
| How do you know the gate helps? | Backtest precision and recall on labelled history and synthetic incidents, plus the share of blocked deploys later fixed before release. I don't claim saved downtime — a counterfactual can't be measured |
| Who can override a freeze? | A signed-in approver with the platform-lead role, with a written reason of at least 20 characters; every override is a row reviewed in the retro |

**Soundbites**

- "A dead service has a perfect error rate — unless your gate knows the difference between zero errors and zero data."
- "The model writes the explanation, never the verdict."
- "Rollback is always free."
- "Freeze the culprit, not the victim."
- "My freeze policy has unit tests and a regression gate, like any other code."

**How BudgetGuard fits your SRE portfolio**

| Project | Question it answers | Link to BudgetGuard |
| --- | --- | --- |
| ChaosProof | Does this change survive failure before merge? | Its experiments become entries in BudgetGuard's synthetic incident library |
| BudgetGuard | Can this service afford this change right now? | — |
| HealOps | Can the platform fix a known failure itself? | A HealOps rollback arrives at BudgetGuard on the free rollback lane |
| IncidentPilot | How is an incident run and written up? | BudgetGuard's incident rows and decision records feed the PIR timeline |
| TraceMap | Where does latency and error come from across hops? | Cross-checks D-3's `error_origin` attribution against traces |

Target panels: Google SRE Bangalore, Flipkart, Razorpay, PhonePe, CRED and Atlassian SRE — each runs error-budget governance in some form, which makes the pricing and attribution questions above the ones most likely to come up.
