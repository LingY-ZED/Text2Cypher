from __future__ import annotations

import pytest

from text2cypher.components.code_graph_business_rule_selector import (
    BusinessRulePromptStage,
    CodeGraphBusinessRuleSelector,
)
from text2cypher.components.code_graph_business_rules import BusinessRuleModule
from text2cypher.domain.models import FewShotExample, FewShotSchemaRequirements
from text2cypher.domain.query_shapes import QueryShape


def _select(
    question: str,
    *,
    stage: BusinessRulePromptStage = (
        BusinessRulePromptStage.CYPHER_TRANSLATOR
    ),
    query_shape: QueryShape | None = None,
    examples: tuple[FewShotExample, ...] = (),
) -> tuple[BusinessRuleModule, ...]:
    return CodeGraphBusinessRuleSelector().select(
        question,
        stage=stage,
        query_shape=query_shape,
        examples=examples,
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "系统中有哪些微服务？",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
            ),
        ),
        (
            "ts-rebook-service 通过 REST 调用了哪些下游服务？",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.REST,
            ),
        ),
        (
            "谁消费 email 队列中的消息？",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.MQ,
            ),
        ),
        (
            "哪些方法直接调用 FoodServiceImpl.getAllFood？",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.METHOD_CALL,
            ),
        ),
        (
            "RebookServiceImpl.rebook 直接调用了哪些方法？",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.METHOD_CALL,
            ),
        ),
        (
            "哪些入口 API 可以到达 ConsignServiceImpl.updateConsignRecord？",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.METHOD_CALL,
                BusinessRuleModule.ENTRY_API,
            ),
        ),
        (
            "查询 WaitListOrderServiceImpl.triggerThread 到 "
            "PollThread.doPreserve 的有序方法路径。",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.METHOD_CALL,
                BusinessRuleModule.ORDERED_PATH,
            ),
        ),
        (
            "查询 getTickets 从入口 API 开始的完整上游调用链。",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.METHOD_CALL,
                BusinessRuleModule.ENTRY_API,
                BusinessRuleModule.ORDERED_PATH,
            ),
        ),
        (
            "查询 InsidePaymentServiceImpl.pay 的完整下游调用链。",
            (
                BusinessRuleModule.CORE,
                BusinessRuleModule.ANCHOR_OWNERSHIP,
                BusinessRuleModule.METHOD_CALL,
                BusinessRuleModule.ENTRY_API,
                BusinessRuleModule.REST,
                BusinessRuleModule.ORDERED_PATH,
            ),
        ),
    ],
)
def test_selects_modules_from_explicit_query_shape(
    question: str,
    expected: tuple[BusinessRuleModule, ...],
) -> None:
    assert _select(question) == expected


def test_direct_rest_egress_does_not_pull_method_call_rules() -> None:
    selected = _select(
        "InsidePaymentServiceImpl.pay 直接调用哪些下游 API 和目标服务？"
        "同时返回目标方法。"
    )

    assert selected == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.REST,
    )


def test_general_rest_aggregation_adds_only_aggregation_dependency() -> None:
    selected = _select(
        "按目标服务分组统计 ts-admin-basic-info-service 的 REST 调用数量。"
    )

    assert selected == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.REST,
        BusinessRuleModule.AGGREGATION,
    )


def test_aggregation_does_not_create_a_business_scene_on_its_own() -> None:
    assert _select("统计数量") == (BusinessRuleModule.CORE,)


@pytest.mark.parametrize(
    "stage",
    (
        BusinessRulePromptStage.FEW_SHOT_ROUTER,
        BusinessRulePromptStage.CYPHER_TRANSLATOR,
    ),
)
def test_compound_change_question_excludes_decomposition_from_consumers(
    stage: BusinessRulePromptStage,
) -> None:
    selected = _select(
        "修改 ConsignServiceImpl.insertConsignRecord 后，哪些上游方法、可到达的"
        "入口 API 和远程下游服务需要回归验证？",
        stage=stage,
    )

    assert selected == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.METHOD_CALL,
        BusinessRuleModule.ENTRY_API,
        BusinessRuleModule.REST,
    )


def test_selected_few_shot_metadata_can_supply_a_missing_scene_cue() -> None:
    example = FewShotExample(
        id="rest-example",
        category="path_query",
        question="查看服务远程调用的目标。",
        cypher="MATCH (node) RETURN node",
        aliases=("查看 REST 下游",),
        tags=("REST", "下游API", "跨服务"),
        schema_requirements=FewShotSchemaRequirements(),
    )

    selected = _select("查询某服务的依赖", examples=(example,))

    assert selected == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.REST,
    )


@pytest.mark.parametrize(
    "stage",
    (
        BusinessRulePromptStage.FEW_SHOT_ROUTER,
        BusinessRulePromptStage.CYPHER_TRANSLATOR,
    ),
)
def test_core_is_always_first_for_each_consuming_stage(
    stage: BusinessRulePromptStage,
) -> None:
    selected = _select("普通问题", stage=stage)

    assert selected == (BusinessRuleModule.CORE,)


def test_explicit_query_shape_argument_has_priority() -> None:
    selected = _select(
        "普通问题",
        query_shape=QueryShape.FULL_DOWNSTREAM_CHAIN,
    )

    assert selected == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.METHOD_CALL,
        BusinessRuleModule.ENTRY_API,
        BusinessRuleModule.REST,
        BusinessRuleModule.ORDERED_PATH,
    )


@pytest.mark.parametrize(
    ("question", "stage", "error"),
    (
        ("   ", BusinessRulePromptStage.CYPHER_TRANSLATOR, ValueError),
        ("问题", "translator", TypeError),
    ),
)
def test_rejects_invalid_selector_inputs(
    question: str,
    stage: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        _select(question, stage=stage)  # type: ignore[arg-type]
