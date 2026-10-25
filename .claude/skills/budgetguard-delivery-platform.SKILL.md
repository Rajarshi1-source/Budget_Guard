---
name: budgetguard-delivery-platform
description: Ship and run BudgetGuard — docker-compose with every image tag in one .env.versions file, multi-arch images with SBOM and cosign keyless signing, k3s on an Oracle Cloud ARM VM, Helm charts for the four services as Argo Rollouts canaries with budget-proportional steps from the gate's canary_profile, Traefik weighted routing, the budget-burn AnalysisTemplate, PodMonitors, kube-prometheus-stack with 35-day retention, the platform chart with PDBs and NetworkPolicy, GitHub environments per service with the BudgetGuard protection rule and environment-scoped KUBECONFIG, the ci, build-push and deploy workflows, Cloudflare Tunnel, demo Makefile targets and a chaos drill of BudgetGuard itself. Use for ANY Dockerfile, compose, Helm, k3s, Argo Rollouts, canary, GitHub Actions, environment, signing, public deployment or sizing work, even if the user only says deploy, canary or pipeline. MANDATE - deploy by digest, verify the signature, and gate the job on the rollout result, not helm wait.
---

# BudgetGuard — delivery platform (Rev 2 §9, §10.1–10.2, §11.4)

BudgetGuard's enforcement is only as strong as the pipeline around it. Three properties make
the gate unbypassable, and every change here must keep them:

| Property | Mechanism | What breaks it |
| --- | --- | --- |
| The deploy job cannot run without the gate | `environment: production-<service>` with BudgetGuard's custom protection rule | Removing the environment line, but then… |
| …a job without the environment has no cluster credentials | `KUBECONFIG` is an **environment** secret, not a repo secret | Moving it to a repo secret |
| Admins cannot quietly skip it | `can_admins_bypass: false`, plus the bypass reconciler in the gate | Turning bypass on without the reconciler |

Pairs with: `budgetguard-admission-gate` (protection rule, `/v1/decisions`, OIDC),
`budgetguard-spring-services` (Dockerfile, fault endpoints the Makefile calls),
`budgetguard-slo-rules` (generated rules, Alertmanager tree), `budgetguard-data-layer`
(Alembic, backups), `budgetguard-mlops-evals` (backtest and brief-eval workflows).

**Full manifests and workflows: `references/manifests-and-workflows.md`** (compose, Makefile,
both Helm charts, cluster bootstrap, RBAC, tunnel, GitHub setup script, `ci.yml`,
`build-push.yml`, `deploy.yml`, chaos drill). Read it before editing any of those files.

---

## 1. Environments and strategy per component

| Component | Where | Strategy | Why |
| --- | --- | --- | --- |
| 4 services | `bg-apps`, 4 replicas | Argo Rollouts canary; steps from `canary_profile`; background burn analysis | The system under test; D-7 |
| `platform-api` | `bg-platform`, 2 + PDB | Rolling, `maxUnavailable: 0`, `maxSurge: 1` | The gate must answer during its own deploy |
| `gate-worker` | 2 | Rolling | Consumer group; `XAUTOCLAIM` covers a killed pod |
| `snapshotter` | 2 | Rolling | PostgreSQL advisory lock picks one writer (no Lease, no RBAC) |
| `brief-writer` | 1 | Rolling | Off the critical path |
| Schema | Alembic job | Expand → migrate → contract, one release apart | No release depends on a column the previous one lacks |
| Rules and policy | GitOps | Generated in CI, applied as `PrometheusRule`; policy gated by backtest | Rules are code with tests |
| Dashboard | 2 | Rolling | Stateless |
| Blue-green | — | Rejected | Doubles memory on a free VM; one all-or-nothing switch instead of graded exposure |

Canary profile → steps (the API returns `bake`; Rev 2's job read a field the gate never set):

| `canary_profile` | When | Bake | Rendered steps |
| --- | --- | --- | --- |
| `[25, 100]` | ≥ 50% budget left | 2m | 25 → pause 2m → 100 |
| `[10, 50, 100]` | 20–50% | 5m | 10 → 5m → 50 → 5m → 100 |
| `[5, 25, 50, 100]` | < 20%, LOW_TRAFFIC, fix lane, unknown, or decision unreadable | 10m | 5 → 10m → 25 → 10m → 50 → 10m → 100 |
| `[100]` | proven rollback | 0s | 100 |

---

## 2. Flags against Rev 2 (fixed in the reference files)

1. **The 5% canary is fiction for three of four services.** Rev 2 notes weights are
   approximated by replica counts without a traffic router (4 replicas → 5% is really 25%).
   Worse: auth, inventory and notifications get their traffic from orders *inside* the
   cluster, so an ingress-level router never sees it. Fix: Argo Rollouts' native Traefik
   integration (`traefik.io` group, Traefik v3, which k3s bundles) **and** route
   service-to-service calls through an internal Traefik entrypoint
   (`http://auth.bg-apps.internal`). One extra in-cluster hop buys real weights. If you skip
   it, say "replica-weighted canary" in the README, never "5%".
2. **`helm upgrade --wait` does not wait for canary analysis.** Helm 4 waits with kstatus,
   which knows nothing about Rollout steps or AnalysisRuns. The job can go green while the
   canary is still baking or later aborts, and GitHub then records a successful deployment
   that BudgetGuard counts for DORA. Fix: `kubectl argo rollouts status <svc> --timeout 45m`,
   which exits non-zero on abort.
3. **`bake` is read but never produced.** The gate's `Decision` has `canary_profile` only;
   `GET /v1/decisions` derives `bake` from the profile (table above).
4. **`/v1/decisions` must be public.** GitHub-hosted runners call it, but Rev 2 §9.4 says only
   the webhook and dashboard are reachable. Expose it through the tunnel; every call carries
   a GitHub OIDC token checked for repository, environment and audience.
5. **Images are signed but never verified.** `deploy.yml` now runs `cosign verify` against the
   `build-push.yml@refs/heads/main` identity before Helm sees the digest.
6. **Decision values flow into a shell unescaped.** `${{ steps.decision.outputs.weights }}`
   inside `run:` is template-injected. Pass through `env:` and validate with `jq -e` first.
7. **Two schema owners.** Rev 2's compose mounts `platform/sql` into
   `docker-entrypoint-initdb.d` *and* uses Alembic. Init scripts run once on an empty volume,
   so local and cluster schemas drift. Alembic owns the schema; compose runs a `migrate` job.
8. **Alertmanager route order.** Pages must `continue` into the `budgetguard` receiver or the
   incident table never hears about them (same tree as `budgetguard-slo-rules` §5).
9. **The deployer kubeconfig is long-lived.** Scope it with a namespaced Role (reference §5).
   Hardening path, named not built: Kubernetes structured authentication trusting GitHub's
   OIDC issuer, mapping the `environment` claim to RBAC, so no cluster secret is stored at all.

---

## 3. AnalysisTemplate essentials

- Two metrics: **canary has traffic** (≥ 30 req/min; no traffic is not a pass, D-4) and
  **canary vs stable self-inflicted burn** ≤ 2x for 10 checks, `failureLimit: 2`.
- `initialDelay: 2m` so the first `rate(...[2m])` window is full.
- `or vector(0)` on both error numerators: a healthy pod has no 5xx series at all, and an
  empty result errors the measurement instead of passing it.
- `clamp_min(stable_ratio, 0.0005)` so a perfect stable does not divide by zero.
- Only `error_origin="self"` counts (D-3): a canary is not aborted for auth's outage.
- PodMonitor `podTargetLabels: [rollouts-pod-template-hash]` is what makes canary and stable
  separable in PromQL; the Rollout passes the hashes with `podTemplateHashValue: Latest/Stable`.

---

## 4. Public deployment and sizing

| Option | Fit | Notes |
| --- | --- | --- |
| Oracle Cloud Always Free Ampere A1 + k3s | Recommended | ARM64: images must be multi-arch. Check current free-tier limits and idle reclamation |
| AWS EC2 | Works, paid above ~8 GB | A 4 GB instance cannot hold the stack |
| Railway | Not suitable | No Kubernetes, so no Argo Rollouts or kube-prometheus-stack |
| GKE / EKS | Production path | Over a portfolio budget |

Memory budget (Rev 2 §11.4): 4 JVMs ≈ 2 GB, Prometheus ≈ 1 GB, TimescaleDB ≈ 1 GB, platform
≈ 1 GB, k3s and the rest ≈ 2 GB → 7–8 GB. The JVMs use `-XX:MaxRAMPercentage=75` with a
512 Mi limit; measure RSS under the k6 merge storm before trusting the estimate.

Expose only the dashboard, read-only Grafana, `/v1/github/webhook` and `/v1/decisions`
through Cloudflare Tunnel; the VM opens no inbound ports. Run demo mode (continuous k6 with a
scheduled fault window) and label demo numbers as demo numbers on the page.

---

## 5. Procedures

**Add a service**: SLO spec → `bg generate` → Helm release values → paths-filter entry in
`deploy.yml` → environment + protection rule + `KUBECONFIG` via the setup script → internal
host in Traefik → dashboard card appears from the API.

**Rotate the deployer credential**: new token → `gh secret set KUBECONFIG --env …` for each
environment → delete the old token secret → run one deploy.

**Bump a version** (Prometheus, Grafana, Argo, Helm, k3s): change `.env.versions` or the
chart pin in one PR; CI runs promtool from the *same* image tag as the server, so rule-test
drift between local and server cannot happen.

**Investigate a stuck deploy**: Actions run shows "Waiting" → `GET /v1/decisions?run_id=…`
(was the webhook received?) → gate-worker logs for the delivery id → `XPENDING
bg:gate-evaluations gate-workers` → the callback state on the decision row.

---

## 6. Interview soundbites

- "Deleting the environment line doesn't bypass the gate, because the cluster credentials
  only exist in that environment."
- "Helm says 'deployed' when the objects are applied. A canary is done when the analysis
  says so, so the job waits on `kubectl argo rollouts status`, which fails on abort."
- "Without a traffic router, a 5% step on four replicas is a 25% step. And internal callers
  never pass the ingress, so I route service-to-service calls through Traefik too."
- "Every image is deployed by digest and its keyless signature is verified against the
  build workflow's identity before Helm sees it."
- "The canary only aborts on self-inflicted errors; a dependency outage doesn't roll back
  a healthy release."
- "I chaos-tested BudgetGuard itself: kill the API mid-deploy and GitHub just waits; kill
  Prometheus and the service's declared failure mode decides, with an audit row."
