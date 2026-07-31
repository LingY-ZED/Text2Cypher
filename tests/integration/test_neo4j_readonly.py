"""显式启用时连接真实 Neo4j 的只读集成测试。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import pytest

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.config import Settings
from text2cypher.domain.models import QueryResult
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
    "simple-locate-method": 1,
    "ownership-service-apis": 12,
    "ownership-api-service": 1,
    "call-method-downstream-methods": 1,
    "call-method-downstream-services": 3,
    "call-service-outgoing-rest": 3,
    "impact-upstream-services": 1,
    "impact-upstream-methods": 1,
    "impact-entry-apis": 1,
    "mq-between-services": 1,
    "mq-publishers-for-queue": 4,
    "mq-full-message-chain": 4,
    "aggregate-rest-service-pairs": 49,
    "aggregate-method-downstream-calls": 3,
    "compound-method-impact": 1,
    "compound-rest-mq-dependencies": 1,
}

GOLDEN_COLUMNS = {
    "simple-list-services": ("服务名称",),
    "simple-filter-upstream-apis": ("接口路径", "请求方式"),
    "simple-locate-method": ("全限定名", "方法签名"),
    "ownership-service-apis": ("接口路径", "请求方式", "API类型"),
    "ownership-api-service": ("服务名称",),
    "call-method-downstream-methods": ("被调用方法", "调用类型"),
    "call-method-downstream-services": ("下游服务", "接口路径"),
    "call-service-outgoing-rest": ("调用方法", "下游服务", "接口路径"),
    "impact-upstream-services": ("上游服务",),
    "impact-upstream-methods": ("上游方法", "上游服务"),
    "impact-entry-apis": ("入口接口", "请求方式"),
    "mq-between-services": ("交换机名称", "队列名称", "路由键"),
    "mq-publishers-for-queue": ("发布方法", "交换机名称", "队列名称"),
    "mq-full-message-chain": (
        "发布方法",
        "交换机名称",
        "队列名称",
        "消费方法",
    ),
    "aggregate-rest-service-pairs": (
        "调用方服务",
        "被调用服务",
        "调用关系数",
    ),
    "aggregate-method-downstream-calls": ("下游服务", "调用关系数"),
    "compound-method-impact": ("上游调用方", "下游服务"),
    "compound-rest-mq-dependencies": ("REST下游服务", "MQ下游服务"),
}

FOOD_METHOD = "foodsearch.service.FoodServiceImpl.getAllFood"
FOOD_CONTROLLER_METHOD = "foodsearch.controller.FoodController.getAllFood"
FOOD_ENTRY_PATH = (
    "/api/v1/foodservice/foods/{date}/{startStation}/{endStation}/{tripId}"
)
FOOD_DOWNSTREAM_SERVICES = {
    "ts-station-food-service",
    "ts-train-food-service",
    "ts-travel-service",
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
    assert _values(results["simple-locate-method"], "全限定名") == {
        FOOD_METHOD
    }
    assert _values(results["ownership-api-service"], "服务名称") == {
        "ts-food-service"
    }
    assert _values(results["call-method-downstream-methods"], "被调用方法") == {
        FOOD_METHOD
    }

    for example_id in (
        "call-method-downstream-services",
        "call-service-outgoing-rest",
        "aggregate-method-downstream-calls",
    ):
        assert _values(results[example_id], "下游服务") == FOOD_DOWNSTREAM_SERVICES

    assert _values(results["impact-upstream-services"], "上游服务") == {
        "ts-food-service"
    }
    assert _values(results["impact-upstream-methods"], "上游方法") == {
        FOOD_METHOD
    }
    assert _values(results["impact-upstream-methods"], "上游服务") == {
        "ts-food-service"
    }
    assert _values(results["impact-entry-apis"], "入口接口") == {
        FOOD_ENTRY_PATH
    }

    assert _values(results["mq-between-services"], "队列名称") == {
        "food_delivery"
    }
    assert _values(results["mq-between-services"], "交换机名称") == {""}
    for example_id in ("mq-publishers-for-queue", "mq-full-message-chain"):
        assert _values(results[example_id], "队列名称") == {"food_delivery"}
        assert _values(results[example_id], "交换机名称") == {"(default)"}

    rest_pairs = results["aggregate-rest-service-pairs"].rows
    assert all(row["调用方服务"] != row["被调用服务"] for row in rest_pairs)

    compound_row = results["compound-method-impact"].rows[0]
    assert set(compound_row["上游调用方"]) == {FOOD_CONTROLLER_METHOD}
    assert set(compound_row["下游服务"]) == FOOD_DOWNSTREAM_SERVICES

    dependency_row = results["compound-rest-mq-dependencies"].rows[0]
    assert set(dependency_row["REST下游服务"]) == FOOD_DOWNSTREAM_SERVICES
    assert set(dependency_row["MQ下游服务"]) == {"ts-delivery-service"}


def _values(result: QueryResult, column: str) -> set[Any]:
    return {row[column] for row in result.rows}
