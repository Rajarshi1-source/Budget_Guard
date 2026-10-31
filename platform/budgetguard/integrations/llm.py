"""LLM provider adapters for the Freeze Brief (W7.4, budgetguard-mlops-evals §3).

All return the same Completion; cost comes from the pricing table in config/llm.yaml. The SDKs are
in the optional `llm` extra and imported lazily, so the gate image never ships clients it does not
call. No model name appears in code: config/llm.yaml is the only place one is chosen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str


class BriefModel(Protocol):
    model: str

    async def complete(self, system: str, user: str, *, max_output_tokens: int) -> Completion: ...


class OpenAIModel:
    """OpenAI Responses API: `output_text`, `usage.input_tokens` / `usage.output_tokens`."""

    def __init__(self, model: str) -> None:
        from openai import AsyncOpenAI  # optional dependency
        self.client: Any = AsyncOpenAI()
        self.model = model

    async def complete(self, system: str, user: str, *, max_output_tokens: int) -> Completion:
        t = time.perf_counter()
        r = await self.client.responses.create(model=self.model, instructions=system, input=user,
                                               max_output_tokens=max_output_tokens)
        return Completion(r.output_text, r.usage.input_tokens, r.usage.output_tokens,
                          (time.perf_counter() - t) * 1000, self.model)


class AnthropicModel:
    """Anthropic Messages API."""

    def __init__(self, model: str) -> None:
        from anthropic import AsyncAnthropic  # optional dependency
        self.client: Any = AsyncAnthropic()
        self.model = model

    async def complete(self, system: str, user: str, *, max_output_tokens: int) -> Completion:
        t = time.perf_counter()
        r = await self.client.messages.create(model=self.model, system=system, max_tokens=max_output_tokens,
                                              messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in r.content if b.type == "text")
        return Completion(text, r.usage.input_tokens, r.usage.output_tokens,
                          (time.perf_counter() - t) * 1000, self.model)


class OllamaModel:
    """Local Ollama: /api/chat with stream false."""

    def __init__(self, model: str, base_url: str = "http://ollama:11434") -> None:
        import httpx
        self.client = httpx.AsyncClient(base_url=base_url)
        self.model = model

    async def complete(self, system: str, user: str, *, max_output_tokens: int) -> Completion:
        t = time.perf_counter()
        r = await self.client.post("/api/chat", json={
            "model": self.model, "stream": False, "options": {"num_predict": max_output_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        body = r.json()
        return Completion(body["message"]["content"], body.get("prompt_eval_count", 0),
                          body.get("eval_count", 0), (time.perf_counter() - t) * 1000, self.model)


PROVIDERS: dict[str, type[OpenAIModel] | type[AnthropicModel] | type[OllamaModel]] = {
    "openai": OpenAIModel, "anthropic": AnthropicModel, "ollama": OllamaModel}


def build_model(provider: str, model: str) -> BriefModel | None:
    """None for provider `template`: the deterministic path, no client at all."""
    if provider == "template":
        return None
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected template or one of {sorted(PROVIDERS)}")
    return PROVIDERS[provider](model)
