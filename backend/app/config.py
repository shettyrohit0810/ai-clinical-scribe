"""Application settings.

Two sources, one shape:

- Local dev: values come from a git-ignored ``.env`` (see ``.env.example``).
- Production: the systemd unit sets ``AWS_SECRET_NAME``; before Settings is
  constructed we fetch that single JSON secret from AWS Secrets Manager using
  the EC2 instance role (boto3 resolves credentials from the instance
  metadata service — no static AWS keys exist anywhere in this project) and
  overlay it onto the process environment.

The fetch happens once per process via ``lru_cache`` and is reused for the
process lifetime. Tradeoff (deliberate): rotating the secret requires a
service restart. For a single-box deployment that is simpler and easier to
reason about than a refresh loop.
"""

import json
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _overlay_aws_secrets() -> None:
    """Merge the Secrets Manager JSON blob into os.environ (prod only)."""
    secret_name = os.environ.get("AWS_SECRET_NAME")
    if not secret_name:
        return  # local dev: .env is the only source
    import boto3  # local import: boto3 is never touched in local dev

    payload = boto3.client("secretsmanager").get_secret_value(
        SecretId=secret_name
    )["SecretString"]
    for key, value in json.loads(payload).items():
        # setdefault: a real env var (e.g. set for a one-off debug run)
        # still wins over the stored secret.
        os.environ.setdefault(key.upper(), str(value))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://scribe:scribe@localhost:5433/scribe"
    # Dev default is intentionally obvious junk; the production guard below
    # makes it impossible to boot prod without a real secret from Secrets
    # Manager (forgetting the key fails loudly at startup, not silently).
    jwt_secret: str = "dev-insecure-jwt-secret-do-not-use-in-prod"
    jwt_expire_minutes: int = 30
    # Consumed only by app/llm.py — the single gateway for all LLM traffic.
    # Empty default keeps tests (mocked LLM) and tooling importable without
    # a key; a real call without one fails inside llm.py with a structured
    # error, never a crash.
    anthropic_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    _overlay_aws_secrets()
    settings = Settings()
    if settings.app_env == "production" and settings.jwt_secret == "dev-insecure-jwt-secret-do-not-use-in-prod":
        raise RuntimeError(
            "JWT_SECRET missing from the production secret — refusing to start"
        )
    return settings
