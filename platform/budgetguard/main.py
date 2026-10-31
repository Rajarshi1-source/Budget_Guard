"""App factory. Workers run the same image with a different command (see compose)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from . import edge, redact
from .api import router as api_router
from .config import get_settings
from .integrations.github_app import router as github_router
from .integrations.prometheus import PrometheusClient
from .rulegen.spec import load_dir


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.valkey = Redis.from_url(settings.valkey_url, decode_responses=True)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
    app.state.prom = PrometheusClient(settings.prometheus_url, app.state.http, settings.prometheus_timeout_s)
    app.state.specs = load_dir(settings.slo_dir)
    from .pgstore import PgStore  # real store; tests inject a fake instead
    app.state.store = await PgStore.connect(settings.database_url)
    await app.state.store.ensure_policy_version(settings.policy_version)   # FK target for decisions
    from .integrations.callbacks import rerun_failed_jobs
    app.state.rerun = lambda d: rerun_failed_jobs(app.state.http, d)
    from .integrations.github_app import GitHubApi
    from .workers.gate_worker import Context, load_policy
    policy = load_policy(settings)
    app.state.attribution = policy.attribution
    app.state.gate = Context(                            # inline evaluation when Valkey is down (§7)
        settings=settings, policy=policy, specs={sl.metadata.service: sl for sl in app.state.specs},
        prom=app.state.prom, engine=app.state.store.engine, valkey=None, http=app.state.http,
        github=lambda i: GitHubApi(app.state.http, i))
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.valkey.aclose()
        await app.state.store.close()


def create_app(*, lifespan_override: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
               ) -> FastAPI:
    redact.install()                                     # before the first request is logged
    app = FastAPI(title="BudgetGuard", version="rev2", lifespan=lifespan_override or lifespan,
                  docs_url=None, redoc_url=None)        # no public docs on the internet-facing app
    edge.install(app)                                    # public-path guard first
    app.include_router(github_router)
    app.include_router(api_router)
    return app


app = create_app()
