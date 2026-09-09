from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from text2cypher.components.retry import RetryPolicy
from text2cypher.domain.errors import CypherValidationError, Neo4jAccessError
from text2cypher.domain.models import CypherFailureKind
from text2cypher.infrastructure.neo4j.failure_context import neo4j_error_context
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator
from text2cypher.interfaces.logging import JsonLogFormatter


def test_explain_diagnostics_reach_json_logs(caplog):
    status = FakeStatus(
        "01N52", "unknown property", {"_position": {"line": 3, "column": 9}}
    )
    validator = Neo4jCypherValidator(
        FakeValidatorDriver(statuses=(status,)), "neo4j", 30
    )
    with pytest.raises(CypherValidationError):
        validator.validate("MATCH (n) RETURN n.missing")
    record = next(r for r in caplog.records if r.getMessage() == "neo4j_explain_failed")
    payload = json.loads(JsonLogFormatter().format(record))
    assert {
        key: payload[key] for key in ("code", "gql_status", "line", "column", "message")
    } == {
        "code": None,
        "gql_status": "01N52",
        "line": 3,
        "column": 9,
        "message": "unknown property",
    }


def test_exception_diagnostic_position_is_extracted():
    error = SimpleNamespace(
        message="syntax error",
        code="Neo.ClientError.Statement.SyntaxError",
        gql_status="42001",
        diagnostic_record={"_position": {"line": 2, "column": 7}},
    )
    context = neo4j_error_context(
        CypherFailureKind.VALIDATION, error, fallback_message="failed"
    )
    assert (context.line, context.column, context.gql_status) == (2, 7, "42001")


@dataclass
class FakeSummary:
    """模拟 Neo4j 的查询摘要。"""

    query_type: str
    gql_status_objects: tuple[FakeStatus, ...] = ()


@dataclass
class FakeStatus:
    """模拟 Neo4j 的 GQL 状态对象。"""

    gql_status: str
    status_description: str | None = None
    diagnostic_record: dict[str, object] | None = None


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


def test_validator_passes_parameters_to_neo4j_explain() -> None:
    driver = FakeValidatorDriver()

    report = Neo4jCypherValidator(driver, "neo4j", 5).validate(
        "RETURN $value AS value",
        {"value": 7},
    )

    assert report.query_type == "r"
    assert driver.calls[0][1]["parameters_"] == {"value": 7}


def test_validator_preserves_whitelisted_server_error_for_corrector(caplog) -> None:
    error = Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Statement.SyntaxError",
        message="Invalid input 'RETURN': expected ')'",
        position={"line": 1, "column": 18, "offset": 17},
    )
    driver = FakeValidatorDriver(errors=[error])

    with pytest.raises(CypherValidationError) as captured:
        Neo4jCypherValidator(driver, "neo4j", 5).validate("RETURN (")

    assert str(captured.value) == "Cypher 未通过 Neo4j EXPLAIN 校验"
    context = captured.value.failure_context
    assert context is not None
    assert context.message == "Invalid input 'RETURN': expected ')'"
    assert context.code == "Neo.ClientError.Statement.SyntaxError"
    assert context.gql_status == "50N42"
    assert context.classification == "ClientError"
    assert (context.line, context.column, context.offset) == (1, 18, 17)
    payload = json.loads(JsonLogFormatter().format(caplog.records[-1]))
    assert payload["code"] == context.code
    assert payload["message"] == context.message
    assert (payload["line"], payload["column"]) == (1, 18)


def test_validator_preserves_unknown_property_explain_status_for_corrector() -> None:
    driver = FakeValidatorDriver(
        statuses=(
            FakeStatus(
                gql_status="01N52",
                status_description="Unknown property `missing`",
                diagnostic_record={"_position": {"line": 1, "column": 8, "offset": 7}},
            ),
        )
    )

    with pytest.raises(CypherValidationError) as captured:
        Neo4jCypherValidator(driver, "neo4j", 5).validate("RETURN n.missing")

    context = captured.value.failure_context
    assert context is not None
    assert context.message == "Unknown property `missing`"
    assert context.gql_status == "01N52"
    assert (context.line, context.column, context.offset) == (1, 8, 7)
