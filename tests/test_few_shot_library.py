from __future__ import annotations

import re
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
                    PropertySchema("请求体类型"),
                    PropertySchema("响应类型"),
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
            NodeSchema(
                "类",
                (PropertySchema("全限定名"), PropertySchema("简名")),
            ),
            NodeSchema("消息交换机", (PropertySchema("交换机名称"),)),
            NodeSchema("消息队列", (PropertySchema("队列名称"),)),
        ),
        relationships=(
            RelationshipSchema("归属于"),
            RelationshipSchema("接口实现"),
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
            RelationshipPattern(("消息队列",), "归属于", ("微服务",)),
            RelationshipPattern(("消息队列",), "消息流", ("方法",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
            RelationshipPattern(("类",), "接口实现", ("类",)),
        ),
    )


def test_default_library_contains_expected_20_example_catalog() -> None:
    examples = JsonFewShotExampleLoader().load()

    assert len(examples) == 20
    assert Counter(example.category for example in examples) == {
        "simple_query": 3,
        "path_query": 9,
        "impact_analysis": 2,
        "mq_query": 3,
        "aggregate_statistics": 3,
    }
    assert {example.id for example in examples} == {
        "simple-list-services",
        "simple-filter-upstream-apis",
        "simple-api-contract",
        "ownership-service-apis",
        "path-class-methods",
        "path-interface-implementation",
        "path-queue-owner",
        "call-method-downstream-methods",
        "call-method-downstream-services",
        "call-method-full-upstream-chain",
        "call-method-full-downstream-chain",
        "call-service-outgoing-rest",
        "impact-upstream-services",
        "impact-entry-apis",
        "mq-between-services",
        "mq-publishers-for-queue",
        "mq-full-message-chain",
        "aggregate-service-api-counts",
        "aggregate-service-target-calls",
        "aggregate-interface-implementations",
    }
    assert all(len(example.aliases) == 2 for example in examples)
    assert all(3 <= len(example.tags) <= 5 for example in examples)


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


def test_default_library_contains_short_focused_cypher() -> None:
    examples = JsonFewShotExampleLoader().load()
    catalog = "\n".join(
        f"{example.question}\n{example.cypher}" for example in examples
    )
    cypher_catalog = "\n".join(example.cypher for example in examples)
    lengths = [len(example.cypher) for example in examples]

    assert "'/api/v1/foods'" not in catalog
    assert "food.queue" not in catalog
    assert "food-exchange" not in catalog
    assert "[call_rel:调用*1..5]" not in catalog
    assert "call_rel.调用类型 AS 调用类型" not in catalog
    assert "compound-method-impact" not in catalog
    assert "compound-rest-mq-dependencies" not in catalog
    assert "WHERE" not in cypher_catalog.upper()
    assert "UNION" not in cypher_catalog.upper()
    assert re.search(r"\bCALL\b", cypher_catalog, re.IGNORECASE) is None
    assert "COLLECT(" not in cypher_catalog.upper()
    assert ";" not in cypher_catalog
    assert "//" not in cypher_catalog
    assert "/*" not in cypher_catalog
    assert max(lengths) <= 480
    assert sum(lengths) / len(lengths) <= 200
    assert sum("[:调用*0..5]" in example.cypher for example in examples) == 3
    assert "{简名: 'InsidePaymentServiceImpl'}" in catalog
    assert "{简名: 'ConsignServiceImpl'}" in catalog
    assert "food_delivery" in catalog
    assert "展开 food_delivery 的完整消息链" in catalog

    examples_by_id = {example.id: example for example in examples}
    mq_chain = examples_by_id["mq-full-message-chain"]
    assert (
        "(q:消息队列 {队列名称:'food_delivery'})"
        "-[:消息流 {消息流类型:'消费'}]->(c:方法)"
        in mq_chain.cypher
    )
    assert "(p:方法" in mq_chain.cypher
    assert "(q:消息队列 {队列名称:'food_delivery'})" in mq_chain.cypher
    assert "(s:微服务)" in mq_chain.cypher
    assert "(r:微服务)" in mq_chain.cypher
    publishers = examples_by_id["mq-publishers-for-queue"]
    assert "仅发布方向" in publishers.tags
    downstream = examples_by_id["call-method-downstream-services"]
    assert "直接下游" in downstream.tags
    assert "所属类名" not in downstream.cypher
    assert "(caller)-[:调用" in downstream.cypher
    direct_methods = examples_by_id["call-method-downstream-methods"]
    assert "所属类名" not in direct_methods.cypher
    assert "(caller)-[:调用]->(callee:方法)" in direct_methods.cypher
    upstream_chain = examples_by_id["call-method-full-upstream-chain"]
    assert "nodes(path)" in upstream_chain.cypher
    assert "入口API" in upstream_chain.cypher
    assert "]->(target:方法" in upstream_chain.cypher
    assert "), (target)-[:归属于]" in upstream_chain.cypher
    downstream_chain = examples_by_id["call-method-full-downstream-chain"]
    assert "OPTIONAL MATCH" in downstream_chain.cypher
    assert "目标上游API" in downstream_chain.cypher
