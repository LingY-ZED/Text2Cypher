from __future__ import annotations

import pytest

from text2cypher.application.factory import (
    PipelineComponents,
    build_pipeline_from_components,
)
from text2cypher.domain.models import (
    ExecutedCypher,
    GraphQueryRequest,
    GraphSchema,
    QueryContext,
    QueryResult,
    SubQueryResponse,
)


class _SchemaFetcher:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def fetch(self) -> GraphSchema:
        self._calls.append("schema")
        return GraphSchema()


class _GraphQueryEngine:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher:
        assert request.query_id == "q1"
        assert context.schema == GraphSchema()
        self._calls.append("engine")
        return ExecutedCypher("RETURN 1", QueryResult(("value",), ({"value": 1},)))


class _Formatter:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> str:
        assert question == "问题"
        assert len(sub_queries) == 1
        self._calls.append("formatter")
        return "formatted"


def _components(
    calls: list[str],
    *,
    result_formatter: _Formatter | None = None,
) -> PipelineComponents:
    return PipelineComponents(
        schema_fetcher=_SchemaFetcher(calls),
        prompt_builder=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        cypher_parser=object(),  # type: ignore[arg-type]
        cypher_validator=object(),  # type: ignore[arg-type]
        cypher_executor=object(),  # type: ignore[arg-type]
        result_formatter=result_formatter,
        graph_query_engine=_GraphQueryEngine(calls),
    )


def test_factory_builds_the_pipeline_from_explicit_components() -> None:
    calls: list[str] = []
    pipeline = build_pipeline_from_components(
        _components(calls, result_formatter=_Formatter(calls))
    )

    response = pipeline.run("问题")

    assert response.formatted == "formatted"
    assert calls == ["schema", "engine", "formatter"]


def test_factory_rejects_a_missing_interface_formatter() -> None:
    with pytest.raises(ValueError, match="ResultFormatter"):
        build_pipeline_from_components(_components([]))
