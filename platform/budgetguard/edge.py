"""Public-path guard (security-and-api.md §2). The tunnel's path regex is the first filter; this
repeats it so a tunnel misconfiguration cannot expose internal routes. Cloudflare always sets
CF-Connecting-IP on requests that came through the edge, and nothing inside the cluster does."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

PUBLIC = {("POST", "/v1/github/webhook"), ("GET", "/v1/decisions")}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def public_path_guard(request: Request,
                                call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if "cf-connecting-ip" in request.headers and (request.method, request.url.path) not in PUBLIC:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return await call_next(request)
