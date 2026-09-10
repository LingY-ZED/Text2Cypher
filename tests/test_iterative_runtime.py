from __future__ import annotations

from collections.abc import Iterable

from text2cypher.domain.iterative import (
    EvidenceBinding,
    FindCallChainActionInput,
    IterativePlan,
    IterativeStopReason,
    ObservationStatus,
    PlanDecision,
    QueryCodeGraphActionInput,
    ResolveSymbolActionInput,
    RuntimeAction,
    RuntimeToolName,
)
from text2cypher.domain.models import (
    ExecutedCypher,
    GraphQueryRequest,
    GraphSchema,
    QueryContext,
    QueryResult,
    ResolvedMethod,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
)
from text2cypher.runtime.iterative import IterativeRuntime


def _continue(*actions: RuntimeAction, missing: str = "缺失信息") -> IterativePlan:
    return IterativePlan(PlanDecision.CONTINUE, (), (missing,), actions)


def _complete(*, obtained: tuple[str, ...] = ("已获得答案",)) -> IterativePlan:
    return IterativePlan(PlanDecision.COMPLETE, obtained, (), ())


def _resolve_action(action_id: str, anchor: str) -> RuntimeAction:
    return RuntimeAction(
        action_id,
        RuntimeToolName.RESOLVE_SYMBOL,
        "解析目标方法",
        ("全限定名",),
        ResolveSymbolActionInput(anchor),
    )


def _chain_action(
    action_id: str,
    anchor: str | EvidenceBinding,
) -> RuntimeAction:
    return RuntimeAction(
        action_id,
        RuntimeToolName.FIND_CALL_CHAIN,
        "查询完整调用链",
        ("调用链",),
        FindCallChainActionInput(anchor),
    )


def _query_action(action_id: str, question: str = "查询目标服务") -> RuntimeAction:
    return RuntimeAction(
        action_id,
        RuntimeToolName.QUERY_CODE_GRAPH,
        "查询目标服务",
        ("服务",),
        QueryCodeGraphActionInput(question),
    )


class _SchemaTool:
    def __init__(self) -> None:
        self.calls = 0
        self.schema = GraphSchema()

    def get_schema(self) -> GraphSchema:
        self.calls += 1
        return self.schema


class _SequencePlanner:
    def __init__(self, plans: Iterable[IterativePlan]) -> None:
        self._plans = iter(plans)
        self.contexts = []

    def plan(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return next(self._plans)


class _Answerer:
    def __init__(self) -> None:
        self.contexts = []

    def answer(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return ResultSummary(
            "答案",
            ResultSummaryMode.TEMPLATE,
            ResultSummaryFallbackReason.DISABLED,
        )


class _Resolver:
    def __init__(self) -> None:
        self.anchors: list[str] = []

    def resolve_symbol(
        self,
        anchor: str,
        **kwargs: object,
    ) -> tuple[ResolvedMethod, ...]:
        del kwargs
        self.anchors.append(anchor)
        return (
            ResolvedMethod(
                "sample.PaymentService.pay",
                "pay",
                "sample.PaymentService",
                "payment-service",
                "v1",
            ),
        )


class _CallChainTool:
    def __init__(self) -> None:
        self.anchors: list[str] = []

    def execute(self, anchor: str, **kwargs: object) -> ExecutedCypher | None:
        del kwargs
        self.anchors.append(anchor)
        return ExecutedCypher(
            "RETURN chain",
            QueryResult(("方法路径",), ({"方法路径": [anchor]},)),
        )


class _QueryTool:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.requests: list[GraphQueryRequest] = []
        self.contexts: list[QueryContext] = []

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher:
        self.requests.append(request)
        self.contexts.append(context)
        if self.failure is not None:
            raise self.failure
        return ExecutedCypher(
            "RETURN service",
            QueryResult(("服务",), ({"服务": "payment-service"},)),
        )


def _runtime(
    planner: _SequencePlanner,
    answerer: _Answerer,
    *,
    schema_tool: _SchemaTool | None = None,
    resolver: _Resolver | None = None,
    chain_tool: _CallChainTool | None = None,
    query_tool: _QueryTool | None = None,
    max_rounds: int = 3,
    max_actions: int = 9,
) -> IterativeRuntime:
    return IterativeRuntime(
        schema_tool=schema_tool or _SchemaTool(),
        resolve_symbol_tool=resolver or _Resolver(),
        call_chain_tool=chain_tool or _CallChainTool(),
        query_code_graph_tool=query_tool or _QueryTool(),
        planner=planner,
        answerer=answerer,
        max_rounds=max_rounds,
        max_actions=max_actions,
    )


def test_iterative_runtime_completes_after_one_execution_round() -> None:
    schema_tool = _SchemaTool()
    planner = _SequencePlanner(
        (_continue(_resolve_action("r1a1", "Payment.pay")), _complete())
    )
    answerer = _Answerer()
    resolver = _Resolver()

    run = _runtime(
        planner,
        answerer,
        schema_tool=schema_tool,
        resolver=resolver,
    ).run("  分析 Payment.pay  ")

    assert run.stop_reason is IterativeStopReason.COMPLETE
    assert run.rounds_executed == 1
    assert run.actions_consumed == 1
    assert schema_tool.calls == 1
    assert resolver.anchors == ["Payment.pay"]
    assert len(planner.contexts) == 2
    assert len(answerer.contexts) == 1
    assert run.question == "分析 Payment.pay"


def test_iterative_runtime_uses_previous_observation_binding_in_second_round() -> None:
    planner = _SequencePlanner(
        (
            _continue(_resolve_action("r1a1", "Payment.pay")),
            _continue(
                _chain_action(
                    "r2a1",
                    EvidenceBinding("o1:e1", "qualified_name"),
                ),
                missing="调用链",
            ),
            _complete(),
        )
    )
    answerer = _Answerer()
    chain_tool = _CallChainTool()

    run = _runtime(planner, answerer, chain_tool=chain_tool).run("分析调用链")

    assert run.stop_reason is IterativeStopReason.COMPLETE
    assert run.rounds_executed == 2
    assert chain_tool.anchors == ["sample.PaymentService.pay"]
    assert [item.status for item in run.observations] == [
        ObservationStatus.SUCCESS,
        ObservationStatus.SUCCESS,
    ]


def test_iterative_runtime_blocks_repeated_resolved_action() -> None:
    planner = _SequencePlanner(
        (
            _continue(_query_action("r1a1")),
            _continue(_query_action("r2a1")),
        )
    )
    answerer = _Answerer()
    query_tool = _QueryTool()

    run = _runtime(planner, answerer, query_tool=query_tool).run("查询服务")

    assert run.stop_reason is IterativeStopReason.REPEATED_ACTIONS
    assert len(query_tool.requests) == 1
    assert run.observations[-1].status is ObservationStatus.DUPLICATE
    assert run.actions_consumed == 2


def test_iterative_runtime_stops_when_action_budget_is_exhausted() -> None:
    planner = _SequencePlanner(
        (
            _continue(
                _resolve_action("r1a1", "Payment.pay"),
                _query_action("r1a2"),
            ),
        )
    )
    answerer = _Answerer()
    resolver = _Resolver()
    query_tool = _QueryTool()

    run = _runtime(
        planner,
        answerer,
        resolver=resolver,
        query_tool=query_tool,
        max_actions=1,
    ).run("查询服务")

    assert run.stop_reason is IterativeStopReason.ACTION_BUDGET_EXHAUSTED
    assert resolver.anchors == ["Payment.pay"]
    assert query_tool.requests == []
    assert run.actions_consumed == 1
    assert len(answerer.contexts) == 1


def test_iterative_runtime_replans_after_tool_failure_without_replaying_success(
) -> None:
    planner = _SequencePlanner(
        (
            _continue(_query_action("r1a1")),
            _continue(_resolve_action("r2a1", "Payment.pay")),
            _complete(),
        )
    )
    answerer = _Answerer()
    query_tool = _QueryTool(failure=RuntimeError("database unavailable"))
    resolver = _Resolver()

    run = _runtime(
        planner,
        answerer,
        query_tool=query_tool,
        resolver=resolver,
    ).run("查询服务")

    assert run.stop_reason is IterativeStopReason.COMPLETE
    assert len(query_tool.requests) == 1
    assert resolver.anchors == ["Payment.pay"]
    assert run.observations[0].status is ObservationStatus.FAILED
    assert planner.contexts[1].observations[0].error_type == "RuntimeError"
    assert run.actions_consumed == 2


def test_iterative_runtime_stops_after_consecutive_rounds_without_evidence() -> None:
    planner = _SequencePlanner(
        (
            _continue(_query_action("r1a1", "查询服务 A")),
            _continue(_query_action("r2a1", "查询服务 B")),
        )
    )
    answerer = _Answerer()
    query_tool = _QueryTool(failure=RuntimeError("database unavailable"))

    run = _runtime(planner, answerer, query_tool=query_tool).run("查询服务")

    assert run.stop_reason is IterativeStopReason.NO_NEW_EVIDENCE
    assert len(query_tool.requests) == 2
    assert len(planner.contexts) == 2


def test_iterative_runtime_records_invalid_binding_without_calling_tool() -> None:
    planner = _SequencePlanner(
        (
            _continue(
                _chain_action(
                    "r1a1",
                    EvidenceBinding("o99:e1", "qualified_name"),
                )
            ),
            _continue(_query_action("r2a1")),
        )
    )
    answerer = _Answerer()
    chain_tool = _CallChainTool()
    query_tool = _QueryTool(failure=RuntimeError("database unavailable"))

    run = _runtime(
        planner,
        answerer,
        chain_tool=chain_tool,
        query_tool=query_tool,
    ).run("查询服务")

    assert chain_tool.anchors == []
    assert run.observations[0].status is ObservationStatus.FAILED
    assert run.observations[0].error_type == "BindingResolutionError"


def test_query_tool_internal_recovery_remains_one_outer_action() -> None:
    class _RecoveringQueryTool(_QueryTool):
        def __init__(self) -> None:
            super().__init__()
            self.recovery_attempts = 0

        def query(
            self,
            request: GraphQueryRequest,
            context: QueryContext,
        ) -> ExecutedCypher:
            self.recovery_attempts += 2
            return super().query(request, context)

    planner = _SequencePlanner(
        (_continue(_query_action("r1a1")), _complete())
    )
    answerer = _Answerer()
    query_tool = _RecoveringQueryTool()

    run = _runtime(planner, answerer, query_tool=query_tool).run("查询服务")

    assert query_tool.recovery_attempts == 2
    assert len(query_tool.requests) == 1
    assert run.actions_consumed == 1
    assert run.rounds_executed == 1
    assert run.stop_reason is IterativeStopReason.COMPLETE
