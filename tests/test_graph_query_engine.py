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


class DirectStatementGateway:
    def __init__(self, result: ExecutedCypher) -> None:
        self._result = result
        self.statements: list[QueryStatement] = []

    def execute_candidate(self, candidate: str) -> ExecutedCypher:
        raise AssertionError(f"不应调用 LLM 候选执行：{candidate}")

    def execute_statement(self, statement: QueryStatement) -> ExecutedCypher:
        self.statements.append(statement)
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


def test_engine_routes_legacy_full_method_shape_through_the_translator() -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question="sample.Service.run 的完整调用链是什么？",
        intent="查询完整调用链",
        required_information=("完整调用链表格",),
        anchor="sample.Service.run",
        query_shape=QueryShape.FULL_METHOD_CALL_CHAIN,
    )
    calls: list[str] = []
    expected = ExecutedCypher(
        cypher="MATCH translated RETURN translated",
        result=QueryResult(columns=("translated",), rows=({"translated": 1},)),
    )
    gateway = SequenceGateway(calls, [expected])

    result = DefaultGraphQueryEngine(
        prompt_builder=RecordingPromptBuilder(calls, query),
        llm_client=RecordingLLM(
            calls,
            [LLMResponse(content="MATCH translated RETURN translated")],
        ),
        read_only_cypher_gateway=gateway,
        few_shot_router=RecordingRouter(calls, query),
    ).query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == expected
    assert calls == [
        "router",
        "prompt",
        "llm",
        "gateway:MATCH translated RETURN translated",
    ]


def test_engine_does_not_reassemble_a_general_api_call_chain_child() -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question="查询/api/v1/travelservice/trips/left的调用链",
        intent="查询 API 调用链",
        required_information=("完整调用链表格",),
        anchor="/api/v1/travelservice/trips/left",
        query_shape=QueryShape.GENERAL,
    )
    calls: list[str] = []
    expected = ExecutedCypher(
        cypher="MATCH local RETURN local",
        result=QueryResult(columns=("local",), rows=({"local": 1},)),
    )
    gateway = SequenceGateway(calls, [expected])

    result = DefaultGraphQueryEngine(
        prompt_builder=RecordingPromptBuilder(calls, query),
        llm_client=RecordingLLM(
            calls,
            [LLMResponse(content="MATCH local RETURN local")],
        ),
        read_only_cypher_gateway=gateway,
        few_shot_router=RecordingRouter(calls, query),
    ).query(
        GraphQueryRequest.from_primary_agent_query(query),
        QueryContext(GraphSchema()),
    )

    assert result == expected
    assert calls == [
        "router",
        "prompt",
        "llm",
        "gateway:MATCH local RETURN local",
    ]


@pytest.mark.parametrize(
    ("query_shape", "question", "anchor", "expected_column"),
    (
        (
            QueryShape.UPSTREAM_REACHABILITY,
            "哪些上游方法能够调用到 sample.Service.run？",
            "sample.Service.run",
            "上游方法",
        ),
        (
            QueryShape.DIRECT_UPSTREAM,
            "哪些方法直接调用 sample.Service.run？",
            "sample.Service.run",
            "调用方法",
        ),
        (
            QueryShape.DIRECT_DOWNSTREAM_METHOD,
            "sample.Service.run 直接调用哪些方法？",
            "sample.Service.run",
            "被调用方法",
        ),
        (
            QueryShape.REACHABLE_ENTRY_API,
            "哪些入口 API 可以到达 sample.Service.run？",
            "sample.Service.run",
            "入口API",
        ),
        (
            QueryShape.FULL_ENTRY_CHAIN,
            "sample.Service.run 的上游调用链是什么？",
            "sample.Service.run",
            "方法路径",
        ),
        (
            QueryShape.DIRECT_REST_EGRESS,
            "sample.Service.run 直接下游 API 和服务是什么？",
            "sample.Service.run",
            "下游服务",
        ),
    ),
)
def test_engine_uses_deterministic_compiler_for_resolved_method_shapes(
    query_shape: QueryShape,
    question: str,
    anchor: str,
    expected_column: str,
) -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question=question,
        intent="查询方法图谱事实",
        required_information=(expected_column,),
        anchor=anchor,
        query_shape=query_shape,
    )
    expected = ExecutedCypher(
        cypher="compiled method query",
        result=QueryResult(columns=(expected_column,), rows=()),
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
    assert expected_column in gateway.statements[1].cypher


def test_engine_uses_deterministic_service_dependency_matrix() -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question=(
            "找出通过 MQ 向 ts-notification-service 发送消息的服务，"
            "并列出每个发送服务的 REST 下游服务"
        ),
        intent="查询发送服务及其 REST 下游服务",
        required_information=("发送服务", "下游服务"),
        anchor="ts-notification-service",
        query_shape=QueryShape.GENERAL,
    )
    expected = ExecutedCypher(
        cypher="compiled service dependency",
        result=QueryResult(columns=("发送服务", "下游服务"), rows=()),
    )
    gateway = DirectStatementGateway(expected)
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
    assert gateway.statements[0].parameters == {
        "receiverServiceName": "ts-notification-service"
    }


@pytest.mark.parametrize(
    ("question", "expected_column", "parameter"),
    (
        (
            "图中名为 email 的消息队列节点一共有多少个？",
            "队列节点数",
            {"queueName": "email"},
        ),
        (
            "列出各微服务拥有的默认消息交换机及其交换机类型。",
            "交换机类型",
            {"exchangeName": "(default)"},
        ),
        (
            "哪些微服务把 ts-order-other-service 当作 REST 调用目标？",
            "上游服务",
            {"targetServiceName": "ts-order-other-service"},
        ),
    ),
)
def test_engine_uses_deterministic_service_facts(
    question: str,
    expected_column: str,
    parameter: dict[str, str],
) -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question=question,
        intent="查询服务图谱事实",
        required_information=(expected_column,),
        anchor="事实锚点",
        query_shape=QueryShape.GENERAL,
    )
    expected = ExecutedCypher(
        cypher="compiled service fact",
        result=QueryResult(columns=(expected_column,), rows=()),
    )
    gateway = DirectStatementGateway(expected)
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
    assert gateway.statements[0].parameters == parameter


@pytest.mark.parametrize(
    ("question", "expected_column"),
    (
        (
            "AdminRouteServiceImpl 属于哪个微服务，实现了哪些接口，声明了哪些方法？",
            "方法全限定名",
        ),
        (
            "AdminRouteServiceImpl 所属微服务对外公开了哪些 API？",
            "接口路径",
        ),
    ),
)
def test_engine_uses_deterministic_class_facts(
    question: str,
    expected_column: str,
) -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question=question,
        intent="查询类结构事实",
        required_information=(expected_column,),
        anchor="AdminRouteServiceImpl",
        query_shape=QueryShape.GENERAL,
    )
    expected = ExecutedCypher(
        cypher="compiled class fact",
        result=QueryResult(columns=(expected_column,), rows=()),
    )
    gateway = DirectStatementGateway(expected)
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
    assert gateway.statements[0].parameters == {
        "classAnchor": "AdminRouteServiceImpl",
        "classSuffix": ".AdminRouteServiceImpl",
    }


@pytest.mark.parametrize(
    ("question", "expected_column"),
    (
        (
            "统计 ts-admin-basic-info-service 所有上游 API 的 HTTP 方法分布。",
            "API数量",
        ),
        (
            "统计 ts-admin-basic-info-service 直接 REST 调用的每个目标服务的调用次数。",
            "调用关系数",
        ),
        (
            "查询 ts-admin-basic-info-service 的接口实现类。",
            "实现类",
        ),
    ),
)
def test_engine_uses_deterministic_service_aggregates(
    question: str,
    expected_column: str,
) -> None:
    query = PrimaryAgentQuery(
        query_id="q1",
        question=question,
        intent="查询服务聚合事实",
        required_information=(expected_column,),
        anchor="ts-admin-basic-info-service",
        query_shape=QueryShape.GENERAL,
    )
    expected = ExecutedCypher(
        cypher="compiled service aggregate",
        result=QueryResult(columns=(expected_column,), rows=()),
    )
    gateway = DirectStatementGateway(expected)
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
    assert gateway.statements[0].parameters == {
        "serviceName": "ts-admin-basic-info-service"
    }
