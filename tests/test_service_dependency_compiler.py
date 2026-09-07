"""Tests for parameterized service-dependency matrix compilation."""

from __future__ import annotations

from text2cypher.graph_core.service_dependency import ServiceDependencyCypherCompiler


def test_service_dependency_compiler_keeps_service_name_out_of_cypher() -> None:
    statement = ServiceDependencyCypherCompiler().compile_message_senders_rest_targets(
        "ts-notification-service' RETURN 1 //"
    )

    assert statement.parameters == {
        "receiverServiceName": "ts-notification-service' RETURN 1 //"
    }
    assert "ts-notification-service" not in statement.cypher
    assert "发送服务" in statement.cypher
    assert "下游服务" in statement.cypher
    assert ";" not in statement.cypher
    assert "//" not in statement.cypher
    assert "CALL " not in statement.cypher
