from __future__ import annotations

import pytest

from text2cypher.application.question_decomposer import LLMQuestionDecomposer
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import (
    ChatPrompt,
    GraphSchema,
    LLMResponse,
    NodeSchema,
    SubQuestionPlan,
)


class StubLLMClient:
    def __init__(
        self,
        response: LLMResponse | Exception | list[LLMResponse | Exception],
    ) -> None:
        self._responses = response if isinstance(response, list) else [response]
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        response = (
            self._responses.pop(0)
            if len(self._responses) > 1
            else self._responses[0]
        )
        if isinstance(response, Exception):
            raise response
        return response


def test_llm_decomposer_reviews_valid_independent_sub_questions_once() -> None:
    client = StubLLMClient(
        [
            LLMResponse(
                content=(
                    '{"sub_questions":['
                    '{"id":"q1","question":"查询 Alice 的任职公司","inputs":[]},'
                    '{"id":"q2","question":"查询 Alice 的同事","inputs":[]}]}'
                )
            ),
            LLMResponse(
                content=(
                    '{"sub_questions":['
                    '{"id":"q1","question":"查询 Alice 的任职公司","inputs":[]},'
                    '{"id":"q2","question":"查询 Alice 的同事","inputs":[]}]}'
                )
            ),
        ]
    )
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose(
        "查询 Alice 的任职公司和同事",
        GraphSchema(nodes=(NodeSchema("Person"),)),
    )

    assert decomposition.sub_questions == (
        SubQuestionPlan("q1", "查询 Alice 的任职公司"),
        SubQuestionPlan("q2", "查询 Alice 的同事"),
    )
    assert len(client.prompts) == 2
    assert "Person" in client.prompts[0].user
    assert "Alice" in client.prompts[0].user
    assert "候选计划" in client.prompts[1].user


def test_llm_decomposer_reviews_invalid_plan_once_before_fallback() -> None:
    client = StubLLMClient(
        [
            LLMResponse(content="not-json"),
            LLMResponse(
                content='{"sub_questions":[{"id":"q1","question":"改写","inputs":[]}]}'
            ),
        ]
    )
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose(
        "查询 Alice 的任职公司",
        GraphSchema(nodes=(NodeSchema("Person"),)),
    )

    assert decomposition.sub_questions == (
        SubQuestionPlan("q1", "查询 Alice 的任职公司"),
    )
    assert len(client.prompts) == 2
    assert "invalid_plan" in client.prompts[1].user


def test_llm_decomposer_reviews_dependency_signal_in_single_plan() -> None:
    client = StubLLMClient(
        [
            LLMResponse(
                content='{"sub_questions":[{"id":"q1","question":"原始","inputs":[]}]}'
            ),
            LLMResponse(
                content=(
                    '{"sub_questions":['
                    '{"id":"q1","question":"查询实体并返回名称","inputs":[]},'
                    '{"id":"q2","question":"查询这些实体所属组织","inputs":['
                    '{"source_id":"q1","columns":["名称"]}]}]}'
                )
            ),
        ]
    )
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose(
        "先查询实体名称，再查询这些实体所属组织",
        GraphSchema(nodes=(NodeSchema("Person"),)),
    )

    assert decomposition.sub_questions[1].depends_on == ("q1",)
    assert "dependency_signal_without_plan" in client.prompts[1].user


def test_llm_decomposer_falls_back_when_review_fails() -> None:
    client = StubLLMClient(
        [
            LLMResponse(content="not-json"),
            LLMGenerationError("review-secret"),
        ]
    )
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose("原始敏感问题", GraphSchema())

    assert decomposition.sub_questions == (SubQuestionPlan("q1", "原始敏感问题"),)
    assert len(client.prompts) == 2


@pytest.mark.parametrize(
    "response",
    [
        LLMGenerationError("secret-provider-response"),
        LLMResponse(content=""),
        LLMResponse(content="not-json-secret"),
        LLMResponse(content='{"sub_questions":[]}'),
        LLMResponse(
            content=(
                '{"sub_questions":['
                '{"id":"q1","question":"重复","inputs":[]},'
                '{"id":"q2","question":"重复","inputs":[]}]}'
            )
        ),
    ],
)
def test_llm_decomposer_falls_back_without_logging_sensitive_content(
    response: LLMResponse | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(response)
    decomposer = LLMQuestionDecomposer(client)

    decomposition = decomposer.decompose("原始敏感问题", GraphSchema())

    assert decomposition.sub_questions == (
        SubQuestionPlan("q1", "原始敏感问题"),
    )
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
