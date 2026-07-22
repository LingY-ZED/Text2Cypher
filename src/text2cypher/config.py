"""Environment-backed settings for the future live adapters."""

from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from TEXT2CYPHER_ environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TEXT2CYPHER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    neo4j_uri: str
    neo4j_username: str
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"
    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str
    llm_timeout_seconds: int = 30
    schema_timeout_seconds: int = 10
    query_timeout_seconds: int = 10
    max_result_rows: int = 100
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> Settings:
        """Load settings from the configured environment and optional .env file."""

        return cls()  # type: ignore[call-arg]

    @field_validator("neo4j_uri", "neo4j_username", "neo4j_database", "llm_model")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("neo4j_password", "llm_api_key")
    @classmethod
    def non_blank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_llm_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("must be an http or https URL")
        return normalized

    @field_validator(
        "llm_timeout_seconds",
        "schema_timeout_seconds",
        "query_timeout_seconds",
        "max_result_rows",
    )
    @classmethod
    def positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be positive")
        return value
