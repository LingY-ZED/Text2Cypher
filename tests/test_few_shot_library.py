from __future__ import annotations

from collections import Counter

import pytest

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
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


def test_default_library_does_not_contain_known_invalid_cypher() -> None:
    examples = JsonFewShotExampleLoader().load()
    catalog = "\n".join(
        f"{example.question}\n{example.cypher}" for example in examples
    )

    assert "'/api/v1/foods'" not in catalog
    assert "food.queue" not in catalog
    assert "food-exchange" not in catalog
    assert "[call_rel:调用*1..5]" not in catalog
    assert "call_rel.调用类型 AS 调用类型" not in catalog
    assert (
        "'/api/v1/foodservice/foods/{date}/{startStation}/{endStation}/{tripId}'"
        in catalog
    )
    assert "food_delivery" in catalog
    assert "'(default)'" in catalog
