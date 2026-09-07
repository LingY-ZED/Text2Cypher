"""Tests for parameterized ownership and REST-dependency fact compilation."""

from __future__ import annotations

import pytest

from text2cypher.graph_core.service_facts import ServiceFactCypherCompiler


@pytest.mark.parametrize(
    ("statement_name", "value", "parameter"),
    (
        ("compile_message_queue_count", "email' RETURN 1 //", "queueName"),
        ("compile_exchange_owners", "(default)' RETURN 1 //", "exchangeName"),
        (
            "compile_rest_callers",
            "ts-order-other-service' RETURN 1 //",
            "targetServiceName",
        ),
        (
            "compile_entry_api_http_distribution",
            "ts-admin-basic-info-service' RETURN 1 //",
            "serviceName",
        ),
        (
            "compile_rest_target_call_counts",
            "ts-admin-basic-info-service' RETURN 1 //",
            "serviceName",
        ),
        (
            "compile_implementation_classes",
            "ts-admin-basic-info-service' RETURN 1 //",
            "serviceName",
        ),
    ),
)
def test_service_fact_compiler_keeps_entity_values_out_of_cypher(
    statement_name: str,
    value: str,
    parameter: str,
) -> None:
    statement = getattr(ServiceFactCypherCompiler(), statement_name)(value)

    assert statement.parameters == {parameter: value}
    assert value not in statement.cypher
    assert ";" not in statement.cypher
    assert "//" not in statement.cypher
    assert "CALL " not in statement.cypher
