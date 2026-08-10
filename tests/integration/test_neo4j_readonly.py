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
    "simple-filter-upstream-apis": 203,
    "simple-api-contract": 1,
    "ownership-service-apis": 6,
    "path-class-methods": 4,
    "path-interface-implementation": 1,
    "path-queue-owner": 5,
    "call-method-downstream-methods": 4,
    "call-method-downstream-services": 3,
    "call-service-outgoing-rest": 12,
    "impact-upstream-services": 6,
    "impact-entry-apis": 1,
    "mq-between-services": 1,
    "mq-publishers-for-queue": 4,
    "mq-full-message-chain": 2,
    "aggregate-service-api-counts": 3,
    "aggregate-service-target-calls": 5,
    "aggregate-interface-implementations": 38,
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
    "impact-upstream-services": ("上游服务",),
    "impact-entry-apis": ("上游方法", "入口接口", "请求方式"),
    "mq-between-services": ("交换机名称", "队列名称", "路由键"),
    "mq-publishers-for-queue": ("发布方法", "交换机名称"),
    "mq-full-message-chain": ("交换机名称", "队列名称", "消费方法"),
    "aggregate-service-api-counts": ("请求方式", "API数量"),
    "aggregate-service-target-calls": ("下游服务", "调用关系数"),
    "aggregate-interface-implementations": ("服务名称", "接口实现类数"),
}


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
    """逐条验证 18 条黄金示例的 Schema、只读性和真实结果语义。"""

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
            max(GOLDEN_ROW_COUNTS.values()),
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
    finally:
        provider.close()

    assert incompatible == []
    assert len(reports) == 18
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
    assert _values(
        results["call-method-downstream-services"],
        "下游服务",
    ) == {
        "ts-order-other-service",
        "ts-order-service",
        "ts-payment-service",
    }
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
    assert _values(results["impact-upstream-services"], "上游服务") == {
        "ts-admin-order-service",
        "ts-cancel-service",
        "ts-execute-service",
        "ts-inside-payment-service",
        "ts-rebook-service",
        "ts-security-service",
    }
    assert _values(results["impact-entry-apis"], "入口接口") == {
        "/api/v1/consignservice/consigns"
    }
    assert _values(results["impact-entry-apis"], "上游方法") == {
        "consign.controller.ConsignController.updateConsign"
    }
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
        "email",
        "food_delivery",
    }
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
        row["接口实现类数"]
        for row in implementation_counts.rows
        if row["服务名称"] == "ts-auth-service"
    ) == 2
    assert max(row["接口实现类数"] for row in implementation_counts.rows) == 2


def _values(result: QueryResult, column: str) -> set[Any]:
    return {row[column] for row in result.rows}
