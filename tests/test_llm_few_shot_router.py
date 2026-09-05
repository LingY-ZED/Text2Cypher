from __future__ import annotations

import pytest

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    LLMResponse,
    NodeSchema,
    PrimaryAgentQuery,
    RelationshipPattern,
    RelationshipSchema,
)
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


def _schema() -> GraphSchema:
    return GraphSchema(
        nodes=(NodeSchema("A"), NodeSchema("B")),
        relationships=(RelationshipSchema("R"),),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )


def _example(
    identifier: str,
    *,
    requirements: FewShotSchemaRequirements | None = None,
    cypher: str = "MATCH (n) RETURN n",
    query_shape: QueryShape = QueryShape.GENERAL,
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category="test",
        question=f"{identifier} 示例问题",
        cypher=cypher,
        aliases=(f"{identifier} 别名",),
        tags=("测试",),
        schema_requirements=requirements or FewShotSchemaRequirements(),
        query_shape=query_shape,
    )


def _compatible_requirements() -> FewShotSchemaRequirements:
    return FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R",),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )


def test_router_sends_only_stable_compatible_metadata() -> None:
    compatible = _example(
        "a-compatible",
        requirements=_compatible_requirements(),
        cypher="MATCH (secret:Internal) RETURN secret",
    )
    second_compatible = _example(
        "b-compatible",
        requirements=_compatible_requirements(),
    )
    incompatible = _example(
        "z-incompatible",
        requirements=FewShotSchemaRequirements(
            node_labels=("A", "B"),
            relationship_types=("R",),
            patterns=(RelationshipPattern(("B",), "R", ("A",)),),
        ),
        cypher="MATCH (should_not:Appear) RETURN should_not",
    )
    client = StubLLMClient(
        LLMResponse(content='{"selected_ids":["a-compatible"]}')
    )
    router = LLMFewShotRouter(
        (incompatible, second_compatible, compatible),
        client,
    )

    selected = router.route("查询目标", _schema())

    assert tuple(example.id for example in selected) == ("a-compatible",)
    assert len(client.prompts) == 1
    prompt = client.prompts[0]
    assert prompt.user.index("a-compatible") < prompt.user.index("b-compatible")
    assert '"id":"a-compatible","category":"test"' in prompt.user
    assert "z-incompatible" not in prompt.user
    assert "MATCH" not in prompt.user
    assert "schema_requirements" not in prompt.user
    assert "Internal" not in prompt.user
    assert "查询锚点" in prompt.system
    assert "关系方向" in prompt.system
    assert "返回字段、分组维度和聚合形状" in prompt.system
    assert "不得仅因共享关键词而优先选择" in prompt.system
    assert "同时覆盖起始锚点和最终返回形状" in prompt.system
    assert "# 代码知识图谱业务语义：路由核心" in prompt.system
    assert "# 代码知识图谱业务语义：路由有序路径" not in prompt.system
    assert "方法-[:下游调用]->下游API" not in prompt.system


@pytest.mark.parametrize(
    "content",
    [
        '{"selected_ids":["b","a"]}',
        '```json\n{"selected_ids":["b","a"]}\n```',
    ],
)
def test_router_accepts_json_and_standard_json_fence_in_response(
    content: str,
) -> None:
    client = StubLLMClient(LLMResponse(content=content))
    router = LLMFewShotRouter(
        (_example("a"), _example("b")),
        client,
    )

    selected = router.route("查询", GraphSchema())

    assert tuple(example.id for example in selected) == ("b", "a")


def test_router_preserves_valid_ids_and_filters_invalid_values() -> None:
    client = StubLLMClient(
        LLMResponse(
            content=(
                '{"selected_ids":["missing","b",3,"b","a"],'
                '"ignored":"metadata"}'
            )
        )
    )
    router = LLMFewShotRouter(
        (_example("a"), _example("b")),
        client,
    )

    selected = router.route("查询", GraphSchema())

    assert tuple(example.id for example in selected) == ("b", "a")


@pytest.mark.parametrize(
    "content",
    [
        '解释文字\n{"selected_ids":["a"]}',
        '{"ids":["a"]}',
        '["a"]',
        '```python\n{"selected_ids":["a"]}\n```',
    ],
)
def test_router_falls_back_for_invalid_response(
    content: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(LLMResponse(content=content))
    router = LLMFewShotRouter((_example("a"), _example("b")), client)

    assert router.route("查询", GraphSchema()) == ()
    assert "ValueError" in caplog.text
    assert content not in caplog.text


def test_router_returns_empty_without_compatible_candidates() -> None:
    client = StubLLMClient(LLMResponse(content='{"selected_ids":["a"]}'))
    router = LLMFewShotRouter(
        (_example("a", requirements=_compatible_requirements()),),
        client,
    )

    assert router.route("查询", GraphSchema(nodes=(NodeSchema("City"),))) == ()
    assert client.prompts == []


def test_router_falls_back_after_llm_error_without_logging_sensitive_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(LLMGenerationError("router-secret-response"))
    router = LLMFewShotRouter((_example("a"), _example("b")), client)

    assert router.route("查询", GraphSchema()) == ()
    assert "LLMGenerationError" in caplog.text
    assert "router-secret-response" not in caplog.text


def test_router_applies_prompt_character_budget_after_id_validation() -> None:
    client = StubLLMClient(LLMResponse(content='{"selected_ids":["a","b"]}'))
    router = LLMFewShotRouter(
        (
            _example("a", cypher="MATCH (n) RETURN '超过预算'"),
            _example("b"),
        ),
        client,
        max_chars=1,
    )

    assert router.route("查询", GraphSchema()) == ()


def test_router_filters_schema_before_deterministic_query_shape() -> None:
    client = StubLLMClient(
        LLMResponse(
            content='{"selected_ids":["upstream","entry","general"]}'
        )
    )
    router = LLMFewShotRouter(
        (
            _example(
                "upstream",
                query_shape=QueryShape.UPSTREAM_REACHABILITY,
            ),
            _example("entry", query_shape=QueryShape.FULL_ENTRY_CHAIN),
            _example("general"),
        ),
        client,
    )

    selected = router.route("查询getTickets的上游调用链", GraphSchema())

    assert tuple(example.id for example in selected) == ("entry",)
    assert client.prompts == []


def test_router_keeps_only_direct_rest_example_for_explicit_shape() -> None:
    client = StubLLMClient(
        LLMResponse(content='{"selected_ids":["direct-rest","general"]}')
    )
    router = LLMFewShotRouter(
        (
            _example(
                "direct-rest",
                query_shape=QueryShape.DIRECT_REST_EGRESS,
            ),
            _example("general"),
        ),
        client,
    )

    selected = router.route(
        "InsidePaymentServiceImpl.pay 直接调用哪些下游 API 和目标服务？"
        "同时返回目标方法。",
        GraphSchema(),
    )

    assert tuple(example.id for example in selected) == ("direct-rest",)
    assert client.prompts == []


def test_router_keeps_service_mapping_general_for_complete_pairing() -> None:
    client = StubLLMClient(
        LLMResponse(content='{"selected_ids":["service-map","full-chain"]}')
    )
    router = LLMFewShotRouter(
        (
            _example("service-map"),
            _example(
                "full-chain",
                query_shape=QueryShape.FULL_DOWNSTREAM_CHAIN,
            ),
        ),
        client,
    )

    selected = router.route(
        "列出 ts-admin-basic-info-service 远程调用的下游 API，并保持调用方法、"
        "目标服务、目标上游 API 和目标入口方法的完整配对。",
        GraphSchema(),
    )

    assert tuple(example.id for example in selected) == ("service-map",)
    assert client.prompts == []


def test_router_recognizes_explicit_full_downstream_cross_service_chain() -> None:
    client = StubLLMClient(
        LLMResponse(content='{"selected_ids":["full-chain","ordered"]}')
    )
    router = LLMFewShotRouter(
        (
            _example(
                "full-chain",
                query_shape=QueryShape.FULL_DOWNSTREAM_CHAIN,
            ),
            _example(
                "ordered",
                query_shape=QueryShape.ORDERED_METHOD_PATH,
            ),
        ),
        client,
    )

    selected = router.route(
        "查询 InsidePaymentServiceImpl.pay 的完整下游跨服务调用链，"
        "返回有序方法路径、下游 API、目标服务、目标上游 API 和入口方法。",
        GraphSchema(),
    )

    assert tuple(example.id for example in selected) == ("full-chain",)
    assert client.prompts == []


def test_router_uses_primary_shape_and_semantics_as_authoritative_input() -> None:
    client = StubLLMClient(
        LLMResponse(content='{"selected_ids":["reachable-a"]}')
    )
    router = LLMFewShotRouter(
        (
            _example(
                "reachable-a",
                query_shape=QueryShape.UPSTREAM_REACHABILITY,
            ),
            _example(
                "reachable-b",
                query_shape=QueryShape.UPSTREAM_REACHABILITY,
            ),
            _example("direct", query_shape=QueryShape.DIRECT_UPSTREAM),
        ),
        client,
    )
    query = PrimaryAgentQuery(
        "q1",
        "查询 FoodServiceImpl.getAllFood 的调用关系",
        "查询所有上游可达方法和距离",
        ("目标方法", "上游可达方法", "调用距离"),
        "FoodServiceImpl.getAllFood",
        QueryShape.UPSTREAM_REACHABILITY,
    )

    selected = router.route_planned(query, GraphSchema())

    assert tuple(example.id for example in selected) == ("reachable-a",)
    prompt = client.prompts[0]
    assert "upstream_reachability" in prompt.user
    assert "Primary 语义计划" in prompt.user
    assert "FoodServiceImpl.getAllFood" in prompt.user
    assert "调用距离" in prompt.user
    assert '"id":"direct"' not in prompt.user


@pytest.mark.parametrize(
    ("required_information", "selected_id"),
    [
        (("下游 API", "目标服务"), "base-rest"),
        (
            ("下游 API", "目标服务", "目标 API", "入口方法", "匹配类型"),
            "external-mapping",
        ),
    ],
)
def test_router_distinguishes_direct_rest_return_structures(
    required_information: tuple[str, ...],
    selected_id: str,
) -> None:
    client = StubLLMClient(
        LLMResponse(content=f'{{"selected_ids":["{selected_id}"]}}')
    )
    router = LLMFewShotRouter(
        (
            _example(
                "base-rest",
                query_shape=QueryShape.DIRECT_REST_EGRESS,
            ),
            _example(
                "external-mapping",
                query_shape=QueryShape.DIRECT_REST_EGRESS,
            ),
        ),
        client,
    )
    query = PrimaryAgentQuery(
        "q1",
        "查询 InsidePaymentServiceImpl.pay 的直接 REST 出口",
        "查询直接 REST 下游及所需映射",
        required_information,
        "InsidePaymentServiceImpl.pay",
        QueryShape.DIRECT_REST_EGRESS,
    )

    selected = router.route_planned(query, GraphSchema())

    assert tuple(example.id for example in selected) == (selected_id,)
    prompt = client.prompts[0]
    assert "、".join(required_information) in prompt.user


def test_router_returns_single_compatible_shape_without_llm_call() -> None:
    client = StubLLMClient(LLMGenerationError("不应调用"))
    only = _example(
        "direct-downstream",
        query_shape=QueryShape.DIRECT_DOWNSTREAM_METHOD,
    )
    router = LLMFewShotRouter((only,), client)
    query = PrimaryAgentQuery(
        "q1",
        "查询 RebookServiceImpl.rebook 的直接下游方法",
        "查询直接调用的方法",
        ("目标方法", "直接下游方法"),
        "RebookServiceImpl.rebook",
        QueryShape.DIRECT_DOWNSTREAM_METHOD,
    )

    assert router.route_planned(query, GraphSchema()) == (only,)
    assert client.prompts == []
