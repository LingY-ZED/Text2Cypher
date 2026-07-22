from __future__ import annotations

import pytest
from pydantic import ValidationError

from text2cypher.config import Settings


def _settings_kwargs() -> dict[str, str]:
    return {
        "neo4j_uri": "bolt://localhost:7687",
        "neo4j_username": "neo4j",
        "neo4j_password": "secret",
        "llm_base_url": "https://example.test/v1/",
        "llm_api_key": "key",
        "llm_model": "demo-model",
    }


def test_settings_normalize_llm_base_url_and_keep_secrets() -> None:
    settings = Settings(**_settings_kwargs())

    assert settings.llm_base_url == "https://example.test/v1"
    assert settings.neo4j_password.get_secret_value() == "secret"


def test_settings_reject_non_positive_limit() -> None:
    with pytest.raises(ValidationError, match="必须为正数"):
        Settings(**_settings_kwargs(), max_result_rows=0)


def test_settings_reject_blank_secret() -> None:
    with pytest.raises(ValidationError, match="不能为空"):
        Settings(**(_settings_kwargs() | {"llm_api_key": " "}))
