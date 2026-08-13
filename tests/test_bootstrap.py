from __future__ import annotations

from pathlib import Path

import pytest

from text2cypher.application.bootstrap import (
    _build_cypher_corrector,
    _build_few_shot_router,
    _build_question_decomposer,
    _build_result_summarizer,
)
from text2cypher.application.result_summarizer import LLMResultSummarizer
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.config import Settings
from text2cypher.domain.errors import FewShotLibraryError
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    CypherFailureKind,
    CypherFailureSource,
    GraphSchema,
    LLMResponse,
    NodeSchema,
    PropertySchema,
)


class StubLLMClient:
    def __init__(self, content: str = '{"selected_ids":[]}') -> None:
        self._content = content
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(content=self._content)


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
    router_client = StubLLMClient(
        '{"selected_ids":["simple-list-services"]}'
    )
    router = _build_few_shot_router(_settings(), router_client)
    assert router is not None

    examples = router.route("列出所有微服务", _service_schema())
    prompt = DefaultPromptBuilder().build(
        _service_schema(),
        "列出所有微服务",
        examples,
    )

    assert "参考示例：" in prompt.user
    assert "系统中有哪些微服务？" in prompt.user
    assert len(router_client.prompts) == 1


def test_bootstrap_enables_decomposer_with_shared_client() -> None:
    shared_client = StubLLMClient('{"sub_questions":["列出服务"]}')
    decomposer = _build_question_decomposer(_settings(), shared_client)
    assert decomposer is not None

    decomposition = decomposer.decompose("列出服务", _service_schema())

    assert decomposition.sub_questions == ("列出服务",)
    assert len(shared_client.prompts) == 1


def test_bootstrap_reuses_shared_client_for_decomposer_review() -> None:
    shared_client = StubLLMClient(
        '{"sub_questions":["查询 REST 下游","查询 MQ 下游"]}'
    )
    decomposer = _build_question_decomposer(_settings(), shared_client)
    assert decomposer is not None

    decomposition = decomposer.decompose(
        "查询服务的 REST 和 MQ 下游",
        _service_schema(),
    )

    assert decomposition.sub_questions == (
        "查询服务的 REST 和 MQ 下游",
    )
    assert len(shared_client.prompts) == 2


def test_bootstrap_can_disable_decomposer_without_model_call() -> None:
    shared_client = StubLLMClient("not-used")

    decomposer = _build_question_decomposer(
        _settings(question_decomposition_enabled=False),
        shared_client,
    )

    assert decomposer is None
    assert shared_client.prompts == []


def test_bootstrap_disabled_preserves_zero_shot_and_skips_library_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_loaded() -> tuple[object, ...]:
        raise AssertionError("禁用 Few-shot 时不应加载示例库")

    monkeypatch.setattr(
        "text2cypher.application.bootstrap.JsonFewShotExampleLoader.load",
        fail_if_loaded,
    )
    router = _build_few_shot_router(
        _settings(
            few_shot_enabled=False,
            few_shot_library_path=Path("missing.json"),
        ),
        StubLLMClient(),
    )

    assert router is None


def test_bootstrap_enabled_fails_fast_for_missing_custom_library(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(FewShotLibraryError, match="无法读取"):
        _build_few_shot_router(
            _settings(
                few_shot_enabled=True,
                few_shot_library_path=missing_path,
            ),
            StubLLMClient(),
        )


def test_bootstrap_enables_cypher_corrector_with_shared_client() -> None:
    client = StubLLMClient("RETURN 1")
    corrector = _build_cypher_corrector(_settings(), client)

    assert corrector is not None
    response = corrector.correct(
        ChatPrompt(system="system", user="user"),
        "bad output",
        CypherFailureContext(
            kind=CypherFailureKind.PARSE,
            source=CypherFailureSource.LOCAL,
            message="模型响应不包含 Cypher",
        ),
    )

    assert response.content == "RETURN 1"
    assert len(client.prompts) == 1
    assert "bad output" in client.prompts[0].user


def test_bootstrap_can_disable_cypher_corrector() -> None:
    client = StubLLMClient("not-used")

    corrector = _build_cypher_corrector(
        _settings(cypher_correction_enabled=False),
        client,
    )

    assert corrector is None
    assert client.prompts == []


def test_bootstrap_summary_uses_shared_client_defaults() -> None:
    summarizer = _build_result_summarizer(_settings(), StubLLMClient())

    assert isinstance(summarizer, LLMResultSummarizer)
