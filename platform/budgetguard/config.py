"""All configuration from the environment. No defaults for secrets: a missing secret fails at boot."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    """Infrastructure only, no secrets beyond the URLs: migrations and the snapshotter boot on this
    without holding the GitHub App key or API tokens they never use."""
    model_config = SettingsConfigDict(env_prefix="BG_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bg:bg@timescaledb:5432/budgetguard"
    valkey_url: str = "redis://valkey:6379/0"
    prometheus_url: str = "http://prometheus:9090"
    prometheus_timeout_s: float = 3.0
    history_source: Literal["buckets", "raw"] = "buckets"   # "raw" only to measure option A (W8.7)
    slo_dir: Path = Path("slo")                      # the ServiceLevel specs (copied into the image)
    prompt_dir: Path = Path("prompts")               # Freeze Brief prompts, versioned by sha256 (W7)
    llm_config: Path = Path("config/llm.yaml")       # the only place a model name appears
    slack_briefs_url: SecretStr | None = None        # #bg-briefs incoming webhook; unset = not posted


class Settings(CoreSettings):
    github_repo: str = Field(description="owner/name the App guards")
    github_repo_id: int                              # OIDC repository_id: survives renames, not re-creation
    github_owner_id: int                             # OIDC repository_owner_id
    github_installation_id: int                      # the only installation the App acts for
    github_app_id: str
    github_app_private_key: SecretStr
    github_webhook_secret: SecretStr
    github_webhook_secret_previous: SecretStr | None = None   # set only during a rotation
    alertmanager_token: SecretStr
    alertmanager_token_next: SecretStr | None = None          # set only during a rotation
    dashboard_token: SecretStr
    dashboard_token_next: SecretStr | None = None             # set only during a rotation
    approver_ids: tuple[int, ...] = ()               # numeric GitHub user ids; logins can be renamed
    policy_version: str = "unknown"                  # baked into the image: git log -1 --format=%H -- policy/
    policy_file: Path = Path("../policy/freeze-policy.yaml")   # the image sets /app/policy/freeze-policy.yaml
    public_url: str = "https://budgetguard.example.dev"
    apps_namespace: str = "bg-apps"                  # Rollouts the cluster drift reconciler reads (W8.5)

    @field_validator("github_webhook_secret", "alertmanager_token", "dashboard_token")
    @classmethod
    def _required_not_blank(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("secret is blank")
        return v

    @field_validator("github_webhook_secret_previous", "alertmanager_token_next", "dashboard_token_next",
                     mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """compose writes `${X_NEXT:-}` as "": an empty rotation slot must not become a secret that an
        empty bearer token (or an HMAC with an empty key) would match (found in the W8.5 drill)."""
        return None if isinstance(v, str) and not v.strip() else v

    def webhook_secrets(self) -> list[str]:
        """Current secret first, then the previous one while a rotation is in progress."""
        secrets = [self.github_webhook_secret, self.github_webhook_secret_previous]
        return [s.get_secret_value() for s in secrets if s is not None]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_core_settings() -> CoreSettings:
    return CoreSettings()


def __getattr__(name: str) -> Settings:
    """`from budgetguard.config import settings` works (PEP 562) without reading the
    environment at import time, so tests can set env vars first."""
    if name == "settings":
        return get_settings()
    raise AttributeError(name)
