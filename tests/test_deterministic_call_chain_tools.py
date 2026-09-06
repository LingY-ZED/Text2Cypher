"""实体解析与完整调用链 Tool 的确定性边界测试。"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from text2cypher.domain.errors import CypherExecutionError
from text2cypher.domain.models import (
    CALL_CHAIN_RESULT_COLUMNS,
    ExecutedCypher,
    QueryResult,
    QueryStatement,
    ResolvedMethod,
)
from text2cypher.tools.find_call_chain import FindCallChainTool
from text2cypher.tools.resolve_symbol import ResolveSymbolTool


def _method_row(
    qualified_name: str = "sample.Service.run",
) -> dict[str, str]:
    return {
        "qualified_name": qualified_name,
        "method_name": "run",
        "class_qualified_name": "sample.Service",
        "service_name": "sample-service",
        "graph_version": "v2",
    }


class _Gateway:
    def __init__(self, results: list[QueryResult]) -> None:
        self._results = results
        self.statements: list[QueryStatement] = []

    def execute_statement(self, statement: QueryStatement) -> ExecutedCypher:
        self.statements.append(statement)
        return ExecutedCypher(statement.cypher, self._results.pop(0))


def test_resolve_symbol_prefers_exact_qualified_name() -> None:
    gateway = _Gateway([QueryResult((), (_method_row(),))])

    resolved = ResolveSymbolTool(gateway).resolve_symbol("sample.Service.run")

    assert resolved == (ResolvedMethod(**_method_row()),)
    assert len(gateway.statements) == 1
    statement = gateway.statements[0]
    assert "$anchor" in statement.cypher
    assert "sample.Service.run" not in statement.cypher
    assert statement.parameters == {
        "anchor": "sample.Service.run",
        "graphVersion": None,
        "serviceName": None,
        "httpMethod": None,
        "apiPath": None,
    }


def test_resolve_symbol_falls_back_to_class_suffix_then_method_name() -> None:
    class_gateway = _Gateway(
        [
            QueryResult((), ()),
            QueryResult((), (_method_row("package.Service.run"),)),
        ]
    )
    resolved = ResolveSymbolTool(class_gateway).resolve_symbol("Service.run")

    assert resolved[0].qualified_name == "package.Service.run"
    assert len(class_gateway.statements) == 2
    assert "class.全限定名 ENDS WITH $className" in class_gateway.statements[1].cypher
    assert class_gateway.statements[1].parameters["className"] == "Service"

    method_gateway = _Gateway(
        [QueryResult((), ()), QueryResult((), (_method_row(),))]
    )
    resolved = ResolveSymbolTool(method_gateway).resolve_symbol(
        "run",
        service_name="sample-service",
        http_method="POST",
        api_path="/api/sample",
    )

    assert resolved[0].method_name == "run"
    assert len(method_gateway.statements) == 2
    assert "method.方法名 = $methodName" in method_gateway.statements[1].cypher
    assert method_gateway.statements[1].parameters == {
        "anchor": "run",
        "graphVersion": None,
        "serviceName": "sample-service",
        "httpMethod": "POST",
        "apiPath": "/api/sample",
        "methodName": "run",
    }


def test_resolve_entry_api_uses_api_and_service_as_disambiguation() -> None:
    gateway = _Gateway([QueryResult((), (_method_row(),))])

    resolved = ResolveSymbolTool(gateway).resolve_entry_api(
        "POST",
        "/api/sample",
        service_name="sample-service",
    )

    assert resolved == (ResolvedMethod(**_method_row()),)
    statement = gateway.statements[0]
    assert "WHERE true" in statement.cypher
    assert statement.parameters == {
        "anchor": "/api/sample",
        "graphVersion": None,
        "serviceName": "sample-service",
        "httpMethod": "POST",
        "apiPath": "/api/sample",
    }


def _chain_row(**values: object) -> dict[str, object]:
    row: dict[str, object] = {column: None for column in CALL_CHAIN_RESULT_COLUMNS}
    row.update(
        {
            "根方法": "sample.Service.run",
            "图谱版本": "v2",
            "层级": 0,
            "链路类型": "local",
            "源服务": "sample-service",
            "源方法": "sample.Service.run",
            "方法路径": ["sample.Service.run"],
            "目标方法": "sample.Service.run",
        }
    )
    row.update(values)
    return row


class _Resolver:
    def __init__(self) -> None:
        self.kwargs: Mapping[str, object] | None = None

    def resolve_symbol(
        self,
        anchor: str,
        **kwargs: object,
    ) -> tuple[ResolvedMethod, ...]:
        self.kwargs = {"anchor": anchor, **kwargs}
        return (
            ResolvedMethod(
                "sample.Service.run",
                "run",
                "sample.Service",
                "sample-service",
                "v2",
            ),
        )


def test_find_call_chain_returns_sorted_scalar_segments() -> None:
    gateway = _Gateway(
        [
            QueryResult(
                columns=CALL_CHAIN_RESULT_COLUMNS,
                rows=(
                    _chain_row(
                        **{
                            "层级": 1,
                            "链路类型": "rest",
                            "下游API路径": "/api/target",
                            "目标服务": "target-service",
                        }
                    ),
                    _chain_row(),
                ),
            )
        ]
    )
    resolver = _Resolver()

    segments = FindCallChainTool(resolver, gateway).find_call_chain(
        "Service.run",
        graph_version="v2",
        local_hops=3,
        rest_hops=1,
        mq_hops=0,
        service_name="sample-service",
    )

    assert [segment.link_type.value for segment in segments] == ["local", "rest"]
    assert segments[0].to_row() == _chain_row()
    assert all(
        isinstance(value, (str, int, list, type(None)))
        for value in segments[1].to_row().values()
    )
    assert resolver.kwargs == {
        "anchor": "Service.run",
        "graph_version": "v2",
        "service_name": "sample-service",
        "http_method": None,
        "api_path": None,
    }
    assert gateway.statements[0].parameters == {
        "anchorQualifiedName": "sample.Service.run",
        "graphVersion": "v2",
    }
    assert "sample.Service.run" not in gateway.statements[0].cypher


def test_find_call_chain_rejects_a_truncated_complete_result() -> None:
    gateway = _Gateway(
        [
            QueryResult(
                columns=CALL_CHAIN_RESULT_COLUMNS,
                rows=(_chain_row(),),
                truncated=True,
            )
        ]
    )

    with pytest.raises(CypherExecutionError, match="超过安全行数上限"):
        FindCallChainTool(_Resolver(), gateway).find_call_chain("Service.run")
