# BudgetGuard — project rules for Claude

## Sources, in priority order

1. `BudgetGuard Rev 2 — Implementation Plan.md` is the single source of truth for scope,
   architecture, versions, SLOs and schedule.
2. The eight `budgetguard-*` skills in `.claude/skills/` are the project-wide rules and hold
   verified code. Where a skill flags "Rev 2 is wrong here, fixed", the skill wins. Open the
   skill for the area before writing code in it.
3. `BudgetGuard_Rev2_Detailed_Implementation_Plan.md` is the build order: follow its week and
   work-package IDs (W0.1 …) and their exit checks.
4. `BudgetGuard_Implementation_Plan.md` (Rev 1) only where Rev 2 is silent, and only after
   checking it against Rev 2 §B.

## Mandates (never break these)

- The gate's verdict is deterministic and recorded before GitHub is answered. A rollback is
  always admitted. The LLM never decides.
- SLO rules are generated from one spec and never edited by hand. Every window an alert
  references has a recording rule and a promtool test. Silence is not health.
- Budget math is pure functions with no I/O. 14.4x spends 2% of a 30-day budget per hour and
  lasts 50 hours. Missing data is UNKNOWN, never zero errors.
- Services: Spring Boot 4.1.x, Java 21, Maven, never 3.x. Label cardinality stays bounded. The
  services emit truth and decide nothing.
- Prometheus is the live source of truth. Buckets are summed, never averaged. Valkey is never a
  source of truth.
- Every number in a Freeze Brief comes from the decision record.
- Deploy by digest, verify the signature, gate the job on the rollout result, not `helm --wait`.
- The browser never calls the platform API; the dashboard never makes a decision.

## Working rules

- Versions come from `deploy/compose/.env.versions` and `docs/decisions.md`; re-check before
  bumping, and never use `:latest`.
- Any change to an endpoint, credential or secret updates
  `.claude/skills/budgetguard-admission-gate/references/security-and-api.md` in the same PR.
- Record pins and decisions made while building in `docs/decisions.md`.

## Commands on this machine

- Java build and tests (Windows): `cd services && mvnw.cmd verify`.
- Docker, compose, kind, promtool, kubeconform: inside WSL 2 distro `Ubuntu-D`
  (`wsl -d Ubuntu-D`; the repo is at `/mnt/d/New Projects/Budget_Guard`). Do not run Maven over
  `/mnt/*` (Surefire forks stall); see plan §6 W0.6.
- `Ubuntu-D` stops when no WSL session is attached, and Docker stops with it.
- In Git Bash, prefix commands that pass `/…` paths to Windows tools with `MSYS_NO_PATHCONV=1`.
