from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from text2cypher.domain.models import (
    DependencyInput,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    SubQuestionPlan,
    Text2CypherResponse,
)


def test_question_decomposition_normalizes_and_preserves_order() -> None:
    decomposition = QuestionDecomposition(
        original_question="  分析上下游  ",
        sub_questions=(
            SubQuestionPlan("q1", "  查询上游  "),
            SubQuestionPlan("q2", "查询下游"),
        ),
    )

    assert decomposition.original_question == "分析上下游"
    assert decomposition.sub_questions == (
        SubQuestionPlan("q1", "查询上游"),
        SubQuestionPlan("q2", "查询下游"),
    )
    assert decomposition.decomposed is True


@pytest.mark.parametrize(
    ("sub_questions", "message"),
    [
        ((), "1 到 3"),
        (
            tuple(SubQuestionPlan(f"q{index}", str(index)) for index in range(1, 5)),
            "1 到 3",
        ),
        ((SubQuestionPlan("q1", "重复"), SubQuestionPlan("q2", "重复")), "不能重复"),
        ((SubQuestionPlan("q1", "改写后的问题"),), "必须保留原始问题"),
        ((SubQuestionPlan("q2", "原问题"),), "必须按 q1",
        ),
    ],
)
def test_question_decomposition_rejects_invalid_sub_questions(
    sub_questions: tuple[SubQuestionPlan, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        QuestionDecomposition("原问题", sub_questions)


def test_question_decomposition_is_immutable() -> None:
    decomposition = QuestionDecomposition.original("问题")

    with pytest.raises(FrozenInstanceError):
        decomposition.original_question = "新问题"  # type: ignore[misc]


def test_question_decomposition_supports_a_multi_parent_dag() -> None:
    decomposition = QuestionDecomposition(
        "综合查询",
        (
            SubQuestionPlan("q1", "查询服务 A"),
            SubQuestionPlan("q2", "查询服务 B"),
            SubQuestionPlan(
                "q3",
                "查询两个服务的共同依赖",
                (
                    DependencyInput("q1", ("服务名称",)),
                    DependencyInput("q2", ("服务名称",)),
                ),
            ),
        ),
    )

    assert decomposition.sub_questions[2].depends_on == ("q1", "q2")


@pytest.mark.parametrize(
    "sub_questions",
    [
        (SubQuestionPlan("q1", "问题", (DependencyInput("q1", ("值",)),)),),
        (
            SubQuestionPlan("q1", "问题"),
            SubQuestionPlan("q2", "第二问题", (DependencyInput("missing", ("值",)),)),
        ),
    ],
)
def test_question_decomposition_rejects_non_earlier_dependencies(
    sub_questions: tuple[SubQuestionPlan, ...],
) -> None:
    with pytest.raises(ValueError, match="依赖只能引用更早"):
        QuestionDecomposition("问题", sub_questions)


def test_text2cypher_response_uses_uniform_sub_query_shape() -> None:
    first = SubQueryResponse("查询上游", "RETURN 1", QueryResult((), ()))
    second = SubQueryResponse("查询下游", "RETURN 2", QueryResult((), ()))
    response = Text2CypherResponse(
        question="分析上下游",
        sub_queries=(first, second),
        formatted="{}",
    )

    assert response.sub_queries == (first, second)
    assert response.decomposed is True
    assert not hasattr(response, "cypher")
    assert not hasattr(response, "result")


def test_text2cypher_response_requires_at_least_one_sub_query() -> None:
    with pytest.raises(ValueError, match="1 到 3"):
        Text2CypherResponse("问题", (), "{}")
