from __future__ import annotations

import pytest

from text2cypher.components.primary_agent import (
    PrimaryAgentPlanError,
    PrimaryAgentPromptBuilder,
    PrimaryAgentResponseError,
    PrimaryAgentResponseParser,
)


def test_primary_agent_prompt_is_schema_free_and_uses_capabilities_once() -> None:
    prompt = PrimaryAgentPromptBuilder().build("查询 getAllFood 的调用方和入口", 3)

    assert "用户问题：\n查询 getAllFood 的调用方和入口" in prompt.user
    assert "最多生成 3 个查询" in prompt.user
    assert prompt.system.count("# 代码知识图谱可检索业务能力") == 1
    assert "方法-[:服务于]->上游API" not in prompt.system
    assert "节点属性：" not in prompt.user
    assert "Schema" not in prompt.user
    assert "不要生成 Cypher" in prompt.system
    assert "未绑定指代" in prompt.system
    assert "不得按返回列机械拆分" in prompt.system
    assert "哪些方法调用 X" in prompt.system
    assert "不得自行添加原问题没有的直接、间接、可达性" in prompt.system
    assert "按每个对象给出度量" in prompt.system


@pytest.mark.parametrize(
    "content",
    [
        (
            '{"analysis_summary":"分别查询上游和下游。","queries":['
            '{"question":"查询 A 的上游调用者","intent":"定位上游",'
            '"required_information":["上游调用者"]},'
            '{"question":"查询 A 的直接下游","intent":"定位下游",'
            '"required_information":["直接下游目标"]}]}'
        ),
        (
            "```json\n"
            '{"analysis_summary":"查询原问题。","queries":['
            '{"question":"查询服务","intent":"定位服务",'
            '"required_information":["服务信息"]}]}\n'
            "```"
        ),
    ],
)
def test_primary_agent_response_parser_accepts_strict_json(
    content: str,
) -> None:
    original_question = "查询 A 的上游和下游" if "上游" in content else "查询服务"

    plan = PrimaryAgentResponseParser().parse(content, original_question, 3)

    assert tuple(query.query_id for query in plan.queries) == tuple(
        f"q{index}" for index in range(1, len(plan.queries) + 1)
    )
    assert all(query.intent for query in plan.queries)
    assert all(query.required_information for query in plan.queries)


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (
            '{"analysis_summary":"摘要","queries":['
            '{"question":"改写","intent":"意图",'
            '"required_information":["信息"]}]}',
            PrimaryAgentPlanError,
        ),
        (
            '{"analysis_summary":"摘要","queries":['
            '{"question":"原问题","intent":"意图",'
            '"required_information":["信息"],"extra":true}]}',
            PrimaryAgentResponseError,
        ),
        (
            '{"analysis_summary":"摘要","queries":[]}',
            PrimaryAgentPlanError,
        ),
        (
            '解释\n{"analysis_summary":"摘要","queries":[]}',
            PrimaryAgentResponseError,
        ),
        (
            '```python\n{"analysis_summary":"摘要","queries":[]}\n```',
            PrimaryAgentResponseError,
        ),
    ],
)
def test_primary_agent_response_parser_rejects_invalid_contracts(
    content: str,
    error: type[ValueError],
) -> None:
    with pytest.raises(error):
        PrimaryAgentResponseParser().parse(content, "原问题", 3)


@pytest.mark.parametrize("max_queries", [1, 4, True])
def test_primary_agent_prompt_and_parser_reject_invalid_limits(
    max_queries: int,
) -> None:
    with pytest.raises(ValueError, match="2 到 3"):
        PrimaryAgentPromptBuilder().build("问题", max_queries)
    with pytest.raises(ValueError, match="2 到 3"):
        PrimaryAgentResponseParser().parse("{}", "问题", max_queries)
