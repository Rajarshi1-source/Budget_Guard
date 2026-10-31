# Platform image: API, workers and the migrate job run this image with different commands.
# Build context is the repo root, so the SLO specs (the source of truth) are baked in:
#   docker build -f platform/Dockerfile --build-arg POLICY_VERSION=$(git log -1 --format=%H -- policy/) .
# Only the paths allowlisted in platform/Dockerfile.dockerignore reach the build.
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv

FROM python:3.14-slim-trixie AS build
# EXTRAS="--extra llm" builds a brief-writer image with the provider SDKs; the default image ships none
ARG EXTRAS=""
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY platform/pyproject.toml platform/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $EXTRAS   # dependency layer, cached across code edits
COPY platform/ ./
RUN uv sync --frozen --no-dev --no-editable $EXTRAS

FROM python:3.14-slim-trixie
ARG POLICY_VERSION=unknown
RUN useradd --uid 10001 --create-home app
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY platform/alembic.ini ./
COPY platform/migrations ./migrations
COPY platform/sql ./sql
COPY slo ./slo
COPY policy ./policy
COPY platform/prompts ./prompts
COPY platform/config ./config
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 BG_SLO_DIR=/app/slo BG_POLICY_VERSION=$POLICY_VERSION \
    BG_POLICY_FILE=/app/policy/freeze-policy.yaml BG_PROMPT_DIR=/app/prompts BG_LLM_CONFIG=/app/config/llm.yaml
USER 10001
EXPOSE 8000
CMD ["uvicorn", "budgetguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
