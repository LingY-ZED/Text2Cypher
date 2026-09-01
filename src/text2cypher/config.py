"""供真实适配器使用的环境变量配置。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import SecretStr, field_validator, model_validator
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
    retry_enabled: bool = True
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 4.0
    cypher_correction_enabled: bool = True
    empty_result_correction_enabled: bool = True
    primary_agent_enabled: bool = True
    primary_agent_max_queries: int = 3
    question_decomposition_enabled: bool = True
    question_decomposition_max_subquestions: int = 3
    few_shot_enabled: bool = True
    few_shot_top_k: int = 3
    few_shot_max_chars: int = 3500
    few_shot_library_path: Path | None = None
    natural_language_summary_enabled: bool = True
    natural_language_summary_max_input_chars: int = 16000
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
        "few_shot_max_chars",
        "natural_language_summary_max_input_chars",
        "schema_timeout_seconds",
        "query_timeout_seconds",
        "max_result_rows",
    )
    @classmethod
    def positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("必须为正数")
        return value

    @field_validator("few_shot_top_k")
    @classmethod
    def valid_few_shot_top_k(cls, value: int) -> int:
        if not 1 <= value <= 3:
            raise ValueError("必须在 1 到 3 之间")
        return value

    @field_validator(
        "primary_agent_max_queries",
        "question_decomposition_max_subquestions",
    )
    @classmethod
    def valid_primary_agent_limit(cls, value: int) -> int:
        if not 2 <= value <= 3:
            raise ValueError("必须在 2 到 3 之间")
        return value

    @field_validator("retry_max_attempts")
    @classmethod
    def valid_retry_max_attempts(cls, value: int) -> int:
        if not 1 <= value <= 3:
            raise ValueError("必须在 1 到 3 之间")
        return value

    @field_validator("retry_base_delay_seconds", "retry_max_delay_seconds")
    @classmethod
    def positive_retry_delay(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("必须为正数")
        return value

    @model_validator(mode="after")
    def valid_retry_delay_range(self) -> Settings:
        if self.retry_max_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError("最大重试延迟不能小于基础重试延迟")
        legacy_enabled_supplied = (
            "question_decomposition_enabled" in self.model_fields_set
        )
        primary_enabled_supplied = "primary_agent_enabled" in self.model_fields_set
        if legacy_enabled_supplied and not primary_enabled_supplied:
            self.primary_agent_enabled = self.question_decomposition_enabled
        self.question_decomposition_enabled = self.primary_agent_enabled

        legacy_limit_supplied = (
            "question_decomposition_max_subquestions" in self.model_fields_set
        )
        primary_limit_supplied = "primary_agent_max_queries" in self.model_fields_set
        if legacy_limit_supplied and not primary_limit_supplied:
            self.primary_agent_max_queries = (
                self.question_decomposition_max_subquestions
            )
        self.question_decomposition_max_subquestions = self.primary_agent_max_queries
        return self

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("必须是有效日志级别")
        return normalized

    @field_validator("few_shot_library_path", mode="before")
    @classmethod
    def empty_few_shot_library_path(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value
