"""Explicit real-Neo4j validation of the frozen evaluation Oracles."""

from __future__ import annotations

import json
import os

import pytest

from evaluation.dataset import load_cases
from text2cypher.components.retry import RetryPolicy
from text2cypher.config import Settings
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator

pytestmark = pytest.mark.skipif(
    os.getenv("TEXT2CYPHER_RUN_INTEGRATION") != "1",
    reason="仅在显式设置 TEXT2CYPHER_RUN_INTEGRATION=1 时访问真实数据库",
)


def test_all_evaluation_oracles_match_the_frozen_snapshot() -> None:
    settings = Settings.from_environment()
    retry = RetryPolicy()
    provider = Neo4jDriverProvider(settings, retry_policy=retry)
    try:
        validator = Neo4jCypherValidator(
            provider.driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            retry_policy=retry,
        )
        executor = Neo4jCypherExecutor(
            provider.driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            settings.max_result_rows,
            retry_policy=retry,
        )
        for case in load_cases():
            for intent in case.intents:
                assert validator.validate(intent.oracle_cypher).query_type == "r"
                result = executor.execute(intent.oracle_cypher)
                assert result.rows
                assert _fingerprint(result.rows) == _fingerprint(
                    intent.expected_snapshot
                )
    finally:
        provider.close()


def _fingerprint(rows: object) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
            for row in rows  # type: ignore[union-attr]
        )
    )
