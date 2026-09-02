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


def test_default_library_contains_compact_reusable_example_catalog() -> None:
    examples = JsonFewShotExampleLoader().load()

    assert len(examples) == 25
    assert Counter(example.category for example in examples) == {
        "simple_query": 3,
        "path_query": 15,
        "mq_query": 4,
        "aggregate_statistics": 3,
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
        "call-method-downstream-methods": QueryShape.DIRECT_DOWNSTREAM_METHOD,
        "impact-entry-apis": QueryShape.REACHABLE_ENTRY_API,
        "call-ordered-method-path": QueryShape.ORDERED_METHOD_PATH,
        "api-external-mapping": QueryShape.DIRECT_REST_EGRESS,
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


def test_default_catalog_metadata_has_one_unambiguous_structure_per_example(
) -> None:
    examples = JsonFewShotExampleLoader().load()
    examples_by_id = {example.id: example for example in examples}
    expected_questions = {
        "simple-list-services": "系统中有哪些微服务？",
        "simple-filter-upstream-apis": (
            "系统提供了哪些 HTTP API？请列出路径和请求方式。"
        ),
        "simple-api-contract": (
            "POST /api/v1/adminrouteservice/adminroute 使用什么请求体和响应类型？"
        ),
        "ownership-service-apis": (
            "AdminRouteServiceImpl 所属微服务提供哪些公开 API？"
            "请返回 API 路径和 HTTP 方法。"
        ),
        "path-class-methods": (
            "consignprice.service.ConsignPriceServiceImpl 定义了哪些方法及签名？"
        ),
        "path-interface-implementation": (
            "auth.service.impl.TokenServiceImpl 实现了哪个接口？"
        ),
        "path-queue-owner": "列出所有消息队列及其所属微服务。",
        "call-method-downstream-methods": (
            "RebookServiceImpl.rebook 直接调用了哪些方法？"
        ),
        "call-service-outgoing-rest": (
            "查询 ts-rebook-service 直接 REST 调用的下游服务，"
            "并返回调用方法和下游服务。"
        ),
        "impact-upstream-services": (
            "查询 ts-order-other-service 的 REST 调用方，"
            "并返回调用服务、调用方法、下游 API、目标 API 和入口方法。"
        ),
        "impact-entry-apis": (
            "哪些入口 API 可以到达 ConsignServiceImpl.updateConsignRecord？"
            "请返回入口方法、API 路径和 HTTP 方法。"
        ),
        "mq-between-services": (
            "查询 ts-preserve-service 到 ts-notification-service 的 MQ 通道，"
            "并返回交换机、队列和路由键。"
        ),
        "mq-publishers-for-queue": (
            "查询经交换机向 food_delivery 队列发送消息的方法，"
            "并返回发布方法和交换机。"
        ),
        "mq-full-message-chain": (
            "展开 food_delivery 的完整消息链，返回发布方法、发送服务、"
            "交换机、队列、消费方法和接收服务。"
        ),
        "aggregate-service-api-counts": (
            "按请求方式统计 ts-admin-route-service 对外 API 数量。"
        ),
        "aggregate-service-target-calls": (
            "按下游服务统计 ts-admin-basic-info-service 的 REST 调用关系数。"
        ),
        "aggregate-interface-implementations": "按微服务统计接口实现类数量。",
        "call-method-upstream-reachability": (
            "哪些上游方法可以调用到 travel.service.TravelServiceImpl.getTickets？"
            "请返回各自的调用距离。"
        ),
        "call-method-direct-upstream": (
            "哪些方法直接调用 FoodServiceImpl.getAllFood？"
        ),
        "call-interface-dispatch": (
            "FoodController.getAllFood 通过哪个接口分派到了哪个实现方法？"
        ),
        "call-ordered-method-path": (
            "查询 WaitListOrderServiceImpl.triggerThread 到 "
            "PollThread.doPreserve 的有序方法路径，并返回路径签名和方法顺序。"
        ),
        "api-external-mapping": (
            "查询 InsidePaymentServiceImpl.pay 直接调用的下游 API，"
            "并返回目标服务、目标 API、入口方法和匹配类型。"
        ),
        "call-method-direct-rest-egress": (
            "查询 InsidePaymentServiceImpl.pay 直接调用的下游 API 和目标服务。"
        ),
        "service-rest-external-mapping": (
            "查询 ts-admin-basic-info-service 的 REST 出口，"
            "并返回调用方法、下游 API、目标服务、目标 API 和入口方法。"
        ),
        "mq-publish-call-point": (
            "preserve.mq.RabbitSend.send 在哪个调用点使用什么路由键"
            "发布到哪个交换机？"
        ),
    }

    assert {
        identifier: example.question
        for identifier, example in examples_by_id.items()
    } == expected_questions
    assert {
        example.id for example in examples if example.category == "simple_query"
    } == {
        "simple-list-services",
        "simple-filter-upstream-apis",
        "simple-api-contract",
    }
    assert {
        example.id for example in examples if example.category == "mq_query"
    } == {
        "mq-between-services",
        "mq-publishers-for-queue",
        "mq-full-message-chain",
        "mq-publish-call-point",
    }
    assert {
        example.id
        for example in examples
        if example.category == "aggregate_statistics"
    } == {
        "aggregate-service-api-counts",
        "aggregate-service-target-calls",
        "aggregate-interface-implementations",
    }
    assert all(
        resolve_query_shape(example.question) is example.query_shape
        for example in examples
    )
    assert all(
        resolve_query_shape(alias) in {QueryShape.GENERAL, example.query_shape}
        for example in examples
        for alias in example.aliases
    )


def test_default_catalog_has_no_literal_only_cypher_duplicates() -> None:
    examples = JsonFewShotExampleLoader().load()

    def fingerprint(cypher: str) -> str:
        without_literals = re.sub(r"'(?:''|[^'])*'", "'<value>'", cypher)
        return re.sub(r"\s+", " ", without_literals).strip()

    fingerprints = [fingerprint(example.cypher) for example in examples]

    assert len(fingerprints) == len(set(fingerprints))


def test_schema_requirements_do_not_retain_unused_schema_tokens() -> None:
    for example in JsonFewShotExampleLoader().load():
        requirements = example.schema_requirements
        for label in requirements.node_labels:
            assert re.search(rf"\([^)]*:{re.escape(label)}(?:[\s{{)])", example.cypher)
        for relationship_type in requirements.relationship_types:
            assert re.search(
                rf"\[[^]]*:{re.escape(relationship_type)}(?:[\s*{{\]])",
                example.cypher,
            )
        for properties in requirements.node_properties.values():
            for property_name in properties:
                assert property_name in example.cypher
        for properties in requirements.relationship_properties.values():
            for property_name in properties:
                assert property_name in example.cypher


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
                "查询 ts-order-other-service 的 REST 调用方，"
                "并返回调用服务、调用方法、下游 API、目标 API 和入口方法。"
            ),
            (
                "查找 ts-order-other-service 的 REST 调用方",
                "查看目标服务的调用来源和入口 API",
            ),
            ("路径查询", "REST", "上游服务", "调用来源", "入口映射"),
        ),
        "api-external-mapping": (
            (
                "查询 InsidePaymentServiceImpl.pay 直接调用的下游 API，"
                "并返回目标服务、目标 API、入口方法和匹配类型。"
            ),
            (
                "查看方法的 REST 调用及其目标入口",
                "查询下游 API 对应的目标服务和入口方法",
            ),
            ("路径查询", "REST", "下游API", "跨服务映射", "入口方法"),
        ),
        "service-rest-external-mapping": (
            (
                "查询 ts-admin-basic-info-service 的 REST 出口，"
                "并返回调用方法、下游 API、目标服务、目标 API 和入口方法。"
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
    assert "call-method-full-upstream-chain" not in examples_by_id
    assert "call-method-full-downstream-chain" not in examples_by_id

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
    assert upstream_reachability.cypher.count("MATCH") == 1
    assert upstream_reachability.cypher.startswith("MATCH path = shortestPath(")
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
    assert "anchorMethod.全限定名 AS 目标方法" not in direct_rest.cypher
    assert "downstreamApi.API路径 AS 下游API" in direct_rest.cypher
    assert "targetService.服务名称 AS 下游服务" in direct_rest.cypher
    assert "全限定名" not in direct_rest.schema_requirements.node_properties[
        "方法"
    ]
    assert "外部调用" not in direct_rest.cypher
    assert "call-method-downstream-services" not in examples_by_id

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

    interface_counts = examples_by_id["aggregate-interface-implementations"]
    assert interface_counts.cypher.count("MATCH") == 1

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
    assert call_point.cypher.count("MATCH") == 1
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
            "call-method-downstream-methods",
            "call-method-direct-upstream",
            "call-method-upstream-reachability",
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
