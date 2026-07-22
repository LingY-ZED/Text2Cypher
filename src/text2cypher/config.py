"""供真实适配器使用的环境变量配置。"""

from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 TEXT2CYPHER_ 环境变量加载的运行时配置。"""

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
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 512
    llm_disable_thinking: bool = False
    schema_timeout_seconds: int = 10
    query_timeout_seconds: int = 10
    max_result_rows: int = 100
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> Settings:
        """从环境变量和可选 .env 文件加载配置。"""

        return cls()  # type: ignore[call-arg]

    @field_validator("neo4j_uri", "neo4j_username", "neo4j_database", "llm_model")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("neo4j_password", "llm_api_key")
    @classmethod
    def non_blank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("不能为空")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_llm_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("必须是 http 或 https URL")
        return normalized

    @field_validator(
        "llm_timeout_seconds",
        "llm_max_tokens",
        "schema_timeout_seconds",
        "query_timeout_seconds",
        "max_result_rows",
    )
    @classmethod
    def positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("必须为正数")
        return value
