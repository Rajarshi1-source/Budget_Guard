"""Runs the Freeze Brief eval for one (provider, model, prompt) and gates CI.

Measures the RAW model: no template fallback, no runtime guard. A model that needs the
fallback to look good has failed the eval.

  python -m evals.run_brief_eval --provider openai --model gpt-5.4-mini --out reports/brief.json
  python -m evals.run_brief_eval --provider template        # deterministic path, must score 100%
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from budgetguard.briefs import LlmConfig, build_model, load_prompt, template_brief, user_message
from evals.grounding import check

THRESHOLDS = {"grounding": 0.99, "fact_recall": 0.95, "p95_latency_ms": 4000.0, "forbidden": 0}


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = successes / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / (1 + z * z / n)


def p95(xs: list[float]) -> float:
    return statistics.quantiles(xs, n=20, method="inclusive")[18] if len(xs) >= 2 else (xs[0] if xs else 0.0)


async def run(provider: str, model_name: str | None, golden: Path, cfg_path: Path,
              concurrency: int = 8, prompt_dir: Path = Path("prompts")) -> dict[str, Any]:
    cases = [json.loads(line) for line in golden.read_text(encoding="utf-8").splitlines() if line.strip()]
    prompt = load_prompt(prompt_dir)
    cfg = LlmConfig.load(cfg_path)
    model = build_model(provider, model_name or cfg.model)
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict[str, Any]] = []

    async def one(case: dict[str, Any]) -> None:
        view = case["view"]
        async with sem:
            if model is None:
                text, ms, cost, err = template_brief(view), 0.0, 0.0, None
            else:
                try:
                    c = await asyncio.wait_for(model.complete(prompt.system, user_message(view),
                                                              max_output_tokens=cfg.max_output_tokens), 30)
                    text, ms, cost, err = c.text, c.latency_ms, cfg.cost_usd(c), None
                except Exception as exc:  # noqa: BLE001 - counted as a failed case, not skipped
                    text, ms, cost, err = "", 30_000.0, 0.0, type(exc).__name__
        r = check(text, view)
        rows.append({"id": case["id"], "kind": case["kind"], "passed": r.passed and err is None,
                     "grounded": r.grounded and err is None,
                     "fact_recall": r.fact_recall if err is None else 0.0,
                     "forbidden": r.forbidden, "ungrounded": r.ungrounded, "missing": r.missing_facts,
                     "latency_ms": ms, "cost_usd": cost, "error": err, "brief": text})

    await asyncio.gather(*(one(c) for c in cases))
    rows.sort(key=lambda r: r["id"])
    n = len(rows)
    grounded = sum(r["grounded"] for r in rows)
    summary = {
        "provider": provider, "model": None if model is None else model.model, "prompt_sha": prompt.sha,
        "cases": n,
        "grounding": grounded / n,
        "grounding_wilson_lower": round(wilson_lower(grounded, n), 4),
        "fact_recall": statistics.fmean(r["fact_recall"] for r in rows),
        "p95_latency_ms": p95([r["latency_ms"] for r in rows]),
        "forbidden": sum(len(r["forbidden"]) for r in rows),
        "errors": sum(r["error"] is not None for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "by_kind": {k: sum(r["passed"] for r in rows if r["kind"] == k) /
                    max(1, sum(r["kind"] == k for r in rows)) for k in ("admit", "block", "lane", "unknown")},
    }
    summary["failures"] = [
        name for name, ok in (
            ("grounding", summary["grounding"] >= THRESHOLDS["grounding"]),
            ("fact_recall", summary["fact_recall"] >= THRESHOLDS["fact_recall"]),
            ("p95_latency_ms", summary["p95_latency_ms"] <= THRESHOLDS["p95_latency_ms"]),
            ("forbidden", summary["forbidden"] <= THRESHOLDS["forbidden"]),
        ) if not ok]
    return {"summary": summary, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--model")
    ap.add_argument("--golden", type=Path, default=Path("evals/golden/briefs.jsonl"))
    ap.add_argument("--config", type=Path, default=Path("config/llm.yaml"))
    ap.add_argument("--out", type=Path, default=Path("reports/brief-eval.json"))
    a = ap.parse_args()
    result = asyncio.run(run(a.provider, a.model, a.golden, a.config))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    s = result["summary"]
    print(json.dumps({k: v for k, v in s.items() if k != "by_kind"}, indent=2))
    return 1 if s["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
