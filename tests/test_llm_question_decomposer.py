from __future__ import annotations

import pytest

from text2cypher.application.question_decomposer import LLMQuestionDecomposer
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import ChatPrompt, GraphSchema, LLMResponse, NodeSchema


class StubLLMClient:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_legacy_decomposer_projects_primary_agent_plan_without_schema() -> None:
    client = StubLLMClient(
        LLMResponse(
            content=(
                '{"analysis_summary":"分别取得任职公司和同事。","queries":['
                '{"question":"查询 Alice 的任职公司","intent":"定位任职公司",'
                '"required_information":["任职公司"]},'
                '{"question":"查询 Alice 的同事","intent":"定位同事",'
                '"required_information":["同事信息"]}]}'
            )
        )
    )

    decomposition = LLMQuestionDecomposer(client).decompose(
        "查询 Alice 的任职公司和同事",
        GraphSchema(nodes=(NodeSchema("Person"),)),
    )

    assert decomposition.sub_questions == (
        "查询 Alice 的任职公司",
        "查询 Alice 的同事",
    )
    assert len(client.prompts) == 1
    assert "Person" not in client.prompts[0].user
    assert "方法-[:服务于]->上游API" not in client.prompts[0].system


def test_legacy_decomposer_keeps_obsolete_reviewer_arguments_without_calling_them(
) -> None:
    decomposer_client = StubLLMClient(
        LLMResponse(
            content=(
                '{"analysis_summary":"分别查询。","queries":['
                '{"question":"查询 A","intent":"意图 A",'
                '"required_information":["信息 A"]},'
                '{"question":"查询 B","intent":"意图 B",'
                '"required_information":["信息 B"]}]}'
            )
        )
    )
    reviewer_client = StubLLMClient(LLMResponse(content="not-used"))

    decomposition = LLMQuestionDecomposer(
        decomposer_client,
        review_llm_client=reviewer_client,
    ).decompose("查询 A 和 B", GraphSchema())

    assert decomposition.sub_questions == ("查询 A", "查询 B")
    assert len(decomposer_client.prompts) == 1
    assert reviewer_client.prompts == []


@pytest.mark.parametrize(
    "response",
    [
        LLMGenerationError("secret-provider-response"),
        LLMResponse(content="not-json-secret"),
        LLMResponse(
            content=(
                '{"analysis_summary":"摘要","queries":['
                '{"question":"擅自改写","intent":"意图",'
                '"required_information":["信息"]}]}'
            )
        ),
    ],
)
def test_legacy_decomposer_uses_primary_agent_fallback(
    response: LLMResponse | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(response)

    decomposition = LLMQuestionDecomposer(client).decompose(
        "原始敏感问题",
        GraphSchema(),
    )

    assert decomposition.sub_questions == ("原始敏感问题",)
    assert len(client.prompts) == 1
    assert "原始敏感问题" not in caplog.text
    assert "secret-provider-response" not in caplog.text
    assert "not-json-secret" not in caplog.text


@pytest.mark.parametrize("max_subquestions", [1, 4])
def test_legacy_decomposer_rejects_invalid_limit(max_subquestions: int) -> None:
    with pytest.raises(ValueError, match="2 到 3"):
        LLMQuestionDecomposer(
            StubLLMClient(LLMResponse(content="{}")),
            max_subquestions=max_subquestions,
        )
