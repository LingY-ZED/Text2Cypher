from __future__ import annotations

import pytest

from text2cypher import IterativeRuntime as PublicIterativeRuntime
from text2cypher.application.factory import (
    PipelineComponents,
    build_iterative_runtime,
    build_pipeline_from_components,
)
from text2cypher.domain.iterative import (
    IterativePlan,
    PlanDecision,
    QueryCodeGraphActionInput,
    RuntimeAction,
    RuntimeToolName,
)
from text2cypher.domain.models import (
    ExecutedCypher,
    GraphQueryRequest,
    GraphSchema,
    QueryContext,
    QueryResult,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
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
        assert request.query_id in {"q1", "r1a1"}
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


def test_factory_builds_explicit_iterative_runtime_without_changing_pipeline() -> None:
    calls: list[str] = []

    class _Planner:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, context):  # type: ignore[no-untyped-def]
            del context
            self.calls += 1
            if self.calls == 1:
                return IterativePlan(
                    PlanDecision.CONTINUE,
                    (),
                    ("服务",),
                    (
                        RuntimeAction(
                            "r1a1",
                            RuntimeToolName.QUERY_CODE_GRAPH,
                            "查询服务",
                            ("服务",),
                            QueryCodeGraphActionInput("查询服务"),
                        ),
                    ),
                )
            return IterativePlan(PlanDecision.COMPLETE, ("服务",), (), ())

    class _Answerer:
        def answer(self, context):  # type: ignore[no-untyped-def]
            assert context.stop_reason.value == "complete"
            return ResultSummary(
                "答案",
                ResultSummaryMode.TEMPLATE,
                ResultSummaryFallbackReason.DISABLED,
            )

    runtime = build_iterative_runtime(
        _components(calls),
        planner=_Planner(),
        answerer=_Answerer(),
    )

    run = runtime.run("问题")

    assert isinstance(runtime, PublicIterativeRuntime)
    assert run.stop_reason.value == "complete"
    assert calls == ["schema", "engine"]
