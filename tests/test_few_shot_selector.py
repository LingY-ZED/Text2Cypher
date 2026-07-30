from __future__ import annotations

import pytest

from text2cypher.components.few_shot_selector import HybridFewShotSelector
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    NodeSchema,
    RelationshipPattern,
    RelationshipSchema,
)


def _example(
    identifier: str,
    question: str,
    *,
    aliases: tuple[str, ...] = ("同义查询",),
    tags: tuple[str, ...] = ("查询",),
    requirements: FewShotSchemaRequirements | None = None,
    cypher: str = "MATCH (n) RETURN n",
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category=identifier,
        question=question,
        cypher=cypher,
        aliases=aliases,
        tags=tags,
        schema_requirements=requirements or FewShotSchemaRequirements(),
    )


def _select(
    selector: HybridFewShotSelector,
    question: str,
    schema: GraphSchema | None = None,
) -> tuple[FewShotExample, ...]:
    current_schema = schema or GraphSchema()
    return selector.select(
        question,
        current_schema,
        SchemaGraphBuilder().build(current_schema),
    )


def _intent_examples() -> tuple[FewShotExample, ...]:
    return (
        _example(
            "simple",
            "列出所有微服务",
            aliases=("查看服务列表",),
            tags=("简单查询", "微服务"),
        ),
        _example(
            "ownership-api",
            "查询 payment-service 提供的 API 端点",
            aliases=("某个服务有哪些接口",),
            tags=("归属", "API"),
        ),
        _example(
            "downstream-call",
            "FoodServiceImpl.getAllFood 调用了哪些下游服务",
            aliases=("查询方法依赖的远程服务",),
            tags=("调用链", "下游", "REST"),
        ),
        _example(
            "reverse-impact",
            "修改 getAllFood 会影响哪些上游调用方",
            aliases=("反向查询调用这个方法的方法",),
            tags=("影响分析", "上游"),
        ),
        _example(
            "mq",
            "查询 order-service 发布到哪些 MQ 队列",
            aliases=("查看服务的消息发布和路由",),
            tags=("消息", "交换机", "消费"),
        ),
        _example(
            "aggregate",
            "统计 REST 跨服务调用次数",
            aliases=("计算远程调用数量",),
            tags=("聚合", "count"),
        ),
        _example(
            "compound-impact",
            "修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务",
            aliases=("综合分析方法的上下游影响",),
            tags=("复合影响", "上游", "下游", "调用链"),
        ),
    )


@pytest.mark.parametrize(
    ("question", "expected_id"),
    [
        ("有哪些微服务？", "simple"),
        ("payment-service 对外提供哪些接口？", "ownership-api"),
        ("FoodServiceImpl.getAllFood 依赖哪些下游服务？", "downstream-call"),
        ("变更 getAllFood 的上游影响范围是什么？", "reverse-impact"),
        ("order-service 向哪些消息队列发布 MQ 消息？", "mq"),
        ("请统计 REST 远程调用的数量", "aggregate"),
        (
            "修改 FoodServiceImpl.getAllFood，同时分析上游调用方和下游服务",
            "compound-impact",
        ),
    ],
)
def test_selector_ranks_expected_intent_category_first(
    question: str,
    expected_id: str,
) -> None:
    selector = HybridFewShotSelector(_intent_examples(), top_k=1)

    selected = _select(selector, question)

    assert selected[0].id == expected_id


def test_selector_normalizes_case_for_qualified_identifiers() -> None:
    selector = HybridFewShotSelector(_intent_examples(), top_k=1)

    selected = _select(
        selector,
        "FOODSERVICEIMPL.GETALLFOOD 调用了什么下游？",
    )

    assert selected[0].id == "downstream-call"


def test_selector_extracts_supported_entity_shapes() -> None:
    features = HybridFewShotSelector._entity_shapes(
        "FoodServiceImpl.getAllFood 从 order-service 调用 /api/foods，"
        "并统计 REST 与 MQ 上下游"
    )

    assert features == {
        "aggregate",
        "api_path",
        "downstream",
        "method_identifier",
        "mq",
        "qualified_identifier",
        "rest",
        "service_name",
        "upstream",
    }


def test_selector_filters_incompatible_examples_before_scoring() -> None:
    schema = GraphSchema(
        nodes=(NodeSchema("A"), NodeSchema("B")),
        relationships=(RelationshipSchema("R"),),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )
    incompatible = _example(
        "exact-but-reversed",
        "查询关键目标",
        requirements=FewShotSchemaRequirements(
            node_labels=("A", "B"),
            relationship_types=("R",),
            patterns=(RelationshipPattern(("B",), "R", ("A",)),),
        ),
    )
    compatible = _example(
        "compatible",
        "查找目标",
        aliases=("查询目标信息",),
        requirements=FewShotSchemaRequirements(
            node_labels=("A", "B"),
            relationship_types=("R",),
            patterns=(RelationshipPattern(("A",), "R", ("B",)),),
        ),
    )
    selector = HybridFewShotSelector(
        (incompatible, compatible),
        top_k=1,
        min_score=0,
    )

    selected = _select(selector, "查询关键目标", schema)

    assert tuple(example.id for example in selected) == ("compatible",)


def test_selector_returns_empty_for_blank_or_low_relevance_question() -> None:
    selector = HybridFewShotSelector(_intent_examples())

    assert _select(selector, " ") == ()
    assert _select(selector, "量子纠缠与星系演化") == ()


def test_selector_respects_top_k_and_stable_id_tie_break() -> None:
    examples = tuple(
        _example(identifier, "查询相同目标")
        for identifier in ("d", "b", "a", "c")
    )
    selector = HybridFewShotSelector(examples, top_k=3, min_score=0)

    first = _select(selector, "查询相同目标")
    second = _select(selector, "查询相同目标")

    assert tuple(example.id for example in first) == ("a", "b", "c")
    assert first == second


def test_selector_stops_when_highest_ranked_example_exceeds_budget() -> None:
    oversized = _example(
        "oversized",
        f"查询目标{'很长' * 100}",
        cypher=f"MATCH (n) RETURN n, '{'内容' * 100}'",
    )
    smaller = _example("smaller", "查询目标")
    selector = HybridFewShotSelector(
        (oversized, smaller),
        min_score=0,
        max_chars=80,
    )

    assert _select(selector, oversized.question) == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"top_k": 0},
        {"top_k": 4},
        {"min_score": -0.1},
        {"min_score": 1.1},
        {"max_chars": 0},
    ],
)
def test_selector_validates_runtime_options(kwargs: dict[str, int | float]) -> None:
    with pytest.raises(ValueError):
        HybridFewShotSelector((), **kwargs)  # type: ignore[arg-type]
