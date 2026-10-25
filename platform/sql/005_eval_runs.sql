-- 005: brief and backtest eval results (budgetguard-mlops-evals §6)
CREATE TABLE eval_runs (
    id          BIGSERIAL PRIMARY KEY,
    suite       TEXT NOT NULL CHECK (suite IN ('brief', 'backtest')),
    provider    TEXT,
    model       TEXT,
    prompt_sha  TEXT,
    policy_version TEXT,
    code_sha    TEXT NOT NULL,
    cases       INT NOT NULL,
    metrics     JSONB NOT NULL,
    passed      BOOLEAN NOT NULL,
    trigger     TEXT NOT NULL CHECK (trigger IN ('pr', 'nightly', 'manual')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX eval_runs_key ON eval_runs (suite, model, prompt_sha, created_at DESC);
