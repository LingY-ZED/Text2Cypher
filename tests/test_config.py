from __future__ import annotations

from pathlib import Path

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


def test_settings_supports_model_output_control() -> None:
    settings = Settings(
        **(
            _settings_kwargs()
            | {"llm_max_tokens": 256, "llm_disable_thinking": True}
        )
    )

    assert settings.llm_max_tokens == 256
    assert settings.llm_disable_thinking is True


def test_settings_has_default_retry_policy() -> None:
    settings = Settings(**_settings_kwargs())

    assert settings.retry_enabled is True
    assert settings.retry_max_attempts == 3
    assert settings.retry_base_delay_seconds == 0.5
    assert settings.retry_max_delay_seconds == 4.0
    assert settings.cypher_correction_enabled is True
    assert settings.empty_result_correction_enabled is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"retry_max_attempts": 0}, "1 到 3"),
        ({"retry_max_attempts": 4}, "1 到 3"),
        ({"retry_base_delay_seconds": 0}, "正数"),
        ({"retry_max_delay_seconds": 0}, "正数"),
        (
            {
                "retry_base_delay_seconds": 1,
                "retry_max_delay_seconds": 0.5,
            },
            "最大重试延迟",
        ),
    ],
)
def test_settings_rejects_invalid_retry_policy(
    overrides: dict[str, float | int],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**(_settings_kwargs() | overrides))


def test_settings_normalizes_log_level_and_rejects_unknown_value() -> None:
    settings = Settings(**(_settings_kwargs() | {"log_level": "debug"}))

    assert settings.log_level == "DEBUG"

    with pytest.raises(ValidationError, match="有效日志级别"):
        Settings(**(_settings_kwargs() | {"log_level": "verbose"}))


def test_settings_has_production_few_shot_defaults() -> None:
    settings = Settings(**_settings_kwargs())

    assert settings.few_shot_enabled is True
    assert settings.few_shot_top_k == 3
    assert settings.few_shot_max_chars == 3500
    assert settings.few_shot_library_path is None


def test_settings_has_production_decomposition_defaults() -> None:
    settings = Settings(**_settings_kwargs())

    assert settings.question_decomposition_enabled is True
    assert settings.question_decomposition_max_subquestions == 3


@pytest.mark.parametrize("maximum", [1, 4])
def test_settings_rejects_invalid_decomposition_limit(maximum: int) -> None:
    with pytest.raises(ValidationError, match="必须在 2 到 3 之间"):
        Settings(
            **_settings_kwargs(),
            question_decomposition_max_subquestions=maximum,
        )


@pytest.mark.parametrize("top_k", [0, 4])
def test_settings_rejects_few_shot_top_k_outside_supported_range(
    top_k: int,
) -> None:
    with pytest.raises(ValidationError, match="必须在 1 到 3 之间"):
        Settings(**_settings_kwargs(), few_shot_top_k=top_k)


def test_settings_rejects_non_positive_few_shot_character_budget() -> None:
    with pytest.raises(ValidationError, match="必须为正数"):
        Settings(**_settings_kwargs(), few_shot_max_chars=0)


def test_settings_normalizes_optional_few_shot_library_path() -> None:
    empty_path = Settings(**_settings_kwargs(), few_shot_library_path=" ")
    custom_path = Settings(
        **_settings_kwargs(),
        few_shot_library_path="config/examples.json",
    )

    assert empty_path.few_shot_library_path is None
    assert custom_path.few_shot_library_path == Path("config/examples.json")
