"""Regression coverage for deterministic legacy method-query compilation."""

from __future__ import annotations

import pytest

from text2cypher.domain.models import ResolvedMethod
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.graph_core.method_query import MethodQueryCypherCompiler


@pytest.mark.parametrize(
    ("query_shape", "expected_column", "expected_unions"),
    (
        (QueryShape.DIRECT_UPSTREAM, "调用方法", 0),
        (QueryShape.DIRECT_DOWNSTREAM_METHOD, "被调用方法", 0),
        (QueryShape.REACHABLE_ENTRY_API, "入口API", 0),
        (QueryShape.FULL_ENTRY_CHAIN, "方法路径", 3),
        (QueryShape.DIRECT_REST_EGRESS, "下游服务", 0),
    ),
)
def test_method_query_compiler_uses_a_parameterized_read_only_contract(
    query_shape: QueryShape,
    expected_column: str,
    expected_unions: int,
) -> None:
    statement = MethodQueryCypherCompiler().compile(
        query_shape,
        (
            ResolvedMethod(
                qualified_name="sample.Service.run' RETURN 1 //",
                method_name="run",
                class_qualified_name="sample.Service",
                service_name="sample-service",
                graph_version="v1",
            ),
        ),
    )

    assert statement.parameters == {
        "anchorQualifiedName": "sample.Service.run' RETURN 1 //"
    }
    assert "sample.Service.run" not in statement.cypher
    assert expected_column in statement.cypher
    assert statement.cypher.count("UNION") == expected_unions
    assert ";" not in statement.cypher
    assert "//" not in statement.cypher
    assert "CALL " not in statement.cypher


def test_method_query_compiler_batches_ambiguous_anchors_in_one_statement() -> None:
    statement = MethodQueryCypherCompiler().compile(
        QueryShape.DIRECT_UPSTREAM,
        (
            ResolvedMethod(
                qualified_name="first.Service.shared",
                method_name="shared",
                class_qualified_name="first.Service",
                service_name="first-service",
                graph_version="v1",
            ),
            ResolvedMethod(
                qualified_name="second.Service.shared",
                method_name="shared",
                class_qualified_name="second.Service",
                service_name="second-service",
                graph_version="v2",
            ),
        ),
    )

    assert statement.parameters == {
        "anchorQualifiedName0": "first.Service.shared",
        "anchorQualifiedName1": "second.Service.shared",
    }
    assert "first.Service.shared" not in statement.cypher
    assert "second.Service.shared" not in statement.cypher
    assert "target.全限定名 = $anchorQualifiedName0" in statement.cypher
    assert "target.全限定名 = $anchorQualifiedName1" in statement.cypher


def test_method_query_compiler_rejects_an_unsupported_shape() -> None:
    method = ResolvedMethod(
        qualified_name="sample.Service.run",
        method_name="run",
        class_qualified_name="sample.Service",
        service_name="sample-service",
        graph_version="v1",
    )

    with pytest.raises(ValueError, match="不支持"):
        MethodQueryCypherCompiler().compile(QueryShape.GENERAL, (method,))
