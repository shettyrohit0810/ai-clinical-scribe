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


@lru_cache
def get_settings() -> Settings:
    _overlay_aws_secrets()
    return Settings()
