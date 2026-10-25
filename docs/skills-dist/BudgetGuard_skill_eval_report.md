# BudgetGuard skills — evaluation report (24 Oct 2026)

Model under test: `claude-opus-5-5` through `claude -p` (Claude Code 2.1.310), the same model
that wrote the skills. All eight skills were installed together in a scratch project's
`.claude/skills/`, so they competed with each other and with the other skills on the account
(PRGraph, NaukriNearby, skill-creator and the built-ins).

## 1. Triggering / routing accuracy

A run passes when the **first** skill Claude loads is the expected one, or, for a negative, no
BudgetGuard skill at all.

| Round | Queries | Passed |
| --- | --- | --- |
| 1 — realistic prompts naming the project (32 positive, 16 near-miss negative) | 48 | 48 |
| 2a — harder: no project name, cross-skill confusers (12 positive, 4 negative) | 16 | 16 |
| 2b — repeat of 2a for stability | 16 | 16 |
| **Total** | **80** | **80** |

Per skill, over all 80 runs:

| Skill | Positive runs | Correct | False triggers | Precision | Recall |
| --- | --- | --- | --- | --- | --- |
| `budgetguard-admission-gate` | 8 | 8 | 0 | 1.00 | 1.00 |
| `budgetguard-data-layer` | 8 | 8 | 0 | 1.00 | 1.00 |
| `budgetguard-delivery-platform` | 8 | 8 | 0 | 1.00 | 1.00 |
| `budgetguard-mlops-evals` | 8 | 8 | 0 | 1.00 | 1.00 |
| `budgetguard-nextjs-dashboard` | 6 | 6 | 0 | 1.00 | 1.00 |
| `budgetguard-slo-math` | 6 | 6 | 0 | 1.00 | 1.00 |
| `budgetguard-slo-rules` | 6 | 6 | 0 | 1.00 | 1.00 |
| `budgetguard-spring-services` | 6 | 6 | 0 | 1.00 | 1.00 |

Negatives never loaded a BudgetGuard skill (24 runs). Where another skill fit, it won:
`naukrinearby-nextjs`, `prgraph-nextjs-frontend`. Confusers that passed include: a canary AnalysisTemplate
question that mentions `error_origin` (delivery, not Spring), an overrides-table change
(data layer, not gate), and Better Auth's `getAccessToken` error (dashboard).

No description needed changing, so `run_loop` description optimisation was not run.
The per-skill sets in `trigger-evals/<skill>.json` use skill-creator's format
(`query`, `should_trigger`; positives plus every other skill's queries as near-misses) if
you want to run `python -m scripts.run_loop` later.

**Caveat**: 80 runs at one sample per query; no confidence interval is claimed beyond "no
errors observed". Prompts were written by the same model that wrote the descriptions.

## 2. Functional check (with skill vs without)

Sixteen cases, two per skill, are saved as skill-creator case folders under each skill's
`evals/` (prompt.md, case.yaml, graders/). Each one ran through `claude -p` with the eight
skills installed and again without them, using read-only tools (Read, Glob, Grep, Skill).
Replies were graded with one regex per grader sentence. All 32 runs completed. Three cases
ran before the account's session limit and the other thirteen after it reset at 22:40 IST.
Total cost was about $8.

| Skill | Case | With skills | Without |
| --- | --- | --- | --- |
| `budgetguard-slo-math` | unknown-vs-low-traffic | 4/4 | 4/4 * |
| `budgetguard-slo-math` | ladder-and-eta-arithmetic | 4/4 | 4/4 |
| `budgetguard-admission-gate` | override-after-rejection | 4/4 | 4/4 * |
| `budgetguard-admission-gate` | victim-not-frozen | 4/4 | 1/4 |
| `budgetguard-slo-rules` | page-alert-with-floor | 4/4 | 4/4 |
| `budgetguard-slo-rules` | alertmanager-inhibition | 3/3 | 2/3 |
| `budgetguard-spring-services` | dependency-error-origin | 4/4 | 4/4 * |
| `budgetguard-spring-services` | fault-endpoint-contract | 4/4 | 4/4 * |
| `budgetguard-data-layer` | incident-uniqueness | 3/3 | 1/3 |
| `budgetguard-data-layer` | cagg-migration | 2/2 | 2/2 |
| `budgetguard-mlops-evals` | weight-change-no-diff | 3/3 | 1/3 |
| `budgetguard-mlops-evals` | model-switch-decision | 4/4 | 0/4 |
| `budgetguard-delivery-platform` | replica-weights | 3/3 | 3/3 |
| `budgetguard-delivery-platform` | helm-wait-vs-rollout | 3/3 | 2/3 |
| `budgetguard-nextjs-dashboard` | override-authorization | 4/4 | 4/4 * |
| `budgetguard-nextjs-dashboard` | suspense-build-error | 3/3 | 3/3 |
| **All 16 cases** | | **56/56** | 43/56 |
| **11 uncontaminated cases** | | **36/36** | **23/36 (64%)** |

\* **Contaminated baseline.** In these five runs, the "without skills" run searched the disk,
found the skill files under `/home/claude/budgetguard-skills/` and quoted them. Those runs are
not a real baseline, so the honest comparison is on the other 11 cases: 36/36 with the skills
against 23/36 without. The gap is largest where the skills hold project decisions that
general knowledge doesn't cover: D-3 attribution, backtest case design, the model-promotion
rule and incident uniqueness. Generic SRE arithmetic and the Next.js Suspense fix pass either
way. A clean re-run needs the baseline in a container or directory that cannot reach the skill
files.

**Grader corrections, stated openly.** After reading the with-skill replies I loosened three
regexes that were stricter than their grader sentence, and re-graded both conditions with the
corrected versions. The generator inlines `14.4 * 0.001` rather than referencing
`bg:slo_allowed_ratio`, so the skill's answer was correct and the regex was wrong. One reply
gave thresholds as `≥ 0.99` rather than `99%`. One called the plugin as
`"$RUNNER_TEMP/kubectl-argo-rollouts" status`. Before the corrections the with-skill score was
53/56. The case folders now hold the corrected graders.

To re-run:

```bash
cd trigger-evals/harness
mkdir -p project2/.claude/skills baseline_project && cp -r ../../skills/* project2/.claude/skills/
python3 functional.py claude-opus-5-5       # isolate baseline_project from the skill files first
# or per skill with the official runner (Claude Code v2.1.269+), from a skill directory:
claude plugin eval . --allow-tools Read,Glob,Grep
```

## 3. Code verified while writing the skills

| Artifact | Check | Result |
| --- | --- | --- |
| Budget math, integrity, ETA (`budgetguard-slo-math`) | pytest | 12 passed |
| Generated rules + promtool tests (`budgetguard-slo-rules`) | promtool 3.13.2 | passed |
| Backtest, synthetic library, brief writer, grounding eval (`budgetguard-mlops-evals`) | pytest | 34 passed |
| Platform API skeleton (starter code) | pytest | 14 passed |
| Dashboard (`budgetguard-nextjs-dashboard`) | tsc, vitest, next build | clean, 8 passed, all routes Partial Prerender |
| Workflows, compose, k8s YAML (`budgetguard-delivery-platform`) | PyYAML parse, `make -n` | parsed; Helm templates not rendered (no Helm binary reachable) |
