"""显式启用时连接真实 Neo4j 的只读集成测试。"""

from __future__ import annotations

import os

import pytest

from text2cypher.config import Settings
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
