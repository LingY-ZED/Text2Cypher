from __future__ import annotations

import pytest

from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherParseError,
    CypherValidationError,
    Neo4jConnectionError,
)
from text2cypher.domain.models import QueryResult, ValidationReport
from text2cypher.graph_core.readonly_cypher_gateway import (
    CandidateExecutionFailure,
    DefaultReadOnlyCypherGateway,
)


class RecordingParser:
    def __init__(self, calls: list[str], error: Exception | None = None) -> None:
        self._calls = calls
        self._error = error

    def parse(self, candidate: str) -> str:
        self._calls.append(f"parse:{candidate}")
        if self._error is not None:
            raise self._error
        return "RETURN 1"


class RecordingValidator:
    def __init__(self, calls: list[str], error: Exception | None = None) -> None:
        self._calls = calls
        self._error = error

    def validate(self, cypher: str) -> ValidationReport:
        self._calls.append(f"validate:{cypher}")
        if self._error is not None:
            raise self._error
        return ValidationReport(query_type="r")


class RecordingExecutor:
    def __init__(self, calls: list[str], error: Exception | None = None) -> None:
        self._calls = calls
        self._error = error

    def execute(self, cypher: str) -> QueryResult:
        self._calls.append(f"execute:{cypher}")
        if self._error is not None:
            raise self._error
        return QueryResult(columns=("value",), rows=({"value": 1},))


def _gateway(
    calls: list[str],
    *,
    parser_error: Exception | None = None,
    validator_error: Exception | None = None,
    executor_error: Exception | None = None,
) -> DefaultReadOnlyCypherGateway:
    return DefaultReadOnlyCypherGateway(
        RecordingParser(calls, parser_error),
        RecordingValidator(calls, validator_error),
        RecordingExecutor(calls, executor_error),
    )


def test_gateway_parses_validates_and_executes_in_fixed_order() -> None:
    calls: list[str] = []

    executed = _gateway(calls).execute_candidate("raw candidate")

    assert calls == ["parse:raw candidate", "validate:RETURN 1", "execute:RETURN 1"]
    assert executed.cypher == "RETURN 1"
    assert executed.result.rows == ({"value": 1},)


def test_gateway_keeps_the_raw_candidate_for_parse_failure() -> None:
    calls: list[str] = []
    error = CypherParseError("cannot parse")

    with pytest.raises(CandidateExecutionFailure) as captured:
        _gateway(calls, parser_error=error).execute_candidate("raw candidate")

    assert calls == ["parse:raw candidate"]
    assert captured.value.candidate == "raw candidate"
    assert captured.value.error is error


@pytest.mark.parametrize(
    ("validator_error", "executor_error", "expected_calls"),
    [
        (
            CypherValidationError("not read only"),
            None,
            ["parse:raw candidate", "validate:RETURN 1"],
        ),
        (
            None,
            CypherExecutionError("database failure"),
            [
                "parse:raw candidate",
                "validate:RETURN 1",
                "execute:RETURN 1",
            ],
        ),
    ],
)
def test_gateway_keeps_the_normalized_candidate_for_repairable_failures(
    validator_error: CypherValidationError | None,
    executor_error: CypherExecutionError | None,
    expected_calls: list[str],
) -> None:
    calls: list[str] = []

    with pytest.raises(CandidateExecutionFailure) as captured:
        _gateway(
            calls,
            validator_error=validator_error,
            executor_error=executor_error,
        ).execute_candidate("raw candidate")

    assert calls == expected_calls
    assert captured.value.candidate == "RETURN 1"
    assert (
        captured.value.error is validator_error
        or captured.value.error is executor_error
    )


def test_gateway_preserves_non_repairable_connection_errors() -> None:
    calls: list[str] = []

    with pytest.raises(Neo4jConnectionError, match="offline"):
        _gateway(
            calls,
            validator_error=Neo4jConnectionError("offline"),
        ).execute_candidate("raw candidate")

    assert calls == ["parse:raw candidate", "validate:RETURN 1"]
