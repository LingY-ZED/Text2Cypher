from __future__ import annotations

from collections import Counter

import pytest

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.components.few_shot_selector import HybridFewShotSelector
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader


@pytest.fixture(scope="module")
def code_knowledge_schema() -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema(
                "API端点",
                (
                    PropertySchema("API类型"),
                    PropertySchema("接口路径"),
                    PropertySchema("请求方式"),
                    PropertySchema("目标微服务"),
                ),
            ),
            NodeSchema("微服务", (PropertySchema("服务名称"),)),
            NodeSchema(
                "方法",
                (
                    PropertySchema("所属类名"),
                    PropertySchema("方法名"),
                    PropertySchema("全限定名"),
                    PropertySchema("方法签名"),
                ),
            ),
            NodeSchema("类"),
            NodeSchema("消息交换机", (PropertySchema("交换机名称"),)),
            NodeSchema("消息队列", (PropertySchema("队列名称"),)),
        ),
        relationships=(
            RelationshipSchema("归属于"),
            RelationshipSchema("调用", (PropertySchema("调用类型"),)),
            RelationshipSchema(
                "消息流",
                (
                    PropertySchema("消息流类型"),
                    PropertySchema("交换机名称"),
                    PropertySchema("队列名称"),
                    PropertySchema("路由键"),
                ),
            ),
        ),
        patterns=(
            RelationshipPattern(("API端点",), "归属于", ("方法",)),
            RelationshipPattern(("API端点",), "调用", ("API端点",)),
            RelationshipPattern(("微服务",), "消息流", ("微服务",)),
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("方法",), "消息流", ("消息交换机",)),
            RelationshipPattern(("方法",), "调用", ("API端点",)),
            RelationshipPattern(("方法",), "调用", ("方法",)),
            RelationshipPattern(("消息交换机",), "消息流", ("消息队列",)),
            RelationshipPattern(("消息队列",), "消息流", ("方法",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        ),
    )


def test_default_library_contains_expected_18_example_catalog() -> None:
    examples = JsonFewShotExampleLoader().load()

    assert len(examples) == 18
    assert Counter(example.category for example in examples) == {
        "simple_filter": 3,
        "ownership_api": 2,
        "call_downstream": 3,
        "reverse_impact": 3,
        "mq": 3,
        "aggregate": 2,
        "compound": 2,
    }
    assert all(example.aliases for example in examples)


def test_all_default_examples_match_frozen_code_knowledge_schema(
    code_knowledge_schema: GraphSchema,
) -> None:
    examples = JsonFewShotExampleLoader().load()
    graph = SchemaGraphBuilder().build(code_knowledge_schema)
    compatibility_filter = FewShotSchemaCompatibilityFilter()

    incompatible = [
        example.id
        for example in examples
        if not compatibility_filter.is_compatible(
            example,
            code_knowledge_schema,
            graph,
        )
    ]

    assert incompatible == []


@pytest.mark.parametrize(
    ("question", "expected_id"),
    [
        ("ts-food-service 有哪些 API 端点？", "ownership-service-apis"),
        (
            "getAllFood 方法调用了哪些下游服务？",
            "call-method-downstream-services",
        ),
        (
            "哪些服务调用了 ts-train-food-service？",
            "impact-upstream-services",
        ),
        (
            "ts-food-service 和 ts-delivery-service 之间有哪些 MQ 通信？",
            "mq-between-services",
        ),
        (
            "列出所有微服务之间的 REST 调用关系统计",
            "aggregate-rest-service-pairs",
        ),
        (
            "修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务？",
            "compound-method-impact",
        ),
    ],
)
def test_default_library_selects_expected_golden_example(
    code_knowledge_schema: GraphSchema,
    question: str,
    expected_id: str,
) -> None:
    graph = SchemaGraphBuilder().build(code_knowledge_schema)
    selector = HybridFewShotSelector(
        JsonFewShotExampleLoader().load(),
        top_k=1,
    )

    selected = selector.select(question, code_knowledge_schema, graph)

    assert selected[0].id == expected_id
