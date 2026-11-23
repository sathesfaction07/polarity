from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration, `KE_`-prefixed environment variables or a `.env` file.

    No list-typed fields. Pydantic Settings parses those as JSON from the environment,
    which produces a confusing decode error for anyone who reasonably writes
    `KE_FIELDS=a,b`.
    """

    model_config = SettingsConfigDict(env_prefix="KE_", env_file=".env", extra="ignore")

    provider: str = "parser"

    aws_region: str = "us-east-1"
    bedrock_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    gcp_project: str = ""
    gcp_location: str = "us-central1"
    gemini_model: str = "gemini-1.5-flash"

    output_dir: Path = Path("output")
    output_format: str = "csv"
    log_level: str = "INFO"
