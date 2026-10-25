# BudgetGuard skills (aligned with the Rev 2 implementation plan)

Eight Claude skills, one per part of the system, plus starter code, the schema and eval-harness
references, per-skill test cases and an evaluation report.

## Skills

| Skill | Covers | Reference file |
| --- | --- | --- |
| `budgetguard-slo-math` | Error budget, burn rate, remaining (negative allowed), 2/5/10% ladder, tri-state integrity, exact unfreeze ETA | — |
| `budgetguard-slo-rules` | ServiceLevel spec, rule generator (`bg generate`), multi-window alerts with traffic floor, SLIIntegrityLost, promtool tests, Sloth parity, Alertmanager | — |
| `budgetguard-spring-services` | 4 Spring Boot 4.1 / Java 21 services, SLO buckets, `error_origin` (D-3), Resilience4j client, fault endpoint, async SLI, k6 | — |
| `budgetguard-admission-gate` | GitHub App protection rule, gate chain with rollback and override lanes, pricing, lane proofs, decision records, OIDC, overrides by re-run | `github-app.md` |
| `budgetguard-data-layer` | PostgreSQL 18 + TimescaleDB schema, snapshotter, continuous aggregates, Alembic, DORA/MTTR SQL, Valkey keys and streams | `schema-and-queries.md` |
| `budgetguard-mlops-evals` | Policy backtester, synthetic incident library, Freeze Brief writer (provider-agnostic, template fallback), grounding eval, CI gates | `eval-harness.md` |
| `budgetguard-delivery-platform` | Compose, multi-arch signed images, k3s, Helm + Argo Rollouts canary with Traefik weights, workflows, tunnel, chaos drill | `manifests-and-workflows.md` |
| `budgetguard-nextjs-dashboard` | Next.js 16 BFF dashboard: service cards, decision explorer with recompute, override flow, policy reports | `dashboard-code.md` |

## Files

- `dist/<skill>.skill` — installable zip (`<skill>/SKILL.md` + `references/`); `dist/<skill>.SKILL.md` — readable copy
- `dist/BudgetGuard_starter_code.md` — repo layout, platform skeleton (tested), kickoff checklist, build order
- `dist/BudgetGuard_schema-and-queries.md`, `dist/BudgetGuard_eval-harness.md` — standalone copies of the two references
- `BudgetGuard_skill_eval_report.md` — routing accuracy and with/without-skill results
- `budgetguard-skill-evals.zip` — every skill's `evals/` case folders (skill-creator format), the per-skill trigger sets and the harness scripts

## Concerns raised against Rev 2 (fixed in the skills, listed so you can update the plan)

1. D-3 is promised but not wired into the gate: the victim of a dependency outage is frozen (`attribution: charge_origin` fix).
2. An override cannot use a callback GitHub already answered; overrides become a single-use record plus a job re-run on an `OVERRIDE` lane.
3. Chain order prose (§3) disagrees with the code (§4.11); the code is right.
4. `LOW_TRAFFIC` treated as UNKNOWN pages on-call for night deploys (policy flag added).
5. Lanes were claimed, not proven (subtree SHA, open-incident reference, override row).
6. Backtest cases stored a priced `GateInput`, so pricing-weight changes replayed identically.
7. Grounding eval was both too lenient (SHA digits) and too strict (dates parsed as negative numbers); it now also runs at runtime.
8. The 5% canary is ~25% with 4 replicas, and internal callers bypass the ingress router.
9. `helm --wait` does not wait for canary analysis; the job now waits on `kubectl argo rollouts status`.
10. `/v1/decisions` must be public for GitHub-hosted runners; images were signed but never verified.
11. `incidents` unique on fingerprint merges recurring alerts and corrupts MTTR.
12. Versions moved: FastAPI 0.128 → pin 0.143.0; Next.js 16.4 shipped (stay on 16.3.8 for now); Better Auth chosen for sign-in.
