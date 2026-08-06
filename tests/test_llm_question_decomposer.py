from __future__ import annotations

import pytest

from text2cypher.application.question_decomposer import LLMQuestionDecomposer
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import (
    ChatPrompt,
    GraphSchema,
    LLMResponse,
    NodeSchema,
)


class StubLLMClient:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_llm_decomposer_returns_valid_independent_sub_questions() -> None:
    client = StubLLMClient(
        LLMResponse(
            content=(
                '{"sub_questions":['
                '"查询 Alice 的任职公司",'
                '"查询 Alice 的同事"]}'
            )
        )
    )
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose(
        "查询 Alice 的任职公司和同事",
        GraphSchema(nodes=(NodeSchema("Person"),)),
    )

    assert decomposition.sub_questions == (
        "查询 Alice 的任职公司",
        "查询 Alice 的同事",
    )
    assert len(client.prompts) == 1
    assert "Person" in client.prompts[0].user
    assert "Alice" in client.prompts[0].user


@pytest.mark.parametrize(
    "response",
    [
        LLMGenerationError("secret-provider-response"),
        LLMResponse(content=""),
        LLMResponse(content="not-json-secret"),
        LLMResponse(content='{"sub_questions":[]}'),
        LLMResponse(content='{"sub_questions":["重复","重复"]}'),
    ],
)
def test_llm_decomposer_falls_back_without_logging_sensitive_content(
    response: LLMResponse | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(response)
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose("原始敏感问题", GraphSchema())

    assert decomposition.sub_questions == ("原始敏感问题",)
    assert "QuestionDecomposer 失败" in caplog.text
    assert "原始敏感问题" not in caplog.text
    assert "secret-provider-response" not in caplog.text
    assert "not-json-secret" not in caplog.text


@pytest.mark.parametrize("max_subquestions", [1, 4])
def test_llm_decomposer_rejects_invalid_limit(max_subquestions: int) -> None:
    with pytest.raises(ValueError, match="2 到 3"):
        LLMQuestionDecomposer(
            StubLLMClient(LLMResponse(content="{}")),
            max_subquestions=max_subquestions,
        )
