from __future__ import annotations

import json

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
    assert "以原问题要求的独立结果集合为单位" in prompt.system
    assert "是否遗漏了可独立计算的并列集合" in prompt.system
    assert "不得按返回列机械拆分" in prompt.system
    assert "哪些方法调用 X" in prompt.system
    assert "不得自行添加原问题没有的直接、间接、可达性" in prompt.system
    assert "按每个对象给出度量" in prompt.system
    assert '"anchor":"固定对象"' in prompt.user
    assert '"query_shape":"查询形状"' in prompt.user
    assert "upstream_reachability" in prompt.user
    assert "full_entry_chain" in prompt.user
    assert "requested_fields" not in prompt.user


def test_primary_agent_prompt_defines_independent_three_view_splits() -> None:
    prompt = PrimaryAgentPromptBuilder().build(
        "分析 ts-security-service 的公开 API、REST 下游服务和 REST 上游服务。",
        3,
    )

    assert "逐项列出原问题要求的结果集合" in prompt.system
    assert "从原始锚点独立计算且不依赖其他集合" in prompt.system
    assert "InsidePaymentServiceImpl.pay" in prompt.system
    assert "`upstream_reachability`" in prompt.system
    assert "`reachable_entry_api`" in prompt.system
    assert "`direct_rest_egress`" in prompt.system
    assert "ts-security-service 的公开 API 路径和 HTTP 方法" in prompt.system
    assert "通过 REST 直接依赖 ts-security-service 的上游服务" in prompt.system
    assert "三个服务级子问题均使用 `general`" in prompt.system


def test_primary_agent_prompt_requires_three_independent_call_chain_views() -> None:
    prompt = PrimaryAgentPromptBuilder().build(
        "POST /api/example 在 ts-demo-service 中的完整调用链是什么？",
        3,
    )

    assert "完整方法调用链是固定例外" in prompt.system
    assert "必须正好输出三个 query_shape=general 的查询" in prompt.user
    assert "服务内调用明细、REST 跨服务明细、MQ 发布消费明细" in prompt.user
    assert "逐字包含原问题中的 API 路径或方法锚点" in prompt.user
    assert "根方法、图谱版本、源服务、源API路径、源HTTP方法" in prompt.user
    assert "完整 JSON 规划示例" in prompt.user
    assert prompt.user.count('"query_shape":"general"') == 3
    assert prompt.user.count("/api/example") >= 4
    assert prompt.user.count("ts-demo-service") >= 4
    assert "同一 API 的请求与响应字段" in prompt.system
    assert "同一 REST 调用的调用方与目标字段" in prompt.system
    assert "同一分组的维度与聚合值" in prompt.system
    assert "MATCH" not in prompt.system


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


def test_primary_agent_parser_accepts_and_validates_explicit_semantic_plan() -> None:
    question = "FoodServiceImpl.getAllFood 的上游调用链是什么？"
    content = (
        '{"analysis_summary":"查询从入口到目标的完整上游调用链。","queries":['
        '{"question":"FoodServiceImpl.getAllFood 的上游调用链是什么？",'
        '"anchor":"FoodServiceImpl.getAllFood",'
        '"query_shape":"full_entry_chain",'
        '"intent":"查询入口 API 到锚点方法的完整有序链",'
        '"required_information":["目标方法","入口 API","有序方法路径"]}]}'
    )

    plan = PrimaryAgentResponseParser().parse(content, question, 3)

    assert plan.queries[0].anchor == "FoodServiceImpl.getAllFood"
    assert plan.queries[0].query_shape.value == "full_entry_chain"
    assert plan.queries[0].effective_query_shape.value == "full_entry_chain"


def test_primary_agent_parser_accepts_fixed_full_method_split() -> None:
    question = (
        "POST /api/example 在 ts-demo-service 中、图谱版本 v2 的完整调用链是什么？"
    )
    anchor = "/api/example"
    qualifiers = "POST /api/example、服务 ts-demo-service、图谱版本 v2"
    content = json.dumps(
        {
            "analysis_summary": "分别查询服务内、REST 和 MQ 调用明细",
            "queries": [
                {
                    "question": f"以 {qualifiers} 为锚点查询服务内调用明细",
                    "anchor": anchor,
                    "query_shape": "general",
                    "intent": "查询服务内有序方法序列",
                    "required_information": [
                        "根方法",
                        "图谱版本",
                        "源服务",
                        "源API路径",
                        "源HTTP方法",
                        "方法路径",
                        "叶子方法",
                    ],
                },
                {
                    "question": f"以 {qualifiers} 为锚点查询 REST 跨服务明细",
                    "anchor": anchor,
                    "query_shape": "general",
                    "intent": "查询最多两层 REST 跨服务调用",
                    "required_information": [
                        "根方法",
                        "图谱版本",
                        "REST层级",
                        "源服务",
                        "源API路径",
                        "源HTTP方法",
                        "源方法",
                        "方法路径",
                        "下游API路径",
                        "目标服务",
                        "目标API路径",
                        "目标HTTP方法",
                        "目标方法",
                    ],
                },
                {
                    "question": f"以 {qualifiers} 为锚点查询 MQ 发布消费明细",
                    "anchor": anchor,
                    "query_shape": "general",
                    "intent": "查询一次 MQ 发布、路由和消费",
                    "required_information": [
                        "根方法",
                        "图谱版本",
                        "源服务",
                        "源API路径",
                        "源HTTP方法",
                        "发布方法",
                        "发布方法路径",
                        "消息交换机",
                        "消息队列",
                        "路由键",
                        "目标服务",
                        "消费方法",
                        "消费者方法路径",
                    ],
                },
            ],
        },
        ensure_ascii=False,
    )

    plan = PrimaryAgentResponseParser().parse(content, question, 3)

    assert plan.decomposed is True
    assert tuple(query.query_shape.value for query in plan.queries) == (
        "general",
        "general",
        "general",
    )
    assert all(query.anchor == anchor for query in plan.queries)


@pytest.mark.parametrize(
    ("original_question", "content", "message"),
    [
        (
            "查询 A",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询 A","anchor":"A","query_shape":"unknown",'
            '"intent":"意图","required_information":["信息"]}]}',
            "query_shape",
        ),
        (
            "查询 A 的直接上游",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询 A 的直接上游","anchor":"A",'
            '"query_shape":"upstream_reachability","intent":"意图",'
            '"required_information":["信息"]}]}',
            "冲突",
        ),
        (
            "查询 A 的上游调用链",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询 A 的上游调用链","anchor":"A",'
            '"query_shape":"upstream_reachability","intent":"意图",'
            '"required_information":["上游方法"]}]}',
            "冲突",
        ),
        (
            "分析 A",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询 A 的上游","intent":"上游",'
            '"required_information":["方法"]},'
            '{"question":"再查询上述方法的服务","intent":"服务",'
            '"required_information":["服务"]}]}',
            "依赖其他查询结果",
        ),
        (
            "查询 A 的完整入口调用链",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询 A 的入口 API","intent":"入口",'
            '"required_information":["API"]},'
            '{"question":"查询 A 的方法路径","intent":"路径",'
            '"required_information":["路径"]}]}',
            "不得拆分",
        ),
        (
            "查询 A 的完整调用链",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询 A 的 REST 分段","intent":"REST",'
            '"required_information":["下游 API"]},'
            '{"question":"查询 A 的 MQ 分段","intent":"MQ",'
            '"required_information":["消息队列"]}]}',
            "必须拆成三个查询",
        ),
        (
            "查询到达 ts-order-other-service 的 REST 上游跨服务链",
            '{"analysis_summary":"摘要","queries":['
            '{"question":"查询到达 ts-order-other-service 的 REST 上游跨服务链",'
            '"anchor":"ts-order-other-service",'
            '"query_shape":"full_entry_chain","intent":"查询跨服务对应",'
            '"required_information":["调用服务","下游 API"]}]}',
            "不能用于服务级",
        ),
    ],
)
def test_primary_agent_parser_rejects_invalid_semantic_plans(
    original_question: str,
    content: str,
    message: str,
) -> None:
    with pytest.raises(
        (PrimaryAgentResponseError, PrimaryAgentPlanError),
        match=message,
    ):
        PrimaryAgentResponseParser().parse(content, original_question, 3)


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (
            '{"analysis_summary":"摘要","queries":['
            '{"question":"原问题","intent":"意图",'
            '"required_information":["信息"],"extra":true}]}',
            PrimaryAgentResponseError,
        ),
        (
            '{"analysis_summary":"摘要","queries":['
            '{"question":"原问题","intent":"意图",'
            '"required_information":["信息"],'
            '"query_shape":"general","requested_fields":["字段"]}]}',
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


def test_primary_agent_parser_restores_original_single_question() -> None:
    content = (
        '{"analysis_summary":"查询完整上游调用链。","queries":['
        '{"question":"改写后的上游问题","anchor":"FoodServiceImpl.getAllFood",'
        '"query_shape":"full_entry_chain","intent":"查询完整入口链",'
        '"required_information":["目标方法","入口 API","方法路径"]}]}'
    )
    original = "FoodServiceImpl.getAllFood 的上游调用链是什么？"

    plan = PrimaryAgentResponseParser().parse(content, original, 3)

    assert plan.queries[0].question == original
    assert plan.queries[0].query_shape.value == "full_entry_chain"


@pytest.mark.parametrize("max_queries", [1, 4, True])
def test_primary_agent_prompt_and_parser_reject_invalid_limits(
    max_queries: int,
) -> None:
    with pytest.raises(ValueError, match="2 到 3"):
        PrimaryAgentPromptBuilder().build("问题", max_queries)
    with pytest.raises(ValueError, match="2 到 3"):
        PrimaryAgentResponseParser().parse("{}", "问题", max_queries)
