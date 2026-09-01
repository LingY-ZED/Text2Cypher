"""显式启用时连接真实 Neo4j 的只读集成测试。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import pytest

from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.config import Settings
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    CypherFailureSource,
    GraphSchema,
    LLMResponse,
    QueryResult,
    SubQueryResponse,
)
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator

pytestmark = pytest.mark.skipif(
    os.getenv("TEXT2CYPHER_RUN_INTEGRATION") != "1",
    reason="仅在显式设置 TEXT2CYPHER_RUN_INTEGRATION=1 时访问真实数据库",
)


GOLDEN_ROW_COUNTS = {
    "simple-list-services": 41,
    "simple-filter-upstream-apis": 255,
    "simple-api-contract": 1,
    "ownership-service-apis": 4,
    "path-class-methods": 4,
    "path-interface-implementation": 1,
    "path-queue-owner": 5,
    "call-method-downstream-methods": 4,
    "call-method-downstream-services": 3,
    "call-service-outgoing-rest": 15,
    "impact-upstream-services": 21,
    "impact-entry-apis": 1,
    "mq-between-services": 1,
    "mq-publishers-for-queue": 1,
    "mq-full-message-chain": 1,
    "aggregate-service-api-counts": 3,
    "aggregate-service-target-calls": 5,
    "aggregate-interface-implementations": 38,
    "call-method-upstream-reachability": 2,
    "call-method-direct-upstream": 1,
    "call-method-direct-rest-egress": 3,
    "call-method-full-upstream-chain": 1,
    "call-method-full-downstream-chain": 7,
    "call-interface-dispatch": 1,
    "call-ordered-method-path": 1,
    "api-external-mapping": 3,
    "service-rest-external-mapping": 20,
    "mq-publish-call-point": 1,
}

GOLDEN_COLUMNS = {
    "simple-list-services": ("服务名称",),
    "simple-filter-upstream-apis": ("接口路径", "请求方式"),
    "simple-api-contract": ("请求体类型", "响应类型"),
    "ownership-service-apis": ("接口路径", "请求方式"),
    "path-class-methods": ("方法名", "方法签名"),
    "path-interface-implementation": ("接口全限定名",),
    "path-queue-owner": ("队列名称", "服务名称"),
    "call-method-downstream-methods": ("被调用方法",),
    "call-method-downstream-services": ("下游服务", "接口路径"),
    "call-service-outgoing-rest": ("调用方法", "下游服务"),
    "impact-upstream-services": (
        "调用服务",
        "调用方法",
        "下游API",
        "目标上游API",
        "目标入口方法",
    ),
    "impact-entry-apis": ("入口方法", "入口接口", "请求方式"),
    "mq-between-services": ("交换机名称", "队列名称", "路由键"),
    "mq-publishers-for-queue": ("发布方法", "交换机名称"),
    "mq-full-message-chain": (
        "发布方法",
        "发送服务",
        "交换机名称",
        "队列名称",
        "消费方法",
        "接收服务",
    ),
    "aggregate-service-api-counts": ("请求方式", "API数量"),
    "aggregate-service-target-calls": ("下游服务", "调用关系数"),
    "aggregate-interface-implementations": ("服务名称", "实现类数量"),
    "call-method-upstream-reachability": ("目标方法", "上游方法", "调用距离"),
    "call-method-direct-upstream": ("目标方法", "上游方法"),
    "call-method-direct-rest-egress": ("目标方法", "下游API", "下游服务"),
    "call-method-full-upstream-chain": ("目标方法", "入口API", "方法路径"),
    "call-method-full-downstream-chain": (
        "目标方法",
        "方法路径",
        "下游API",
        "下游服务",
        "目标上游API",
        "目标入口方法",
    ),
    "call-interface-dispatch": ("调用方法", "实现方法", "经由接口"),
    "call-ordered-method-path": ("路径签名", "方法路径"),
    "api-external-mapping": (
        "下游API",
        "下游服务",
        "目标上游API",
        "目标入口方法",
        "匹配类型",
    ),
    "service-rest-external-mapping": (
        "调用方法",
        "下游API",
        "目标服务",
        "目标上游API",
        "目标入口方法",
    ),
    "mq-publish-call-point": ("发布方法", "源码行号", "路由键", "交换机名称"),
}

MISSING_REST_MAPPING_QUERY = """
MATCH (anchorMethod:方法)-[:下游调用]->(downstreamApi:下游API)
WHERE
  anchorMethod.所属类名 ENDS WITH '.PollThread' AND
  anchorMethod.方法名 = 'doPreserve'
OPTIONAL MATCH (downstreamApi)-[:目标服务]->(targetService:微服务)
OPTIONAL MATCH (downstreamApi)-[:外部调用]->(targetApi:上游API)
OPTIONAL MATCH (targetEntryMethod:方法)-[:服务于]->(targetApi)
RETURN DISTINCT
  anchorMethod.全限定名 AS 调用方法,
  downstreamApi.API路径 AS 下游API,
  targetService.服务名称 AS 目标服务,
  targetApi.API路径 AS 目标上游API,
  targetEntryMethod.全限定名 AS 目标入口方法
ORDER BY 调用方法, 下游API, 目标服务, 目标上游API, 目标入口方法
""".strip()


def test_real_neo4j_schema_and_readonly_query() -> None:
    """验证动态 Schema、EXPLAIN 和查询执行均不写入图数据。"""

    settings = Settings.from_environment()
    provider = Neo4jDriverProvider(settings)
    try:
        driver = provider.driver
        schema = Neo4jSchemaFetcher(
            driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
        ).fetch()
        report = Neo4jCypherValidator(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
        ).validate("RETURN 1 AS 数值")
        result = Neo4jCypherExecutor(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            settings.max_result_rows,
        ).execute("RETURN 1 AS 数值")
    finally:
        provider.close()

    assert schema.nodes or schema.relationships or schema.patterns
    node_names = {node.name for node in schema.nodes}
    relationship_names = {
        relationship.name for relationship in schema.relationships
    }
    assert {"API端点", "测试用例", "配置文件"}.isdisjoint(node_names)
    assert {"上游API", "下游API", "方法", "调用点"} <= node_names
    assert {
        "服务于",
        "下游调用",
        "接口调用",
        "链中下一节点",
        "发布至",
        "路由至",
        "消费自",
    } <= relationship_names
    assert report.query_type == "r"
    assert result.rows == ({"数值": 1},)


def test_real_neo4j_error_reaches_corrector_as_structured_context() -> None:
    """确定性 EXPLAIN 错误应带着真实诊断进入 Corrector，而不调用真实模型。"""

    class StaticPromptBuilder:
        def build(
            self,
            schema: GraphSchema,
            question: str,
            examples: tuple[object, ...] = (),
        ) -> ChatPrompt:
            del schema, question, examples
            return ChatPrompt(system="system", user="user")

    class InvalidCypherLLM:
        def generate(self, prompt: ChatPrompt) -> LLMResponse:
            del prompt
            return LLMResponse(content="RETURN (")

    class RecordingCorrector:
        def __init__(self) -> None:
            self.failure: CypherFailureContext | None = None

        def correct(
            self,
            base_prompt: ChatPrompt,
            failed_candidate: str,
            failure: CypherFailureContext,
        ) -> LLMResponse:
            del base_prompt, failed_candidate
            self.failure = failure
            return LLMResponse(content="RETURN 1 AS 数值")

    class StaticFormatter:
        def format(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> str:
            del question, sub_queries
            return "formatted"

    settings = Settings.from_environment()
    provider = Neo4jDriverProvider(settings)
    corrector = RecordingCorrector()
    try:
        pipeline = Text2CypherPipeline(
            schema_fetcher=Neo4jSchemaFetcher(
                provider.driver,
                settings.neo4j_database,
                settings.schema_timeout_seconds,
            ),
            prompt_builder=StaticPromptBuilder(),
            llm_client=InvalidCypherLLM(),
            cypher_parser=DefaultCypherParser(),
            cypher_validator=Neo4jCypherValidator(
                provider.driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
            ),
            cypher_executor=Neo4jCypherExecutor(
                provider.driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
                settings.max_result_rows,
            ),
            result_formatter=StaticFormatter(),
            cypher_corrector=corrector,
        )
        response = pipeline.run("验证实际错误传递")
    finally:
        provider.close()

    assert response.sub_queries[0].result.rows == ({"数值": 1},)
    assert corrector.failure is not None
    assert corrector.failure.source is CypherFailureSource.NEO4J
    assert corrector.failure.code is not None
    assert corrector.failure.code.startswith("Neo.ClientError.Statement.")
    assert corrector.failure.message


def test_real_few_shot_library_is_schema_compatible_and_readonly() -> None:
    """逐条验证 28 条黄金示例的 Schema、只读性和真实结果语义。"""

    settings = Settings.from_environment()
    provider = Neo4jDriverProvider(settings)
    try:
        driver = provider.driver
        schema = Neo4jSchemaFetcher(
            driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
        ).fetch()
        schema_graph = SchemaGraphBuilder().build(schema)
        compatibility_filter = FewShotSchemaCompatibilityFilter()
        validator = Neo4jCypherValidator(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
        )
        executor = Neo4jCypherExecutor(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            max(GOLDEN_ROW_COUNTS.values()) + 1,
        )
        examples = JsonFewShotExampleLoader().load()

        incompatible = [
            example.id
            for example in examples
            if not compatibility_filter.is_compatible(
                example,
                schema,
                schema_graph,
            )
        ]
        reports = {
            example.id: validator.validate(example.cypher)
            for example in examples
        }
        results = {
            example.id: executor.execute(example.cypher)
            for example in examples
        }
        missing_mapping_report = validator.validate(MISSING_REST_MAPPING_QUERY)
        missing_mapping_result = executor.execute(MISSING_REST_MAPPING_QUERY)
    finally:
        provider.close()

    assert incompatible == []
    assert len(reports) == 28
    assert all(report.query_type == "r" for report in reports.values())
    assert set(results) == set(GOLDEN_ROW_COUNTS)
    assert {
        example_id: len(result.rows)
        for example_id, result in results.items()
    } == GOLDEN_ROW_COUNTS
    assert {
        example_id: result.columns
        for example_id, result in results.items()
    } == GOLDEN_COLUMNS
    assert all(result.rows for result in results.values())
    assert not any(result.truncated for result in results.values())
    assert missing_mapping_report.query_type == "r"
    assert missing_mapping_result.columns == (
        "调用方法",
        "下游API",
        "目标服务",
        "目标上游API",
        "目标入口方法",
    )
    assert missing_mapping_result.rows == (
        {
            "调用方法": "waitorder.utils.PollThread.doPreserve",
            "下游API": "/api/v1/contactservice/preserve",
            "目标服务": None,
            "目标上游API": None,
            "目标入口方法": None,
        },
    )
    _assert_golden_result_semantics(results)


def _assert_golden_result_semantics(
    results: Mapping[str, QueryResult],
) -> None:
    assert results["simple-api-contract"].rows == (
        {
            "请求体类型": "edu.fudan.common.entity.RouteInfo",
            "响应类型": "org.springframework.http.HttpEntity",
        },
    )
    assert _values(results["path-class-methods"], "方法名") == {
        "createAndModifyPrice",
        "getPriceByWeightAndRegion",
        "getPriceConfig",
        "queryPriceInformation",
    }
    assert _values(
        results["path-interface-implementation"],
        "接口全限定名",
    ) == {"auth.service.TokenService"}
    assert {
        (row["队列名称"], row["服务名称"])
        for row in results["path-queue-owner"].rows
    } >= {
        ("email", "ts-notification-service"),
        ("food_delivery", "ts-delivery-service"),
    }
    assert _values(results["call-method-downstream-methods"], "被调用方法") == {
        "rebook.service.RebookServiceImpl.drawBackMoney",
        "rebook.service.RebookServiceImpl.getOrderByRebookInfo",
        "rebook.service.RebookServiceImpl.getTripAllDetailInformation",
        "rebook.service.RebookServiceImpl.updateOrder",
    }
    assert results["call-method-downstream-services"].rows == (
        {
            "下游服务": "ts-order-other-service",
            "接口路径": "/api/v1/orderOtherService/orderOther/{orderId}",
        },
        {
            "下游服务": "ts-order-service",
            "接口路径": "/api/v1/orderservice/order/",
        },
        {
            "下游服务": "ts-payment-service",
            "接口路径": "/api/v1/paymentservice/payment",
        },
    )
    assert _values(results["call-service-outgoing-rest"], "下游服务") == {
        "ts-inside-payment-service",
        "ts-order-other-service",
        "ts-order-service",
        "ts-route-service",
        "ts-seat-service",
        "ts-train-service",
        "ts-travel-service",
        "ts-travel2-service",
    }
    assert _values(results["impact-upstream-services"], "调用服务") == {
        "ts-admin-order-service",
        "ts-cancel-service",
        "ts-execute-service",
        "ts-inside-payment-service",
        "ts-preserve-other-service",
        "ts-rebook-service",
        "ts-seat-service",
        "ts-security-service",
    }
    impact_rows = {
        (
            row["调用服务"],
            row["调用方法"],
            row["下游API"],
            row["目标上游API"],
            row["目标入口方法"],
        )
        for row in results["impact-upstream-services"].rows
    }
    assert len(impact_rows) == 21
    assert {
        (
            "ts-admin-order-service",
            "adminorder.service.AdminOrderServiceImpl.addOrder",
            "/api/v1/orderOtherService/orderOther/admin",
            "/api/v1/orderOtherService/orderOther/admin",
            "other.controller.OrderOtherController.addcreateNewOrder",
        ),
        (
            "ts-inside-payment-service",
            "inside_payment.service.InsidePaymentServiceImpl.setOrderStatus",
            "/api/v1/orderOtherService/orderOther/status/{orderId}/",
            "/api/v1/orderOtherService/orderOther/status/{orderId}/{status}",
            "other.controller.OrderOtherController.modifyOrder",
        ),
        (
            "ts-security-service",
            (
                "security.service.SecurityServiceImpl."
                "getSecurityOrderOtherInfoFromOrder"
            ),
            (
                "/api/v1/orderOtherService/orderOther/security/"
                "{checkDate}/{accountId}"
            ),
            (
                "/api/v1/orderOtherService/orderOther/security/"
                "{checkDate}/{accountId}"
            ),
            "other.controller.OrderOtherController.securityInfoCheck",
        ),
    } <= impact_rows
    assert all(value is not None for row in impact_rows for value in row)
    assert results["impact-entry-apis"].rows == (
        {
            "入口方法": "consign.controller.ConsignController.updateConsign",
            "入口接口": "/api/v1/consignservice/consigns",
            "请求方式": "PUT",
        },
    )
    assert _values(results["mq-between-services"], "队列名称") == {
        "email"
    }
    assert _values(results["mq-between-services"], "交换机名称") == {""}
    assert _values(results["mq-between-services"], "路由键") == {"email"}
    assert _values(results["mq-publishers-for-queue"], "交换机名称") == {
        "(default)"
    }
    assert _values(results["mq-full-message-chain"], "交换机名称") == {
        "(default)"
    }
    assert _values(results["mq-full-message-chain"], "队列名称") == {
        "food_delivery",
    }
    assert _values(results["mq-full-message-chain"], "发送服务") == {
        "ts-food-service",
    }
    assert _values(results["mq-full-message-chain"], "接收服务") == {
        "ts-delivery-service",
    }
    assert results["call-method-full-upstream-chain"].rows == (
        {
            "目标方法": "foodsearch.service.FoodServiceImpl.getAllFood",
            "入口API": (
                "/api/v1/foodservice/foods/"
                "{date}/{startStation}/{endStation}/{tripId}"
            ),
            "方法路径": [
                "foodsearch.controller.FoodController.getAllFood",
                "foodsearch.service.FoodServiceImpl.getAllFood",
            ],
        },
    )
    assert results["call-method-upstream-reachability"].rows == (
        {
            "目标方法": "travel.service.TravelServiceImpl.getTickets",
            "上游方法": "travel.service.TravelServiceImpl.getTripAllDetailInfo",
            "调用距离": 1,
        },
        {
            "目标方法": "travel.service.TravelServiceImpl.getTickets",
            "上游方法": "travel.controller.TravelController.getTripAllDetailInfo",
            "调用距离": 2,
        },
    )
    assert results["call-method-direct-rest-egress"].rows == (
        {
            "目标方法": "inside_payment.service.InsidePaymentServiceImpl.pay",
            "下游API": "/api/v1/orderOtherService/orderOther/{orderId}",
            "下游服务": "ts-order-other-service",
        },
        {
            "目标方法": "inside_payment.service.InsidePaymentServiceImpl.pay",
            "下游API": "/api/v1/orderservice/order/",
            "下游服务": "ts-order-service",
        },
        {
            "目标方法": "inside_payment.service.InsidePaymentServiceImpl.pay",
            "下游API": "/api/v1/paymentservice/payment",
            "下游服务": "ts-payment-service",
        },
    )
    downstream_chain = results["call-method-full-downstream-chain"].rows
    assert all(
        row["目标方法"]
        == "inside_payment.service.InsidePaymentServiceImpl.pay"
        for row in downstream_chain
    )
    direct_rest_rows = {
        (
            row["下游API"],
            row["下游服务"],
            row["目标上游API"],
            row["目标入口方法"],
        )
        for row in downstream_chain
        if len(row["方法路径"]) == 1
    }
    assert direct_rest_rows == {
        (
            "/api/v1/orderOtherService/orderOther/{orderId}",
            "ts-order-other-service",
            "/api/v1/orderOtherService/orderOther/{orderId}",
            "other.controller.OrderOtherController.getOrderById",
        ),
        (
            "/api/v1/orderservice/order/",
            "ts-order-service",
            "/api/v1/orderservice/order/status/{orderId}/{status}",
            "order.controller.OrderController.modifyOrder",
        ),
        (
            "/api/v1/paymentservice/payment",
            "ts-payment-service",
            "/api/v1/paymentservice/payment",
            "com.trainticket.controller.PaymentController.pay",
        ),
    }
    indirect_rest_rows = {
        (
            row["下游API"],
            row["下游服务"],
            row["目标上游API"],
            row["目标入口方法"],
        )
        for row in downstream_chain
        if len(row["方法路径"]) == 2
    }
    assert indirect_rest_rows == {
        (
            "/api/v1/orderOtherService/orderOther/status//",
            "ts-order-other-service",
            "/api/v1/orderOtherService/orderOther",
            "other.controller.OrderOtherController.findAllOrder",
        ),
        (
            "/api/v1/orderOtherService/orderOther/status/{orderId}/",
            "ts-order-other-service",
            "/api/v1/orderOtherService/orderOther/status/{orderId}/{status}",
            "other.controller.OrderOtherController.modifyOrder",
        ),
        (
            "/api/v1/orderservice/order/status//",
            "ts-order-service",
            "/api/v1/orderservice/order",
            "order.controller.OrderController.findAllOrder",
        ),
        (
            "/api/v1/orderservice/order/status/{orderId}/",
            "ts-order-service",
            "/api/v1/orderservice/order/status/{orderId}/{status}",
            "order.controller.OrderController.modifyOrder",
        ),
    }
    assert all(
        row["方法路径"]
        == [
            "inside_payment.service.InsidePaymentServiceImpl.pay",
            "inside_payment.service.InsidePaymentServiceImpl.setOrderStatus",
        ]
        for row in downstream_chain
        if len(row["方法路径"]) == 2
    )
    assert {
        (row["请求方式"], row["API数量"])
        for row in results["aggregate-service-api-counts"].rows
    } == {("DELETE", 1), ("GET", 2), ("POST", 1)}
    assert {
        (row["下游服务"], row["调用关系数"])
        for row in results["aggregate-service-target-calls"].rows
    } == {
        ("ts-config-service", 4),
        ("ts-contacts-service", 4),
        ("ts-price-service", 4),
        ("ts-station-service", 4),
        ("ts-train-service", 4),
    }
    implementation_counts = results["aggregate-interface-implementations"]
    assert next(
        row["实现类数量"]
        for row in implementation_counts.rows
        if row["服务名称"] == "ts-auth-service"
    ) == 2
    assert max(row["实现类数量"] for row in implementation_counts.rows) == 2
    assert results["call-interface-dispatch"].rows == (
        {
            "调用方法": "foodsearch.controller.FoodController.getAllFood",
            "实现方法": "foodsearch.service.FoodServiceImpl.getAllFood",
            "经由接口": "foodsearch.service.FoodService",
        },
    )
    assert _values(results["ownership-service-apis"], "接口路径") == {
        "/api/v1/adminrouteservice/adminroute",
        "/api/v1/adminrouteservice/adminroute/{routeId}",
        "/api/v1/adminrouteservice/welcome",
    }
    assert results["call-ordered-method-path"].rows[0]["方法路径"] == [
        "waitorder.service.Impl.WaitListOrderServiceImpl.triggerThread",
        "waitorder.utils.PollThread.<init>",
        "waitorder.utils.PollThread.run",
        "waitorder.utils.PollThread.doPreserve",
    ]
    assert results["api-external-mapping"].rows == (
        {
            "下游API": "/api/v1/orderOtherService/orderOther/{orderId}",
            "下游服务": "ts-order-other-service",
            "目标上游API": "/api/v1/orderOtherService/orderOther/{orderId}",
            "目标入口方法": "other.controller.OrderOtherController.getOrderById",
            "匹配类型": "exact",
        },
        {
            "下游API": "/api/v1/orderservice/order/",
            "下游服务": "ts-order-service",
            "目标上游API": (
                "/api/v1/orderservice/order/status/{orderId}/{status}"
            ),
            "目标入口方法": "order.controller.OrderController.modifyOrder",
            "匹配类型": "fuzzy",
        },
        {
            "下游API": "/api/v1/paymentservice/payment",
            "下游服务": "ts-payment-service",
            "目标上游API": "/api/v1/paymentservice/payment",
            "目标入口方法": "com.trainticket.controller.PaymentController.pay",
            "匹配类型": "exact",
        },
    )
    assert all(
        row["调用方法"].startswith("adminbasic.service.AdminBasicInfoServiceImpl.")
        for row in results["service-rest-external-mapping"].rows
    )
    service_mapping_rows = {
        (
            row["调用方法"],
            row["下游API"],
            row["目标服务"],
            row["目标上游API"],
            row["目标入口方法"],
        )
        for row in results["service-rest-external-mapping"].rows
    }
    assert len(service_mapping_rows) == 20
    assert {
        (
            "adminbasic.service.AdminBasicInfoServiceImpl.addConfig",
            "/api/v1/configservice/configs",
            "ts-config-service",
            "api/v1/configservice/configs",
            "config.controller.ConfigController.createConfig",
        ),
        (
            "adminbasic.service.AdminBasicInfoServiceImpl.deleteStation",
            "/api/v1/stationservice/stations/{id}",
            "ts-station-service",
            "/api/v1/stationservice/stations/{stationsId}",
            "fdse.microservice.controller.StationController.delete",
        ),
    } <= service_mapping_rows
    assert all(
        value is not None for row in service_mapping_rows for value in row
    )
    assert results["mq-publish-call-point"].rows == (
        {
            "发布方法": "preserve.mq.RabbitSend.send",
            "源码行号": 0,
            "路由键": "email",
            "交换机名称": "(default)",
        },
    )


def _values(result: QueryResult, column: str) -> set[Any]:
    return {row[column] for row in result.rows}
