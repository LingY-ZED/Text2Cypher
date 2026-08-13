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
    def __init__(
        self,
        response: LLMResponse | Exception,
        *additional_responses: LLMResponse | Exception,
    ) -> None:
        self._responses = [response, *additional_responses]
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_llm_decomposer_returns_valid_independent_sub_questions() -> None:
    client = StubLLMClient(
        LLMResponse(
            content=(
                '{"sub_questions":['
                '"查询 Alice 的任职公司",'
                '"查询 Alice 的同事"]}'
            )
        ),
        LLMResponse(content='{"valid":true,"reason":"VALID"}'),
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
    assert len(client.prompts) == 2
    assert "Person" in client.prompts[0].user
    assert "Alice" in client.prompts[0].user
    assert "原始用户问题" in client.prompts[1].user
    assert "候选子问题" in client.prompts[1].user


def test_llm_decomposer_skips_review_for_single_question() -> None:
    client = StubLLMClient(
        LLMResponse(content='{"sub_questions":["列出服务"]}')
    )

    decomposition = LLMQuestionDecomposer(client).decompose(
        "列出服务",
        GraphSchema(),
    )

    assert decomposition.sub_questions == ("列出服务",)
    assert len(client.prompts) == 1


def test_llm_decomposer_falls_back_when_review_rejects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(
        LLMResponse(content='{"sub_questions":["查询 A","查询 B"]}'),
        LLMResponse(
            content='{"valid":false,"reason":"RESULT_DEPENDENCY"}'
        ),
    )

    decomposition = LLMQuestionDecomposer(client).decompose(
        "查询 A 和 B",
        GraphSchema(),
    )

    assert decomposition.sub_questions == ("查询 A 和 B",)
    assert len(client.prompts) == 2
    event = caplog.records[-1].decomposition_review_event
    assert event == {
        "component": "decomposer",
        "stage": "review",
        "outcome": "rejected",
        "reason": "RESULT_DEPENDENCY",
    }


@pytest.mark.parametrize(
    "review_response",
    [
        LLMGenerationError("secret-review-error"),
        LLMResponse(content=""),
        LLMResponse(content="not-json-review"),
        LLMResponse(content='{"valid":false,"reason":"UNKNOWN"}'),
    ],
)
def test_llm_decomposer_falls_back_when_review_fails(
    review_response: LLMResponse | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(
        LLMResponse(content='{"sub_questions":["查询 A","查询 B"]}'),
        review_response,
    )

    decomposition = LLMQuestionDecomposer(client).decompose(
        "原始敏感问题",
        GraphSchema(),
    )

    assert decomposition.sub_questions == ("原始敏感问题",)
    assert len(client.prompts) == 2
    event = caplog.records[-1].decomposition_review_event
    assert event["outcome"] == "fallback"
    assert "原始敏感问题" not in caplog.text
    assert "secret-review-error" not in caplog.text


def test_llm_decomposer_accepts_review_without_rewriting_candidate() -> None:
    candidate = ("查询 A", "查询 B")
    client = StubLLMClient(
        LLMResponse(content='{"sub_questions":["查询 A","查询 B"]}'),
        LLMResponse(content='{"valid":true,"reason":"VALID"}'),
    )

    decomposition = LLMQuestionDecomposer(client).decompose(
        "查询 A 和 B",
        GraphSchema(),
    )

    assert decomposition.sub_questions == candidate


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
