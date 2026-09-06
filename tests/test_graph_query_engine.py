from __future__ import annotations

import pytest

from text2cypher.domain.errors import (
    CypherParseError,
    Neo4jConnectionError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    ExecutedCypher,
    GraphQueryRequest,
    GraphSchema,
    LLMResponse,
    PrimaryAgentQuery,
    QueryContext,
    QueryResult,
    QueryStatement,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.graph_core.readonly_cypher_gateway import CandidateExecutionFailure
from text2cypher.query_engine.engine import DefaultGraphQueryEngine


class RecordingRouter:
    def __init__(self, calls: list[str], expected: PrimaryAgentQuery) -> None:
        self._calls = calls
        self._expected = expected

    def route(self, question: str, schema: GraphSchema) -> tuple[object, ...]:
        del question, schema
        raise AssertionError("planned request must use route_planned")

    def route_planned(
        self,
        query: PrimaryAgentQuery,
        schema: GraphSchema,
    ) -> tuple[object, ...]:
        assert query is self._expected
        assert schema == GraphSchema()
        self._calls.append("router")
        return ()


class RecordingPromptBuilder:
    def __init__(self, calls: list[str], expected: PrimaryAgentQuery) -> None:
        self._calls = calls
        self._expected = expected

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[object, ...] = (),
    ) -> ChatPrompt:
        del schema, question, examples
        raise AssertionError("planned request must use build_planned")

    def build_planned(
        self,
        schema: GraphSchema,
        query: PrimaryAgentQuery,
        examples: tuple[object, ...] = (),
    ) -> ChatPrompt:
        assert query is self._expected
        assert schema == GraphSchema()
        assert examples == ()
        self._calls.append("prompt")
        return ChatPrompt(system="system", user="user")


class RecordingLLM:
    def __init__(self, calls: list[str], responses: list[LLMResponse]) -> None:
        self._calls = calls
        self._responses = responses

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        assert prompt == ChatPrompt(system="system", user="user")
        self._calls.append("llm")
        return self._responses.pop(0)


class SequenceGateway:
    def __init__(
        self,
        calls: list[str],
        outcomes: list[ExecutedCypher | Exception],
    ) -> None:
        self._calls = calls
        self._outcomes = outcomes

    def execute_candidate(self, candidate: str) -> ExecutedCypher:
        self._calls.append(f"gateway:{candidate}")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class DeterministicGateway:
    def __init__(
        self,
        result: ExecutedCypher,
        method_rows: tuple[dict[str, str], ...] | None = None,
    ) -> None:
        self._result = result
        self._method_rows = method_rows or (
            {
                "qualified_name": "sample.Service.run",
                "method_name": "run",
                "class_qualified_name": "sample.Service",
                "service_name": "sample-service",
                "graph_version": "v2",
            },
        )
        self.statements: list[QueryStatement] = []

    def execute_candidate(self, candidate: str) -> ExecutedCypher:
        raise AssertionError(f"不应调用 LLM 候选执行：{candidate}")

    def execute_statement(self, statement: QueryStatement) -> ExecutedCypher:
        self.statements.append(statement)
        if len(self.statements) == 1:
            return ExecutedCypher(
                cypher=statement.cypher,
                result=QueryResult(
                    columns=(
                        "qualified_name",
                        "method_name",
                        "class_qualified_name",
                        "service_name",
                        "graph_version",
                    ),
                    rows=self._method_rows,
                ),
            )
        return self._result


class RecordingCorrector:
    def __init__(self, response: LLMResponse) -> None:
        self._response = response
        self.calls: list[tuple[ChatPrompt, str, object]] = []

    def correct(
        self,
        prompt: ChatPrompt,
        candidate: str,
        failure: object,
    ) -> LLMResponse:
        self.calls.append((prompt, candidate, failure))
        return self._response


def _planned_query() -> PrimaryAgentQuery:
    return PrimaryAgentQuery(
        query_id="q1",
        question="查询 FoodServiceImpl.getAllFood 的上游调用方",
        intent="定位直接调用方",
        required_information=("调用方法",),
    )


def _engine(
    calls: list[str],
    query: PrimaryAgentQuery,
    outcomes: list[ExecutedCypher | Exception],
    *,
    corrector: RecordingCorrector | None = None,
    recover_empty_results: bool = False,
) -> DefaultGraphQueryEngine:
    return DefaultGraphQueryEngine(
        prompt_builder=RecordingPromptBuilder(calls, query),
        llm_client=RecordingLLM(calls, [LLMResponse(content="initial")]),
        read_only_cypher_gateway=SequenceGateway(calls, outcomes),
        few_shot_router=RecordingRouter(calls, query),
        cypher_corrector=corrector,
        recover_empty_results=recover_empty_results,
    )


def test_engine_preserves_planned_port_identity_and_execution_order() -> None:
    calls: list[str] = []
    query = _planned_query()
    executed = ExecutedCypher(
        cypher="RETURN 1",
        result=QueryResult(columns=("value",), rows=({"value": 1},)),
    )

    result = _engine(calls, query, [executed]).query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == executed
    assert calls == ["router", "prompt", "llm", "gateway:initial"]


def test_engine_corrects_parse_failure_with_raw_candidate() -> None:
    calls: list[str] = []
    query = _planned_query()
    corrected = ExecutedCypher(
        cypher="RETURN 2",
        result=QueryResult(columns=("value",), rows=({"value": 2},)),
    )
    corrector = RecordingCorrector(LLMResponse(content="corrected"))

    result = _engine(
        calls,
        query,
        [
            CandidateExecutionFailure("initial", CypherParseError("cannot parse")),
            corrected,
        ],
        corrector=corrector,
    ).query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == corrected
    assert calls == [
        "router",
        "prompt",
        "llm",
        "gateway:initial",
        "gateway:corrected",
    ]
    assert corrector.calls[0][1] == "initial"


def test_engine_replaces_empty_result_only_when_recovery_is_nonempty() -> None:
    calls: list[str] = []
    query = _planned_query()
    original = ExecutedCypher(
        cypher="RETURN initial",
        result=QueryResult(columns=("value",), rows=()),
    )
    corrected = ExecutedCypher(
        cypher="RETURN corrected",
        result=QueryResult(columns=("value",), rows=({"value": 2},)),
    )
    corrector = RecordingCorrector(LLMResponse(content="corrected"))

    result = _engine(
        calls,
        query,
        [original, corrected],
        corrector=corrector,
        recover_empty_results=True,
    ).query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == corrected
    assert calls[-2:] == ["gateway:initial", "gateway:corrected"]
    assert corrector.calls[0][1] == "RETURN initial"


def test_engine_does_not_correct_connection_failures() -> None:
    calls: list[str] = []
    query = _planned_query()
    corrector = RecordingCorrector(LLMResponse(content="corrected"))

    with pytest.raises(Neo4jConnectionError, match="offline"):
        _engine(
            calls,
            query,
            [Neo4jConnectionError("offline")],
            corrector=corrector,
        ).query(
            GraphQueryRequest.from_primary_agent_query(query),
            QueryContext(GraphSchema()),
        )

    assert corrector.calls == []


def test_engine_uses_deterministic_compiler_for_a_uniquely_resolved_call_chain(
) -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question="sample.Service.run 的完整调用链是什么？",
        intent="查询完整调用链",
        required_information=("完整调用链表格",),
        anchor="sample.Service.run",
        query_shape=QueryShape.FULL_METHOD_CALL_CHAIN,
    )
    expected = ExecutedCypher(
        cypher="compiled call chain",
        result=QueryResult(
            columns=("根方法",),
            rows=({"根方法": "sample.Service.run"},),
        ),
    )
    gateway = DeterministicGateway(expected)
    calls: list[str] = []
    engine = DefaultGraphQueryEngine(
        prompt_builder=RecordingPromptBuilder(calls, query),
        llm_client=RecordingLLM(calls, []),
        read_only_cypher_gateway=gateway,
        few_shot_router=RecordingRouter(calls, query),
    )

    result = engine.query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == expected
    assert calls == []
    assert len(gateway.statements) == 2
    assert "sample.Service.run" not in gateway.statements[1].cypher
    assert gateway.statements[1].parameters == {
        "anchorQualifiedName": "sample.Service.run",
        "graphVersion": "v2",
    }


def test_engine_batches_ambiguous_same_name_methods_without_llm_fallback() -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question="shared 的完整调用链是什么？",
        intent="查询完整调用链",
        required_information=("完整调用链表格",),
        anchor="shared",
        query_shape=QueryShape.FULL_METHOD_CALL_CHAIN,
    )
    expected = ExecutedCypher(
        cypher="compiled call chain",
        result=QueryResult(
            columns=("根方法",),
            rows=({"根方法": "first.Service.shared"},),
        ),
    )
    gateway = DeterministicGateway(
        expected,
        method_rows=(
            {
                "qualified_name": "first.Service.shared",
                "method_name": "shared",
                "class_qualified_name": "first.Service",
                "service_name": "first-service",
                "graph_version": "v2",
            },
            {
                "qualified_name": "second.Service.shared",
                "method_name": "shared",
                "class_qualified_name": "second.Service",
                "service_name": "second-service",
                "graph_version": "v2",
            },
        ),
    )
    calls: list[str] = []
    engine = DefaultGraphQueryEngine(
        prompt_builder=RecordingPromptBuilder(calls, query),
        llm_client=RecordingLLM(calls, []),
        read_only_cypher_gateway=gateway,
        few_shot_router=RecordingRouter(calls, query),
    )

    result = engine.query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == expected
    assert calls == []
    assert gateway.statements[1].parameters == {
        "anchorQualifiedName0": "first.Service.shared",
        "anchorQualifiedName1": "second.Service.shared",
        "graphVersion": "v2",
    }
