from __future__ import annotations

from pathlib import Path

import pytest

from text2cypher.application.bootstrap import _build_prompt_builder
from text2cypher.config import Settings
from text2cypher.domain.errors import FewShotLibraryError
from text2cypher.domain.models import GraphSchema, NodeSchema, PropertySchema


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "neo4j_uri": "bolt://localhost:7687",
        "neo4j_username": "neo4j",
        "neo4j_password": "secret",
        "llm_base_url": "https://example.test/v1",
        "llm_api_key": "key",
        "llm_model": "demo-model",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _service_schema() -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema(
                "微服务",
                (PropertySchema("服务名称"),),
            ),
        )
    )


def test_bootstrap_enables_default_few_shot_library() -> None:
    builder = _build_prompt_builder(_settings())

    prompt = builder.build(_service_schema(), "列出所有微服务")

    assert "参考示例：" in prompt.user
    assert "列出所有微服务名称" in prompt.user


def test_bootstrap_disabled_preserves_zero_shot_and_skips_library_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_loaded() -> tuple[object, ...]:
        raise AssertionError("禁用 Few-shot 时不应加载示例库")

    monkeypatch.setattr(
        "text2cypher.application.bootstrap.JsonFewShotExampleLoader.load",
        fail_if_loaded,
    )
    builder = _build_prompt_builder(
        _settings(
            few_shot_enabled=False,
            few_shot_library_path=Path("missing.json"),
        )
    )

    prompt = builder.build(_service_schema(), "列出所有微服务")

    assert "参考示例" not in prompt.user
    assert "参考示例" not in prompt.system


def test_bootstrap_enabled_fails_fast_for_missing_custom_library(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(FewShotLibraryError, match="无法读取"):
        _build_prompt_builder(
            _settings(
                few_shot_enabled=True,
                few_shot_library_path=missing_path,
            )
        )
