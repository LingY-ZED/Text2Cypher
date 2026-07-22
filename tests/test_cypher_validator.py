from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from text2cypher.domain.errors import CypherValidationError
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator


@dataclass
class FakeSummary:
    """模拟 Neo4j 的查询摘要。"""

    query_type: str


@dataclass
class FakeExplainResult:
    """模拟 EXPLAIN 的执行结果。"""

    summary: FakeSummary


class FakeValidatorDriver:
    """记录 EXPLAIN 调用的模拟驱动。"""

    def __init__(self, query_type: str = "r") -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self._query_type = query_type

    def execute_query(self, query: Any, **kwargs: Any) -> FakeExplainResult:
        self.calls.append((query, kwargs))
        return FakeExplainResult(summary=FakeSummary(query_type=self._query_type))


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (节点)",
        "RETURN 1; RETURN 2",
        "RETURN 1 // 注释",
        "CALL db.labels()",
    ],
)
def test_validator_rejects_unsafe_cypher_before_explain(cypher: str) -> None:
    driver = FakeValidatorDriver()

    with pytest.raises(CypherValidationError):
        Neo4jCypherValidator(driver, "neo4j", 5).validate(cypher)

    assert driver.calls == []


def test_validator_allows_string_keyword_and_requires_read_plan() -> None:
    driver = FakeValidatorDriver()

    report = Neo4jCypherValidator(driver, "neo4j", 5).validate(
        "RETURN 'CREATE' AS 文本"
    )

    assert report.query_type == "r"
    assert len(driver.calls) == 1


def test_validator_rejects_non_read_query_type_after_explain() -> None:
    driver = FakeValidatorDriver(query_type="w")

    with pytest.raises(CypherValidationError, match="只读"):
        Neo4jCypherValidator(driver, "neo4j", 5).validate("RETURN 1")
