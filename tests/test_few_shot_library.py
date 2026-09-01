from __future__ import annotations

import re
from collections import Counter

import pytest

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)
from text2cypher.domain.query_shapes import QueryShape, resolve_query_shape
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


def test_default_library_contains_expected_28_example_catalog() -> None:
    examples = JsonFewShotExampleLoader().load()

    assert len(examples) == 28
    assert Counter(example.category for example in examples) == {
        "simple_query": 3,
        "path_query": 16,
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
        "call-method-upstream-reachability",
        "call-method-direct-upstream",
        "call-method-direct-rest-egress",
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
        "service-rest-external-mapping",
        "mq-publish-call-point",
    }
    assert all(len(example.aliases) == 2 for example in examples)
    assert all(3 <= len(example.tags) <= 5 for example in examples)
    assert {
        example.id: example.query_shape
        for example in examples
        if example.query_shape is not QueryShape.GENERAL
    } == {
        "call-method-upstream-reachability": QueryShape.UPSTREAM_REACHABILITY,
        "call-method-direct-upstream": QueryShape.DIRECT_UPSTREAM,
        "call-method-full-upstream-chain": QueryShape.FULL_ENTRY_CHAIN,
        "call-method-full-downstream-chain": QueryShape.FULL_DOWNSTREAM_CHAIN,
        "call-ordered-method-path": QueryShape.ORDERED_METHOD_PATH,
        "call-method-direct-rest-egress": QueryShape.DIRECT_REST_EGRESS,
    }


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


def test_selected_example_metadata_uses_business_language_without_noise() -> None:
    examples_by_id = {
        example.id: example for example in JsonFewShotExampleLoader().load()
    }
    expected_metadata = {
        "call-method-upstream-reachability": (
            (
                "哪些上游方法可以调用到 "
                "travel.service.TravelServiceImpl.getTickets？"
                "请返回各自的调用距离。"
            ),
            (
                "查询目标方法的全部上游可达方法",
                "查看能够调用到指定方法的方法及距离",
            ),
            ("路径查询", "上游方法", "可达集合", "调用距离"),
        ),
        "impact-upstream-services": (
            (
                "哪些服务通过 REST 调用了 ts-order-other-service？"
                "请列出调用方法、下游 API，以及对应的目标 API 和入口方法。"
            ),
            (
                "查找 ts-order-other-service 的 REST 调用方",
                "查看目标服务的调用来源和入口 API",
            ),
            ("影响分析", "REST", "上游服务", "调用来源", "入口映射"),
        ),
        "api-external-mapping": (
            (
                "InsidePaymentServiceImpl.pay 调用了哪些下游 API？"
                "请同时给出目标服务、目标 API、入口方法和匹配类型。"
            ),
            (
                "查看方法的 REST 调用及其目标入口",
                "查询下游 API 对应的目标服务和入口方法",
            ),
            ("路径查询", "REST", "下游API", "跨服务映射", "入口方法"),
        ),
        "service-rest-external-mapping": (
            (
                "ts-admin-basic-info-service 调用了哪些下游 API？"
                "请列出调用方法以及对应的目标服务、目标 API 和入口方法。"
            ),
            (
                "查看服务发起的 REST 调用及目标入口",
                "查询服务的下游 API 和调用方法",
            ),
            ("路径查询", "REST", "服务调用", "下游API", "跨服务映射"),
        ),
        "simple-filter-upstream-apis": (
            "系统提供了哪些 HTTP API？请列出路径和请求方式。",
            (
                "查看系统入口接口清单",
                "列出所有上游 API 的路径和 HTTP 方法",
            ),
            ("简单查询", "API", "入口接口"),
        ),
        "call-interface-dispatch": (
            "FoodController.getAllFood 通过哪个接口分派到了哪个实现方法？",
            (
                "查看 Controller 方法的接口分派目标",
                "查询 Controller 调用对应的接口和实现方法",
            ),
            ("路径查询", "接口调用", "接口分派", "实现方法"),
        ),
    }

    for identifier, expected in expected_metadata.items():
        example = examples_by_id[identifier]
        assert (example.question, example.aliases, example.tags) == expected
        assert resolve_query_shape(example.question) is example.query_shape

    selected_metadata = "\n".join(
        " ".join((example.question, *example.aliases, *example.tags))
        for identifier, example in examples_by_id.items()
        if identifier in expected_metadata
    )
    for noise in ("逐行", "保持完整", "可用映射", "可选映射", "完整对应"):
        assert noise not in selected_metadata

    reachability = examples_by_id["call-method-upstream-reachability"]
    assert "调用链" not in " ".join(
        (reachability.question, *reachability.aliases, *reachability.tags)
    )
    assert "完整上游调用链" in examples_by_id[
        "call-method-full-upstream-chain"
    ].question

    api_list = examples_by_id["simple-filter-upstream-apis"]
    assert "属性过滤" not in api_list.tags
    assert "WHERE" not in api_list.cypher
    interface_dispatch = examples_by_id["call-interface-dispatch"]
    assert "直接调用" not in interface_dispatch.tags


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
    assert "MATCH (callerMethod:方法)-[:调用]->(calledMethod:方法)" in (
        direct_methods.cypher
    )
    assert "调用深度" not in direct_methods.cypher
    assert "调用" not in direct_methods.schema_requirements.relationship_properties
    assert "所属类名" in direct_methods.cypher
    upstream_reachability = examples_by_id["call-method-upstream-reachability"]
    assert upstream_reachability.cypher.count("MATCH") == 3
    assert (
        "targetMethod.所属类名 = 'travel.service.TravelServiceImpl'"
        in upstream_reachability.cypher
    )
    assert "所属类名" in upstream_reachability.schema_requirements.node_properties[
        "方法"
    ]
    assert "upstreamMethod <> targetMethod" in upstream_reachability.cypher
    assert "shortestPath(" in upstream_reachability.cypher
    assert "[:调用*1..]" in upstream_reachability.cypher
    assert "length(path) AS 调用距离" in upstream_reachability.cypher
    assert "调用深度" not in upstream_reachability.cypher
    assert (
        "调用"
        not in upstream_reachability.schema_requirements.relationship_properties
    )
    assert "UNION" not in upstream_reachability.cypher
    direct_upstream = examples_by_id["call-method-direct-upstream"]
    assert "MATCH (upstreamMethod:方法)-[:调用]->(targetMethod:方法)" in (
        direct_upstream.cypher
    )
    assert "调用深度" not in direct_upstream.cypher
    assert "调用" not in direct_upstream.schema_requirements.relationship_properties
    direct_rest = examples_by_id["call-method-direct-rest-egress"]
    assert direct_rest.cypher.startswith(
        "MATCH (anchorMethod:方法)-[:下游调用]->(downstreamApi:下游API)"
    )
    assert (
        "OPTIONAL MATCH (downstreamApi)-[:目标服务]->(targetService:微服务)"
        in direct_rest.cypher
    )
    assert "anchorMethod.全限定名 AS 目标方法" in direct_rest.cypher
    assert "外部调用" not in direct_rest.cypher

    downstream_services = examples_by_id["call-method-downstream-services"]
    assert downstream_services.cypher.startswith(
        "MATCH (anchorMethod:方法)-[:下游调用]->(downstreamApi:下游API)"
    )
    assert downstream_services.cypher.count("OPTIONAL MATCH") == 1
    assert (
        "OPTIONAL MATCH (downstreamApi)-[:目标服务]->(targetService:微服务)"
        in downstream_services.cypher
    )
    assert "可用目标服务" in " ".join(downstream_services.aliases)

    service_apis = examples_by_id["ownership-service-apis"]
    assert "(anchorClass:类 {简名:" in service_apis.cypher
    assert service_apis.cypher.count("MATCH") == 2
    assert "(service)<-[:归属于]-(serviceClass:类)" in service_apis.cypher
    assert "<-[:归属于]-(serviceMethod:方法)" in service_apis.cypher

    mq_publishers = examples_by_id["mq-publishers-for-queue"]
    assert "publish.路由键 = route.路由键" in mq_publishers.cypher
    mq_chain = examples_by_id["mq-full-message-chain"]
    assert "-[:消费自]->(consumerMethod:方法)" in mq_chain.cypher
    assert "publish.路由键 = route.路由键" in mq_chain.cypher

    upstream_chain = examples_by_id["call-method-full-upstream-chain"]
    assert "[:接口调用]" in upstream_chain.cypher
    assert "方法路径" in upstream_chain.cypher
    assert (
        upstream_chain.cypher.count(
            "anchorMethod.所属类名 ENDS WITH '.FoodServiceImpl'"
        )
        == 4
    )
    downstream_chain = examples_by_id["call-method-full-downstream-chain"]
    assert "OPTIONAL MATCH" in downstream_chain.cypher
    assert "目标上游API" in downstream_chain.cypher
    assert "MATCH methodPath = (anchorMethod:方法)" in downstream_chain.cypher
    assert (
        "MATCH (outboundMethod)-[:下游调用]->(downstreamApi:下游API)"
        in downstream_chain.cypher
    )
    method_path_clause = downstream_chain.cypher.split(
        "MATCH methodPath =",
        maxsplit=1,
    )[1].split("\nMATCH (outboundMethod)", maxsplit=1)[0]
    assert "下游调用" not in method_path_clause
    assert "relationships(methodPath)" in downstream_chain.cypher
    assert "nodes(methodPath)" in downstream_chain.cypher

    ordered_path = examples_by_id["call-ordered-method-path"]
    assert "链中下一节点*1..5" in ordered_path.cypher
    assert "路径签名" in ordered_path.cypher
    assert "位置索引" in ordered_path.cypher
    external_mapping = examples_by_id["api-external-mapping"]
    assert external_mapping.cypher.startswith(
        "MATCH (anchorMethod:方法)-[:下游调用]->(downstreamApi:下游API)"
    )
    assert "mapping.匹配类型 AS 匹配类型" in external_mapping.cypher
    assert external_mapping.cypher.count("OPTIONAL MATCH") == 3
    assert "完整映射" not in " ".join(
        (external_mapping.question, *external_mapping.aliases)
    )
    service_mapping = examples_by_id["service-rest-external-mapping"]
    assert "callerMethod.全限定名 AS 调用方法" in service_mapping.cypher
    assert service_mapping.cypher.count("OPTIONAL MATCH") == 3
    assert "跨服务映射" in service_mapping.tags
    assert "完整映射" not in " ".join(
        (service_mapping.question, *service_mapping.aliases)
    )
    upstream_rest = examples_by_id["impact-upstream-services"]
    assert (
        "MATCH (callerMethod)-[:下游调用]->(downstreamApi:下游API)"
        in upstream_rest.cypher
    )
    assert (
        "MATCH (downstreamApi)-[:目标服务]->(targetService:微服务"
        in upstream_rest.cypher
    )
    assert upstream_rest.cypher.count("OPTIONAL MATCH") == 2
    assert "callerService.服务名称 AS 调用服务" in upstream_rest.cypher
    assert "callerMethod.全限定名 AS 调用方法" in upstream_rest.cypher
    assert "targetEntryMethod.全限定名 AS 目标入口方法" in upstream_rest.cypher
    assert {"调用来源", "入口映射"} <= set(upstream_rest.tags)

    impacted_entries = examples_by_id["impact-entry-apis"]
    assert (
        "MATCH (entryMethod)-[:调用*1..]->(anchorMethod)"
        in impacted_entries.cypher
    )
    assert "调用深度" not in impacted_entries.cypher
    assert "调用" not in impacted_entries.schema_requirements.relationship_properties
    assert "直接上游" not in " ".join(
        (impacted_entries.question, *impacted_entries.aliases, *impacted_entries.tags)
    )
    call_point = examples_by_id["mq-publish-call-point"]
    assert "publish.调用点标识 = callPoint.语句文本" in call_point.cypher


def test_default_library_uses_readable_cypher_style_and_role_variables() -> None:
    examples = JsonFewShotExampleLoader().load()
    examples_by_id = {example.id: example for example in examples}

    for example in examples:
        lines = example.cypher.splitlines()

        assert len(lines) >= 3
        assert all(line == line.rstrip() for line in lines)
        assert re.search(
            r"(?m)^(?:MATCH|OPTIONAL MATCH|WHERE|RETURN|ORDER BY|UNION)\b",
            example.cypher,
        )
        assert re.search(r"\S[ \t]+WHERE\b", example.cypher) is None
        assert re.search(r"\S[ \t]+RETURN\b", example.cypher) is None
        assert re.search(r"\S[ \t]+ORDER BY\b", example.cypher) is None
        assert re.search(r"\S[ \t]+UNION\b", example.cypher) is None
        assert not re.search(
            r"\b(?:caller_|target_|entry_|source_)\w*",
            example.cypher,
        )
        if example.category != "aggregate_statistics":
            assert "RETURN DISTINCT" in example.cypher

    direct_calls = examples_by_id["call-method-downstream-methods"]
    assert "callerMethod" in direct_calls.cypher
    assert "calledMethod" in direct_calls.cypher

    downstream_chain = examples_by_id["call-method-full-downstream-chain"]
    assert "anchorMethod" in downstream_chain.cypher
    assert "targetEntryMethod" in downstream_chain.cypher
    assert "(target:方法)" not in downstream_chain.cypher
    assert downstream_chain.cypher.count("UNION") == 1

    upstream_chain = examples_by_id["call-method-full-upstream-chain"]
    assert upstream_chain.cypher.count("UNION") == 3
    assert "pathRelationship" in upstream_chain.cypher
    assert "[:调用*" not in upstream_chain.cypher
    assert "[:调用*" not in downstream_chain.cypher
    assert "\n  EXISTS {" in examples_by_id["impact-entry-apis"].cypher


def test_default_library_preserves_multiline_cypher_in_generation_prompt() -> None:
    example = next(
        item
        for item in JsonFewShotExampleLoader().load()
        if item.id == "impact-entry-apis"
    )

    prompt = DefaultPromptBuilder().build(
        GraphSchema(),
        "当前问题",
        (example,),
    )

    assert example.cypher in prompt.user
    assert "\n  EXISTS {\n    MATCH" in prompt.user
    assert "\nRETURN DISTINCT\n" in prompt.user


@pytest.mark.parametrize(
    "identifiers",
    [
        (
            "call-method-downstream-services",
            "call-method-full-downstream-chain",
            "api-external-mapping",
        ),
        (
            "call-method-full-upstream-chain",
            "call-interface-dispatch",
            "call-ordered-method-path",
        ),
        (
            "mq-publishers-for-queue",
            "mq-full-message-chain",
            "mq-publish-call-point",
        ),
        (
            "aggregate-service-api-counts",
            "aggregate-service-target-calls",
            "aggregate-interface-implementations",
        ),
        (
            "service-rest-external-mapping",
            "api-external-mapping",
            "call-service-outgoing-rest",
        ),
    ],
)
def test_common_example_bundles_stay_within_default_prompt_budget(
    identifiers: tuple[str, str, str],
) -> None:
    examples_by_id = {
        example.id: example for example in JsonFewShotExampleLoader().load()
    }

    size = sum(
        LLMFewShotRouter._prompt_block_size(examples_by_id[identifier], index)
        for index, identifier in enumerate(identifiers, start=1)
    )

    assert size <= 3500
