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
                "上游API",
                tuple(PropertySchema(name) for name in (
                    "API路径", "HTTP方法", "请求体类型", "响应类型",
                )),
            ),
            NodeSchema("下游API", (PropertySchema("API路径"),)),
            NodeSchema("微服务", (PropertySchema("服务名称"),)),
            NodeSchema(
                "方法",
                tuple(PropertySchema(name) for name in (
                    "所属类名", "方法名", "全限定名", "方法签名",
                )),
            ),
            NodeSchema(
                "类",
                tuple(PropertySchema(name) for name in ("全限定名", "简名")),
            ),
            NodeSchema("消息交换机", (PropertySchema("交换机名称"),)),
            NodeSchema("消息队列", (PropertySchema("队列名称"),)),
            NodeSchema(
                "调用点",
                tuple(PropertySchema(name) for name in (
                    "源码行号", "语句文本",
                )),
            ),
        ),
        relationships=(
            RelationshipSchema("归属于"),
            RelationshipSchema("接口实现"),
            RelationshipSchema("服务于"),
            RelationshipSchema("下游调用"),
            RelationshipSchema("目标服务"),
            RelationshipSchema("外部调用", (PropertySchema("匹配类型"),)),
            RelationshipSchema("接口调用", (PropertySchema("经由接口"),)),
            RelationshipSchema("调用", (PropertySchema("调用深度"),)),
            RelationshipSchema(
                "链中下一节点",
                (PropertySchema("路径签名"), PropertySchema("位置索引")),
            ),
            RelationshipSchema(
                "消息依赖",
                tuple(PropertySchema(name) for name in (
                    "交换机名称", "队列名称", "路由键",
                )),
            ),
            RelationshipSchema(
                "发布至",
                (PropertySchema("路由键"), PropertySchema("调用点标识")),
            ),
            RelationshipSchema("路由至", (PropertySchema("路由键"),)),
            RelationshipSchema("消费自"),
        ),
        patterns=(
            RelationshipPattern(("上游API",), "归属于", ("方法",)),
            RelationshipPattern(("下游API",), "外部调用", ("上游API",)),
            RelationshipPattern(("下游API",), "目标服务", ("微服务",)),
            RelationshipPattern(("微服务",), "消息依赖", ("微服务",)),
            RelationshipPattern(("方法",), "下游调用", ("下游API",)),
            RelationshipPattern(("方法",), "发布至", ("消息交换机",)),
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("方法",), "接口调用", ("方法",)),
            RelationshipPattern(("方法",), "服务于", ("上游API",)),
            RelationshipPattern(("方法",), "调用", ("方法",)),
            RelationshipPattern(("方法",), "链中下一节点", ("方法",)),
            RelationshipPattern(("消息交换机",), "路由至", ("消息队列",)),
            RelationshipPattern(("消息队列",), "归属于", ("微服务",)),
            RelationshipPattern(("消息队列",), "消费自", ("方法",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
            RelationshipPattern(("类",), "接口实现", ("类",)),
            RelationshipPattern(("调用点",), "归属于", ("方法",)),
        ),
    )


def test_default_library_contains_expected_24_example_catalog() -> None:
    examples = JsonFewShotExampleLoader().load()

    assert len(examples) == 24
    assert Counter(example.category for example in examples) == {
        "simple_query": 3,
        "path_query": 12,
        "impact_analysis": 2,
        "mq_query": 4,
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
        "call-interface-dispatch",
        "call-ordered-method-path",
        "api-external-mapping",
        "mq-publish-call-point",
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


def test_default_library_uses_only_current_schema_and_preserves_shapes() -> None:
    examples = JsonFewShotExampleLoader().load()
    examples_by_id = {example.id: example for example in examples}
    cypher_catalog = "\n".join(example.cypher for example in examples)

    for legacy_token in (
        "API端点",
        "API类型",
        ".接口路径",
        ".请求方式",
        "目标微服务",
        "消息流",
        "消息流类型",
        "调用类型",
        "[:调用*",
    ):
        assert legacy_token not in cypher_catalog
    assert (
        re.search(
            r"\bCALL\s+(?:\{|[A-Za-z_`])",
            cypher_catalog,
            re.IGNORECASE,
        )
        is None
    )
    assert ";" not in cypher_catalog
    assert "//" not in cypher_catalog
    assert "/*" not in cypher_catalog

    direct_methods = examples_by_id["call-method-downstream-methods"]
    assert "调用深度 IN [0, 1]" in direct_methods.cypher
    assert "所属类名" in direct_methods.cypher

    service_apis = examples_by_id["ownership-service-apis"]
    assert "(anchor:类 {简名:" in service_apis.cypher
    assert service_apis.cypher.count("MATCH") == 2
    assert "(service)<-[:归属于]-(:类)<-[:归属于]-(method:方法)" in (
        service_apis.cypher
    )

    mq_publishers = examples_by_id["mq-publishers-for-queue"]
    assert "publish.路由键 = route.路由键" in mq_publishers.cypher
    mq_chain = examples_by_id["mq-full-message-chain"]
    assert "-[:消费自]->(consumer:方法)" in mq_chain.cypher
    assert "publish.路由键 = route.路由键" in mq_chain.cypher

    upstream_chain = examples_by_id["call-method-full-upstream-chain"]
    assert "[:接口调用]" in upstream_chain.cypher
    assert "方法路径" in upstream_chain.cypher
    downstream_chain = examples_by_id["call-method-full-downstream-chain"]
    assert "OPTIONAL MATCH" in downstream_chain.cypher
    assert "目标上游API" in downstream_chain.cypher

    ordered_path = examples_by_id["call-ordered-method-path"]
    assert "链中下一节点*1..5" in ordered_path.cypher
    assert "路径签名" in ordered_path.cypher
    assert "位置索引" in ordered_path.cypher
    external_mapping = examples_by_id["api-external-mapping"]
    assert "mapping.匹配类型 AS 匹配类型" in external_mapping.cypher
    assert external_mapping.cypher.count("OPTIONAL MATCH") == 3
    upstream_rest = examples_by_id["impact-upstream-services"]
    assert "caller_service.服务名称 AS 调用服务" in upstream_rest.cypher
    assert "caller_method.全限定名 AS 调用方法" in upstream_rest.cypher
    assert "entry.全限定名 AS 目标入口方法" in upstream_rest.cypher
    call_point = examples_by_id["mq-publish-call-point"]
    assert "publish.调用点标识 = point.语句文本" in call_point.cypher
