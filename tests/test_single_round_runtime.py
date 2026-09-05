from __future__ import annotations

import pytest

from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.domain.models import (
    ExecutedCypher,
    GraphQueryRequest,
    GraphSchema,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryContext,
    QueryResult,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
    SubQueryResponse,
)
from text2cypher.runtime.single_round import SingleRoundRuntime
from text2cypher.tools.query_code_graph import QueryCodeGraphTool
from text2cypher.tools.schema import GetSchemaTool


class _RecordingSchemaFetcher:
    def __init__(self, calls: list[str], schema: GraphSchema) -> None:
        self._calls = calls
        self._schema = schema

    def fetch(self) -> GraphSchema:
        self._calls.append("schema")
        return self._schema


class _TwoQueryPrimaryAgent:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def plan(self, question: str) -> PrimaryAgentPlan:
        self._calls.append("plan")
        return PrimaryAgentPlan(
            original_question=question,
            analysis_summary="分为两个独立问题。",
            queries=(
                PrimaryAgentQuery("q1", "第一问", "查询第一项", ("第一项",)),
                PrimaryAgentQuery("q2", "第二问", "查询第二项", ("第二项",)),
            ),
        )


class _RecordingGraphQueryEngine:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.requests: list[GraphQueryRequest] = []
        self.contexts: list[QueryContext] = []

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher:
        self._calls.append(f"query:{request.query_id}")
        self.requests.append(request)
        self.contexts.append(context)
        return ExecutedCypher(
            cypher=f"RETURN '{request.query_id}' AS query_id",
            result=QueryResult(("query_id",), ({"query_id": request.query_id},)),
        )


class _RecordingSummarizer:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def summarize(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> ResultSummary:
        assert question == "原始问题"
        assert tuple(item.question for item in sub_queries) == ("第一问", "第二问")
        self._calls.append("summary")
        return ResultSummary(
            "已完成。",
            ResultSummaryMode.TEMPLATE,
            ResultSummaryFallbackReason.DISABLED,
        )


def test_runtime_uses_tools_once_and_returns_unformatted_structured_run() -> None:
    calls: list[str] = []
    schema = GraphSchema()
    engine = _RecordingGraphQueryEngine(calls)
    runtime = SingleRoundRuntime(
        schema_tool=GetSchemaTool(_RecordingSchemaFetcher(calls, schema)),
        query_code_graph_tool=QueryCodeGraphTool(engine),
        primary_agent=_TwoQueryPrimaryAgent(calls),
        result_summarizer=_RecordingSummarizer(calls),
    )

    run = runtime.run("  原始问题  ")

    assert calls == ["schema", "plan", "query:q1", "query:q2", "summary"]
    assert run.question == "原始问题"
    assert run.plan.sub_questions == ("第一问", "第二问")
    assert tuple(item.question for item in run.sub_queries) == ("第一问", "第二问")
    assert run.summary is not None
    assert not hasattr(run, "formatted")
    assert [request.query_id for request in engine.requests] == ["q1", "q2"]
    assert all(context.schema is schema for context in engine.contexts)


def test_runtime_stops_before_summary_when_a_later_tool_query_fails() -> None:
    calls: list[str] = []

    class _FailingQueryTool:
        def query(
            self,
            request: GraphQueryRequest,
            context: QueryContext,
        ) -> ExecutedCypher:
            del context
            calls.append(f"query:{request.query_id}")
            if request.query_id == "q2":
                raise RuntimeError("second query failed")
            return ExecutedCypher("RETURN 1", QueryResult((), ()))

    class _FailingIfCalledSummarizer:
        def summarize(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> ResultSummary:
            del question, sub_queries
            raise AssertionError("失败后不应总结")

    runtime = SingleRoundRuntime(
        schema_tool=GetSchemaTool(_RecordingSchemaFetcher(calls, GraphSchema())),
        query_code_graph_tool=_FailingQueryTool(),
        primary_agent=_TwoQueryPrimaryAgent(calls),
        result_summarizer=_FailingIfCalledSummarizer(),
    )

    with pytest.raises(RuntimeError, match="second query failed"):
        runtime.run("原始问题")

    assert calls == ["schema", "plan", "query:q1", "query:q2"]


def test_pipeline_can_be_constructed_from_runtime_and_only_formats_its_result() -> None:
    calls: list[str] = []
    runtime = SingleRoundRuntime(
        schema_tool=GetSchemaTool(_RecordingSchemaFetcher(calls, GraphSchema())),
        query_code_graph_tool=QueryCodeGraphTool(_RecordingGraphQueryEngine(calls)),
    )

    class _Formatter:
        def format(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> str:
            assert question == "问题"
            assert len(sub_queries) == 1
            calls.append("format")
            return "formatted"

    response = Text2CypherPipeline(
        result_formatter=_Formatter(),
        single_round_runtime=runtime,
    ).run("问题")

    assert response.formatted == "formatted"
    assert calls == ["schema", "query:q1", "format"]
