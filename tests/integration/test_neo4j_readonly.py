"""显式启用时连接真实 Neo4j 的只读集成测试。"""

from __future__ import annotations

import os

import pytest

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.config import Settings
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator

pytestmark = pytest.mark.skipif(
    os.getenv("TEXT2CYPHER_RUN_INTEGRATION") != "1",
    reason="仅在显式设置 TEXT2CYPHER_RUN_INTEGRATION=1 时访问真实数据库",
)


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
    """验证 18 条黄金示例符合实时 Schema 并逐条通过 EXPLAIN。"""

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
    finally:
        provider.close()

    assert incompatible == []
    assert len(reports) == 18
    assert all(report.query_type == "r" for report in reports.values())
