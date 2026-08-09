from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from text2cypher.components.retry import RetryPolicy
from text2cypher.domain.errors import CypherValidationError, Neo4jAccessError
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator


@dataclass
class FakeSummary:
    """模拟 Neo4j 的查询摘要。"""

    query_type: str
    gql_status_objects: tuple[FakeStatus, ...] = ()


@dataclass
class FakeStatus:
    """模拟 Neo4j 的 GQL 状态对象。"""

    gql_status: str


@dataclass
class FakeExplainResult:
    """模拟 EXPLAIN 的执行结果。"""

    summary: FakeSummary


class FakeValidatorDriver:
    """记录 EXPLAIN 调用的模拟驱动。"""

    def __init__(
        self,
        query_type: str = "r",
        statuses: tuple[FakeStatus, ...] = (),
        errors: list[Exception] | None = None,
    ) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self._query_type = query_type
        self._statuses = statuses
        self._errors = list(errors or [])

    def execute_query(self, query: Any, **kwargs: Any) -> FakeExplainResult:
        self.calls.append((query, kwargs))
        if self._errors:
            raise self._errors.pop(0)
        return FakeExplainResult(
            summary=FakeSummary(
                query_type=self._query_type,
                gql_status_objects=self._statuses,
            )
        )


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


def test_validator_allows_call_as_relationship_variable() -> None:
    driver = FakeValidatorDriver()

    report = Neo4jCypherValidator(driver, "neo4j", 5).validate(
        "MATCH (source)-[call:调用]->(target) "
        "WHERE call.调用类型 = '远程调用' RETURN target"
    )

    assert report.query_type == "r"
    assert len(driver.calls) == 1


def test_validator_passes_parameters_to_explain() -> None:
    driver = FakeValidatorDriver()

    Neo4jCypherValidator(driver, "neo4j", 5).validate(
        "UNWIND $dep_q1_rows AS input RETURN input",
        {"dep_q1_rows": [{"id": "safe-value"}]},
    )

    assert driver.calls[0][1]["parameters_"] == {
        "dep_q1_rows": [{"id": "safe-value"}]
    }


def test_validator_rejects_non_read_query_type_after_explain() -> None:
    driver = FakeValidatorDriver(query_type="w")

    with pytest.raises(CypherValidationError, match="只读"):
        Neo4jCypherValidator(driver, "neo4j", 5).validate("RETURN 1")


def test_validator_rejects_unknown_property_notification_after_explain() -> None:
    driver = FakeValidatorDriver(statuses=(FakeStatus(gql_status="01N52"),))

    with pytest.raises(CypherValidationError, match="不存在的属性"):
        Neo4jCypherValidator(driver, "neo4j", 5).validate("RETURN 节点.不存在")


def test_validator_retries_transient_explain_error() -> None:
    driver = FakeValidatorDriver(errors=[ServiceUnavailable("短暂故障")])
    waits: list[float] = []

    report = Neo4jCypherValidator(
        driver,
        "neo4j",
        5,
        retry_policy=RetryPolicy(),
        sleep_func=waits.append,
    ).validate("RETURN 1")

    assert report.query_type == "r"
    assert len(driver.calls) == 2
    assert waits == [0.5]


def test_validator_does_not_retry_neo4j_access_error() -> None:
    driver = FakeValidatorDriver(errors=[AuthError("权限不足")])

    with pytest.raises(Neo4jAccessError, match="拒绝"):
        Neo4jCypherValidator(
            driver,
            "neo4j",
            5,
            sleep_func=lambda _: pytest.fail("访问错误不应重试"),
        ).validate("RETURN 1")

    assert len(driver.calls) == 1
