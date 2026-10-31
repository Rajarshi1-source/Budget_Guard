---
name: budgetguard-mlops-evals
description: Build and run BudgetGuard's MLOps layer — the policy backtester (D-6) that replays historical and synthetic deploys through a candidate freeze policy, scores precision, recall, harm recall, healthy deploys blocked and burn prevented, and fails a policy PR in CI; the seeded synthetic incident library as invariant cases; the Freeze Brief writer with a provider-agnostic LLM adapter (OpenAI, Anthropic, Ollama, template), llm.yaml, prompt SHA versioning, budget and circuit breakers and a template fallback; the grounding eval with its 120-case golden set, thresholds, eval_runs table and challenger-model promotion; and the policy-backtest and brief-eval workflows. Use for ANY work on backtest/, evals/, briefs.py, prompts, llm.yaml, model choice or eval thresholds, even if the user only says backtest, golden set, hallucination check or which model. MANDATE - the LLM explains, never decides; every number in a brief comes from the decision record.
---

# BudgetGuard — MLOps: policy backtest and Freeze Brief eval (Rev 2 §E, §10)

Two harnesses, one rule: **nothing that changes a verdict or an explanation merges without
a measured comparison to `main`.**

| Harness | Guards | Fails the PR when |
| --- | --- | --- |
| Policy backtest (D-6) | `policy/freeze-policy.yaml`, `gate.py`, `pricing.py` | a rollback is blocked; an invariant scenario breaks; precision, recall or harm recall falls > 2pp; healthy blocks rise > 20% |
| Freeze Brief eval (§E) | prompts, `llm.yaml`, `briefs.py` | grounding < 99%, fact recall < 95%, p95 latency > 4 s, or any forbidden content |

Pairs with: `budgetguard-admission-gate` (the backtest replays its exact `evaluate()`),
`budgetguard-slo-math` (ETA, budget numbers in the brief view), `budgetguard-data-layer`
(`decisions.inputs`, `deployments` labels, `eval_runs`), `budgetguard-delivery-platform`
(where the workflows run).

**Full, tested code is in `references/eval-harness.md`** (replay, synthetic library, history
export, CLI, brief writer, grounding, golden set, runner, tests, both workflows). Read it
before writing any file in `backtest/` or `evals/`. This file holds the design, the rules
and what to say about them.

---

## 1. Ownership: what the model touches

| Task | Owner | Why |
| --- | --- | --- |
| Verdict, price, attribution, ETA | Deterministic Python | Must recompute identically from the decision record (D-8) |
| Policy thresholds and weights | Backtester | Tuned against history, not opinion |
| Freeze Brief prose | LLM, template fallback | Explains numbers it is given |
| Policy-PR summary | Template (the markdown report) | A table is clearer than prose; no LLM needed |

The brief is written **after** the decision row is committed and GitHub has been answered;
the PR comment GitHub shows uses the template, so a slow model never delays a deploy.

---

## 2. Policy backtest (D-6)

### The case model — the most important design choice

```python
@dataclass(frozen=True, slots=True)
class DeployFacts:          # everything known when the gate decided, nothing after
    case_id: str; service: str; at: datetime; integrity: Integrity
    remaining_all_pct: float | None; remaining_self_pct: float | None
    page_firing: bool; self_share_1h: float | None; lane: Lane | None
    features: ChangeFeatures; prior_deploys: int; prior_failures: int
    median_failure_cost_pct: float | None; failure_mode: str

def to_gate_input(f: DeployFacts, p: Policy) -> GateInput:   # price recomputed per policy
```

Rev 2 stores a finished `GateInput` in each case. Its `price_pct` was computed with the old
weights, so a PR that changes `w_migration` replays **identically** and the backtest says
"no change". Store facts; let the candidate policy price them. A test pins this.

### Leak rules

| Leak | Rule |
| --- | --- |
| Future labels | A deploy's change-failure label exists 60 min after it **finished**; prior counts use `finished_at + 60 min <= t` |
| Partial buckets | A bucket counts only when `bucket_start + 5 min <= t` |
| Unobserved outcomes | A deploy blocked in reality never ran: label `None`, excluded, reported as unlabelled |
| Easiest of all | The gate worker writes `DeployFacts` into `decisions.inputs["facts"]` at decision time. History replay then cannot leak |

### Scoring

| Metric | Meaning | How to read it |
| --- | --- | --- |
| Precision | blocked deploys that would have failed | Low by nature: an overspent budget blocks everything; watch the delta |
| Recall | failures the policy would have blocked | Low by design: most failures happen while the budget can afford them |
| **Harm recall** | failures that took the budget below the reserve, blocked | The question the gate exists to answer |
| Healthy blocked | false positives | The cost of the policy, in deploys |
| Burn prevented | sum of budget cost of blocked failures | The benefit, in budget |
| Rollback admit rate | must be 1.0 | Any miss fails CI |

### Synthetic library as cases with expectations

| Scenario | Case | Expectation | Strength |
| --- | --- | --- | --- |
| Fast burn | feature during a self page | BLOCK | invariant |
| Fast burn | proven rollback; proven fix | ADMIT | invariant |
| Dependency outage | orders, 95% of errors from auth | not BLOCK (D-3) | invariant |
| Dependency outage | auth, self page | BLOCK | invariant |
| Dead service | NO_DATA, fail-open / fail-closed | ADMIT_UNVERIFIED / BLOCK | invariant |
| Dead service | rollback while fail-closed | ADMIT | invariant |
| Scrape loss | STALE | never plain ADMIT | invariant |
| Low traffic | LOW_TRAFFIC, 80% left | not BLOCK | invariant |
| Override | NO_DATA, fail-closed, page firing, override lane | ADMIT (break glass, recorded) | invariant |
| Slow burn | 900 lines + migration at 7% left; 40 lines at 7% | BLOCK; ADMIT (the §D worked example) | anchor |
| Flapping | nothing firing | ADMIT | anchor |

Invariant violations fail CI. Anchor moves are warnings ("update the docs if intended"),
because a stricter policy may legitimately block the 40-line change.

Plus a seeded **population**: 4 services × 150 deploys, budget cycling plenty → scarce →
overspent, labels drawn from a "true" risk model that deliberately differs from the policy's
weights so the backtest does not grade itself.

### `gate_ci` (excerpt)

```python
if candidate.rollback_admit_rate < 1.0:            failures.append("a rollback was blocked")
failures += [f"invariant violated: {v}" for v in candidate.violations]
for metric in ("precision", "recall", "harm_recall"):
    if 100 * (getattr(baseline, metric) - getattr(candidate, metric)) > tolerance_pp: ...
if candidate.blocked_healthy > baseline.blocked_healthy * 1.2 + 1: failures.append(...)
if candidate.change_failures < 20: warnings.append("weak evidence")
```

CI scores **main's code with main's policy** and **the PR's code with the PR's policy** in two
checkouts, so a `gate.py` change is measured too.

---

## 3. Freeze Brief writer

```text
decision row + ctx ──► brief_view() ──► BriefWriter ──► grounding.check() ──► Slack / PR
   (precomputes every number)          │ provider adapter      pass: LLM text
                                       │ timeout 4 s           fail: template
                                       │ budget breaker ($5/mo, Valkey ledger)
                                       └ circuit breaker (3 failures → 5 min open)
```

| Rule | Why |
| --- | --- |
| `brief_view()` precomputes every number (ETA in hours, rounded prices) | The model formats; it never computes, so grounding is checkable |
| Model name only in `config/llm.yaml` | Switching is a config PR measured by the eval |
| `prompt_sha` = sha256 of the prompt file | A prompt edit is versioned exactly like a model change |
| Grounding check runs **online** too | A brief that fails at 2 a.m. never reaches Slack; template serves |
| Template must score 100% on the golden set | The fallback is held to a stricter bar than the model |
| SDKs in an optional `llm` extra | The gate image does not ship clients it never calls |

Adapters: `OpenAIModel` (Responses API, `output_text`, `usage.input_tokens/output_tokens`),
`AnthropicModel` (Messages API), `OllamaModel` (`/api/chat`, `stream: false`). All return the
same `Completion`; cost comes from the `pricing` table in `llm.yaml`.

### Model decision (Rev 2 §E, prices from that plan, re-check at kickoff)

| Model | Role | Why |
| --- | --- | --- |
| `gpt-5.4-mini` | Default | Portfolio baseline; about $0.002 per brief |
| `gpt-6-luna` | Challenger, nightly, never served | ~7x cheaper input; promote only on two passing nights at the current `prompt_sha` |
| Flagship | Rejected | More cost and latency, no gain on a grounded summary of given facts |
| Template | Always available | Deterministic, free, 100% grounded |

**Why NOT** an LLM-as-judge for grounding: the judge can hallucinate too, costs money per
case and is non-deterministic in CI. Grounding here is a string-and-number property, so a
deterministic checker is both cheaper and more trustworthy. Use a judge only for tone, and
never as a merge gate.

---

## 4. Grounding eval

Checks, in order (the order fixes Rev 2's false failures):

1. **Identifiers first**, then removed from the text: URLs must equal `decision_url`; dates
   and `HH:MM` must match `eta_utc`; `INC-n` must be in the record; hex 7–40 chars must prefix
   the record SHA; `*-service` names must be in the record.
2. **Numbers** must match a numeric leaf of the view at the precision printed ("1.3" grounds
   1.26; "1.30" does not). Not `str(record)`: that lets SHA digits whitelist anything.
3. **Forbidden**: any override, bypass, force-deploy or admin-merge suggestion; any causal
   phrase ("caused by", "due to") naming a service other than `culprit`.
4. **Required facts**: verdict word, price, affordable, ETA (time or "in N hours"), lane,
   culprit — each only when present; a missing value is not a hit.

Golden set: 120 cases (40 admit, 40 block, 20 lanes, 20 unknown), seeded by `make_golden`,
with adversarial slices: culprit null, overspent budget, dependency culprit, no ETA,
all-digit SHA prefixes. Thresholds: grounding ≥ 0.99, fact recall ≥ 0.95, p95 ≤ 4000 ms,
forbidden = 0. The runner measures the **raw model**, no fallback.

State the statistics honestly: 119/120 has a 95% Wilson lower bound of 0.954. The report
prints the bound beside the rate.

---

## 5. Workflow when asked to change something here

1. Policy change → edit `policy/freeze-policy.yaml` → run `backtest.cli score` on main and the
   branch → `compare` → the workflow posts the table on the PR; on merge `publish` stores it
   for the dashboard. Never tune on the same seed you report (`--seed`).
2. Prompt change → run `run_brief_eval --provider template` (must stay 100%) and the default
   model → compare with the last `eval_runs` row for that model.
3. Model change → only via the promotion rule; edit `llm.yaml` in a PR whose description
   links the two nightly runs.
4. New scenario → add it to `scenario_cases()` with `must_be` / `must_not` and a strength.

---

## 6. Flags against Rev 2 (all fixed in the reference code)

1. Cases hold a stale `GateInput`, so weight changes replay identically.
2. Blocked-in-reality deploys treated as labelled.
3. Prior counts leak future labels.
4. Precision and recall alone make a good freeze look broken; harm recall added.
5. Scenarios were prose; now they fail CI.
6. **D-3 is not wired into the gate**: the victim of a dependency outage is frozen. The
   dependency-outage scenario fails under `attribution: charge_victim`; fix in
   `budgetguard-admission-gate` (third flag).
7. `numbers_grounded(str(record))` is both too permissive (SHA digits) and too strict (dates
   read as negative numbers).
8. `facts_present` counts `None` as present and matches four characters.
9. Grounding ran offline only.

---

## 7. As built in W7 (31 Oct 2026)

The reference code went in with these changes; read them before editing the files:

| Point | Rule |
| --- | --- |
| One set of types | `backtest/replay.py` imports `DeployFacts`, `to_gate_input`, `Policy` from `budgetguard.records` / `budgetguard.policy`; the backtest cannot drift from the gate |
| Grounding location | `budgetguard/grounding.py` (the writer runs it online); `evals/grounding.py` re-exports it |
| Paths | `load_prompt(prompt_dir)`; `BG_PROMPT_DIR` and `BG_LLM_CONFIG` settings; the image copies `platform/prompts` and `platform/config` |
| Adapters | `budgetguard/integrations/llm.py`; SDKs only with `--extra llm` (Dockerfile `EXTRAS`); a missing SDK or key logs the reason and serves the template |
| ETA | the gate worker stores `inputs.eta`; `brief_view` reads it, never recomputes |
| Still allowed | never override wording (FORBIDDEN would fail the template) |
| Delivery | `inputs.brief` + `brief_source` on the decision; Slack via `BG_SLACK_BRIEFS_URL`; PR comment with a `pull_requests: write` token; "budget recovered" note once per blocked decision |
| Encoding | every file read and write is UTF-8 (the report's arrow crashed `compare` on Windows) |
| Workflows | SHA-pinned; steps needing `BG_READONLY_DSN`, `BG_POLICY_DSN`, `BG_EVAL_DSN` or a provider key skip with a notice; main without a backtester is scored with the PR's code |
| Verified | template 120/120 (Wilson 0.969); reserve 3% fails compare (recall -6.3pp, harm recall -4.5pp) |

## 8. Interview soundbites

- "The model writes the explanation, never the verdict. If the provider is down, the freeze
  still works and the brief comes from a template that scores 100% on the same eval."
- "A policy change is a PR with a backtest table: harm recall, healthy deploys blocked,
  burn prevented. Blocking a rollback fails CI outright."
- "The first version of my backtester couldn't see pricing changes, because each case
  stored a precomputed price. Now cases store the facts the gate saw and each policy prices
  them, so a weight change shows up in the diff."
- "Low precision is what a freeze looks like: an overspent budget blocks healthy deploys too.
  I gate on deltas and report the cost in blocked deploys next to the budget it saved."
- "Grounding is a deterministic check, not an LLM judge: every number, time, ID and service
  in the brief must exist in the record at the precision printed. It runs in CI and again at
  runtime."
- "Switching models is a one-line config PR, and only after the challenger passes two
  nightly runs on the current prompt."
