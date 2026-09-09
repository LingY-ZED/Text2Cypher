"""完整调用链从生产装配到参数化网关的契约。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from text2cypher.application.factory import (
    PipelineComponents,
    build_single_round_runtime,
)
from text2cypher.components.call_chain_anchor import parse_call_chain_anchor
from text2cypher.domain.errors import CypherValidationError
from text2cypher.domain.models import (
    CALL_CHAIN_RESULT_COLUMNS,
    ExecutedCypher,
    GraphSchema,
    QueryResult,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.tools.find_call_chain import FindCallChainTool
from text2cypher.tools.resolve_symbol import ResolveSymbolTool


def forbidden(*args, **kwargs):
    raise AssertionError("deterministic query must not call LLM")


class Gateway:
    def __init__(self, *, missing=False, error=None):
        self.statements = []
        self.missing = missing
        self.error = error

    def execute_candidate(self, candidate):
        return forbidden(candidate)

    def execute_statement(self, statement):
        self.statements.append(statement)
        if "AS qualified_name" in statement.cypher:
            rows = (
                ()
                if self.missing
                else (
                    {
                        "qualified_name": "sample.Service.run",
                        "method_name": "run",
                        "class_qualified_name": "sample.Service",
                        "service_name": "ts-demo-service",
                        "graph_version": "v2",
                    },
                )
            )
            return ExecutedCypher(statement.cypher, QueryResult((), rows))
        if self.error:
            raise self.error
        return ExecutedCypher(
            statement.cypher, QueryResult(CALL_CHAIN_RESULT_COLUMNS, ())
        )


def runtime(gateway, engine=None):
    return build_single_round_runtime(
        PipelineComponents(
            schema_fetcher=SimpleNamespace(fetch=lambda: GraphSchema()),
            prompt_builder=SimpleNamespace(build=forbidden),
            llm_client=SimpleNamespace(generate=forbidden),
            cypher_parser=None,
            cypher_validator=None,
            cypher_executor=None,
            read_only_cypher_gateway=gateway,
            graph_query_engine=engine,
            primary_agent=SimpleNamespace(plan=forbidden),
        )
    )


@pytest.mark.parametrize(
    "anchor,is_api",
    [
        ("/api/v1/travelservice/trips/left", True),
        ("POST /api/example", True),
        ("sample.Service.run", False),
        ("Service.run", False),
        ("run", False),
    ],
)
def test_runtime_bypasses_llm_and_preserves_empty_result(anchor, is_api):
    gateway = Gateway()
    run = runtime(gateway).run(f"查询{anchor} 的完整调用链")
    assert not run.plan.decomposed
    assert run.plan.queries[0].query_shape is QueryShape.FULL_METHOD_CALL_CHAIN
    assert len(run.sub_queries) == 1
    assert run.sub_queries[0].result.columns == CALL_CHAIN_RESULT_COLUMNS
    assert not run.sub_queries[0].result.rows
    assert ("WHERE true" in gateway.statements[0].cypher) == is_api
    assert gateway.statements[-1].parameters["graphVersion"] == "v2"
    assert "UNION" in gateway.statements[-1].cypher


def test_explicit_api_qualifiers_are_parameters():
    gateway = Gateway()
    runtime(gateway).run("POST /api/example 在 ts-demo-service 图谱版本 v2 的调用链")
    assert gateway.statements[0].parameters == {
        "anchor": "/api/example",
        "apiPath": "/api/example",
        "httpMethod": "POST",
        "serviceName": "ts-demo-service",
        "graphVersion": "v2",
    }
    assert parse_call_chain_anchor("/api/v1/demo 的调用链").graph_version is None


def test_deterministic_failure_never_falls_back():
    with pytest.raises(CypherValidationError, match="explain failure"):
        runtime(Gateway(error=CypherValidationError("explain failure"))).run(
            "Service.run 的调用链"
        )


@pytest.mark.parametrize("question", ["/api/missing 的调用链", "某方法的调用链"])
def test_only_unmatched_capability_falls_back(question):
    requests = []

    def query(request, context):
        requests.append(request)
        return ExecutedCypher("RETURN 1", QueryResult((), ()))

    run = runtime(Gateway(missing=True), SimpleNamespace(query=query)).run(question)
    assert len(requests) == 1
    assert requests[0].query_shape is QueryShape.GENERAL
    assert len(run.sub_queries) == 1


def test_multi_version_anchors_remain_paired_in_one_bounded_execution():
    from text2cypher.domain.models import ResolvedMethod

    methods = (
        ResolvedMethod("a.Service.run", "run", "a.Service", "a", "v1"),
        ResolvedMethod("b.Service.run", "run", "b.Service", "b", "v2"),
    )
    gateway = Gateway()
    tool = FindCallChainTool(
        SimpleNamespace(resolve_symbol=lambda *a, **k: methods), gateway
    )
    tool.find_call_chain("run")
    assert len(gateway.statements) == 1
    assert gateway.statements[0].parameters == {
        "g0_anchorQualifiedName": "a.Service.run",
        "g0_graphVersion": "v1",
        "g1_anchorQualifiedName": "b.Service.run",
        "g1_graphVersion": "v2",
    }
    assert "$g0_anchorQualifiedName" in gateway.statements[0].cypher
    assert "$g1_graphVersion" in gateway.statements[0].cypher


def test_resolution_truncation_is_not_silently_complete():
    gateway = SimpleNamespace(
        execute_statement=lambda statement: ExecutedCypher(
            statement.cypher,
            QueryResult((), (), truncated=True),
        )
    )
    from text2cypher.domain.errors import CypherExecutionError

    with pytest.raises(CypherExecutionError):
        ResolveSymbolTool(gateway).resolve_entry_api(None, "/api/example")
