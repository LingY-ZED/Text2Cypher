from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from text2cypher.domain.iterative import (
    EvidenceBinding,
    FindCallChainActionInput,
    IterativePlan,
    KeyEntity,
    PlanDecision,
    QueryCodeGraphActionInput,
    ResolveSymbolActionInput,
    RuntimeAction,
    RuntimeToolName,
)
from text2cypher.domain.query_shapes import QueryShape


def _action(
    *,
    action_id: str = "r1a1",
    tool: RuntimeToolName = RuntimeToolName.RESOLVE_SYMBOL,
) -> RuntimeAction:
    action_input = ResolveSymbolActionInput("PaymentService.pay")
    if tool is RuntimeToolName.FIND_CALL_CHAIN:
        action_input = FindCallChainActionInput("PaymentService.pay")
    return RuntimeAction(
        action_id=action_id,
        tool=tool,
        intent="定位目标方法",
        expected_information=("目标方法",),
        action_input=action_input,
    )


def test_iterative_plan_enforces_complete_and_continue_contracts() -> None:
    complete = IterativePlan(PlanDecision.COMPLETE, ("已获得答案",), (), ())
    continuing = IterativePlan(
        PlanDecision.CONTINUE,
        (),
        ("目标方法",),
        (_action(),),
    )

    assert complete.actions == ()
    assert continuing.actions[0].action_id == "r1a1"

    with pytest.raises(ValueError, match="complete"):
        IterativePlan(PlanDecision.COMPLETE, (), ("缺失",), ())
    with pytest.raises(ValueError, match="continue"):
        IterativePlan(PlanDecision.CONTINUE, (), (), (_action(),))


def test_runtime_action_requires_matching_typed_input() -> None:
    with pytest.raises(TypeError, match="输入类型"):
        RuntimeAction(
            "r1a1",
            RuntimeToolName.FIND_CALL_CHAIN,
            "调用链",
            ("路径",),
            ResolveSymbolActionInput("PaymentService.pay"),
        )


def test_query_code_graph_rejects_full_call_chain_bypass() -> None:
    with pytest.raises(ValueError, match="find_call_chain"):
        QueryCodeGraphActionInput(
            "查询 {method} 的完整调用链",
            {"method": EvidenceBinding("o1:e1", "qualified_name")},
            query_shape=QueryShape.FULL_METHOD_CALL_CHAIN,
        )


def test_key_entity_normalizes_and_freezes_attributes() -> None:
    entity = KeyEntity(
        " o1:e1 ",
        " method ",
        {"qualified_name": "PaymentService.pay", "path": ["a", "b"]},
    )

    assert entity.entity_id == "o1:e1"
    assert entity.attributes["path"] == ("a", "b")
    with pytest.raises(TypeError):
        entity.attributes["other"] = "value"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        entity.kind = "service"  # type: ignore[misc]
