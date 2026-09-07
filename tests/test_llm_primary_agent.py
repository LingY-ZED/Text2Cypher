from __future__ import annotations

import logging

import pytest

from text2cypher.application.primary_agent import LLMPrimaryAgent
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import ChatPrompt, LLMResponse
from text2cypher.domain.query_shapes import QueryShape


class StubLLMClient:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_llm_primary_agent_returns_plan_with_one_llm_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = StubLLMClient(
        LLMResponse(
            content=(
                '{"analysis_summary":"分别取得上游和下游。","queries":['
                '{"question":"查询 A 的上游调用者","intent":"定位上游",'
                '"required_information":["上游调用者"]},'
                '{"question":"查询 A 的直接下游","intent":"定位下游",'
                '"required_information":["直接下游目标"]}]}'
            )
        )
    )

    plan = LLMPrimaryAgent(client).plan("查询 A 的上游和下游")

    assert plan.sub_questions == ("查询 A 的上游调用者", "查询 A 的直接下游")
    assert len(client.prompts) == 1
    assert "图谱 Schema" not in client.prompts[0].user
    assert "方法-[:服务于]->上游API" not in client.prompts[0].system
    assert caplog.records[-1].primary_agent_event == {
        "component": "primary_agent",
        "stage": "planning",
        "outcome": "planned",
        "query_count": 2,
        "decomposed": True,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (LLMGenerationError("secret-provider-response"), "llm_generation_error"),
        (LLMResponse(content="not-json-secret"), "invalid_response"),
        (
            LLMResponse(
                content=(
                    '{"analysis_summary":"摘要","queries":['
                    '{"question":"擅自改写","anchor":"ts-order-service",'
                    '"query_shape":"full_entry_chain","intent":"意图",'
                    '"required_information":["信息"]}]}'
                )
            ),
            "invalid_plan",
        ),
    ],
)
def test_llm_primary_agent_falls_back_without_sensitive_logging(
    response: LLMResponse | Exception,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(response)

    plan = LLMPrimaryAgent(client).plan("原始敏感问题")

    assert plan.sub_questions == ("原始敏感问题",)
    assert len(client.prompts) == 1
    event = caplog.records[-1].primary_agent_event
    assert event["outcome"] == "fallback"
    assert event["reason"] == reason
    assert "原始敏感问题" not in caplog.text
    assert "secret-provider-response" not in caplog.text
    assert "not-json-secret" not in caplog.text


def test_llm_primary_agent_does_not_catch_unexpected_errors() -> None:
    client = StubLLMClient(RuntimeError("programming error"))

    with pytest.raises(RuntimeError, match="programming error"):
        LLMPrimaryAgent(client).plan("问题")


def test_llm_primary_agent_preserves_impact_views_after_an_invalid_plan() -> None:
    client = StubLLMClient(LLMResponse(content="not-json"))

    plan = LLMPrimaryAgent(client).plan(
        "修改 InsidePaymentServiceImpl.pay 后，上游方法、入口 API 和下游服务是什么？"
    )

    assert plan.decomposed is True
    assert tuple(query.query_shape for query in plan.queries) == (
        QueryShape.UPSTREAM_REACHABILITY,
        QueryShape.REACHABLE_ENTRY_API,
        QueryShape.DIRECT_REST_EGRESS,
    )
    assert all(query.anchor == "InsidePaymentServiceImpl.pay" for query in plan.queries)


@pytest.mark.parametrize("max_queries", [1, 4, True])
def test_llm_primary_agent_rejects_invalid_limit(max_queries: int) -> None:
    with pytest.raises(ValueError, match="2 到 3"):
        LLMPrimaryAgent(
            StubLLMClient(LLMResponse(content="{}")),
            max_queries=max_queries,
        )
