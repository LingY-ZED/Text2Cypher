from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from text2cypher.domain.models import (
    CypherFailureContext,
    CypherFailureKind,
    CypherFailureSource,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryResult,
    QuestionDecomposition,
    QuestionDecompositionReview,
    QuestionDecompositionReviewReason,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
    SubQueryResponse,
    Text2CypherResponse,
)


@pytest.mark.parametrize(
    "reason",
    [
        QuestionDecompositionReviewReason.RESULT_DEPENDENCY,
        QuestionDecompositionReviewReason.CORRELATION_LOSS,
        QuestionDecompositionReviewReason.NOT_SELF_CONTAINED,
        QuestionDecompositionReviewReason.COVERAGE_MISMATCH,
        QuestionDecompositionReviewReason.MECHANICAL_SPLIT,
        QuestionDecompositionReviewReason.UNCERTAIN,
    ],
)
def test_question_decomposition_review_accepts_rejection_reasons(
    reason: QuestionDecompositionReviewReason,
) -> None:
    review = QuestionDecompositionReview(False, reason)

    assert review.valid is False
    assert review.reason is reason


def test_question_decomposition_review_accepts_valid_result() -> None:
    review = QuestionDecompositionReview(
        True,
        QuestionDecompositionReviewReason.VALID,
    )

    assert review.valid is True


@pytest.mark.parametrize(
    ("valid", "reason"),
    [
        (True, QuestionDecompositionReviewReason.RESULT_DEPENDENCY),
        (False, QuestionDecompositionReviewReason.VALID),
        (1, QuestionDecompositionReviewReason.VALID),
        ("true", QuestionDecompositionReviewReason.VALID),
    ],
)
def test_question_decomposition_review_rejects_invalid_state(
    valid: object,
    reason: QuestionDecompositionReviewReason,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        QuestionDecompositionReview(valid, reason)  # type: ignore[arg-type]


def test_question_decomposition_review_is_immutable() -> None:
    review = QuestionDecompositionReview(
        True,
        QuestionDecompositionReviewReason.VALID,
    )

    with pytest.raises(FrozenInstanceError):
        review.valid = False  # type: ignore[misc]


def test_result_summary_accepts_llm_and_template_states() -> None:
    llm_summary = ResultSummary("  已查询到服务。  ", ResultSummaryMode.LLM)
    template_summary = ResultSummary(
        "未查询到匹配数据。",
        ResultSummaryMode.TEMPLATE,
        ResultSummaryFallbackReason.EMPTY_RESULT,
    )

    assert llm_summary.answer == "已查询到服务。"
    assert llm_summary.fallback_reason is None
    assert template_summary.mode is ResultSummaryMode.TEMPLATE


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (ResultSummaryMode.LLM, ResultSummaryFallbackReason.DISABLED),
        (ResultSummaryMode.TEMPLATE, None),
        ("llm", None),
        (ResultSummaryMode.LLM, "disabled"),
    ],
)
def test_result_summary_rejects_inconsistent_state(
    mode: object,
    reason: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        ResultSummary("答案", mode, reason)  # type: ignore[arg-type]


def test_cypher_failure_context_normalizes_and_is_immutable() -> None:
    context = CypherFailureContext(
        kind=CypherFailureKind.VALIDATION,
        source=CypherFailureSource.NEO4J,
        message="  Invalid input  ",
        code="  Neo.ClientError.Statement.SyntaxError  ",
        line=1,
        column=2,
        offset=0,
    )

    assert context.message == "Invalid input"
    assert context.code == "Neo.ClientError.Statement.SyntaxError"
    with pytest.raises(FrozenInstanceError):
        context.message = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "parse"}, "CypherFailureKind"),
        ({"source": "local"}, "CypherFailureSource"),
        ({"line": -1}, "非负整数"),
        ({"column": True}, "非负整数"),
    ],
)
def test_cypher_failure_context_rejects_invalid_values(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "kind": CypherFailureKind.PARSE,
        "source": CypherFailureSource.LOCAL,
        "message": "错误",
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError), match=message):
        CypherFailureContext(**values)  # type: ignore[arg-type]


def test_question_decomposition_normalizes_and_preserves_order() -> None:
    decomposition = QuestionDecomposition(
        original_question="  分析上下游  ",
        sub_questions=("  查询上游  ", "查询下游"),
    )

    assert decomposition.original_question == "分析上下游"
    assert decomposition.sub_questions == ("查询上游", "查询下游")
    assert decomposition.decomposed is True


@pytest.mark.parametrize(
    ("sub_questions", "message"),
    [
        ((), "1 到 3"),
        (("一", "二", "三", "四"), "1 到 3"),
        (("重复", "重复"), "不能重复"),
        (("改写后的问题",), "必须保留原始问题"),
        (("   ",), "不能为空"),
    ],
)
def test_question_decomposition_rejects_invalid_sub_questions(
    sub_questions: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        QuestionDecomposition("原问题", sub_questions)


def test_question_decomposition_is_immutable() -> None:
    decomposition = QuestionDecomposition("问题", ("问题",))

    with pytest.raises(FrozenInstanceError):
        decomposition.original_question = "新问题"  # type: ignore[misc]


def test_primary_agent_plan_normalizes_and_projects_sub_questions() -> None:
    plan = PrimaryAgentPlan(
        original_question="  分析上下游  ",
        analysis_summary="  需要分别取得上游和下游。  ",
        queries=(
            PrimaryAgentQuery(
                "q1",
                "  查询上游  ",
                "  定位上游  ",
                ("  上游调用者  ",),
            ),
            PrimaryAgentQuery(
                "q2",
                "查询下游",
                "定位下游",
                ("下游调用目标",),
            ),
        ),
    )

    assert plan.original_question == "分析上下游"
    assert plan.analysis_summary == "需要分别取得上游和下游。"
    assert plan.sub_questions == ("查询上游", "查询下游")
    assert plan.decomposed is True


def test_primary_agent_plan_fallback_preserves_original_question() -> None:
    plan = PrimaryAgentPlan.fallback("  查询服务  ")

    assert plan.analysis_summary == "使用原始问题执行单次检索"
    assert plan.queries[0].query_id == "q1"
    assert plan.sub_questions == ("查询服务",)
    assert plan.decomposed is False


@pytest.mark.parametrize(
    ("queries", "message"),
    [
        ((), "1 到 3"),
        (
            (
                PrimaryAgentQuery("q2", "问题", "意图", ("信息",)),
            ),
            "q1 起连续编号",
        ),
        (
            (
                PrimaryAgentQuery("q1", "改写问题", "意图", ("信息",)),
            ),
            "保留原始问题",
        ),
        (
            (
                PrimaryAgentQuery("q1", "重复", "意图一", ("信息一",)),
                PrimaryAgentQuery("q2", "重复", "意图二", ("信息二",)),
            ),
            "不能重复",
        ),
    ],
)
def test_primary_agent_plan_rejects_invalid_queries(
    queries: tuple[PrimaryAgentQuery, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        PrimaryAgentPlan("原始问题", "分析摘要", queries)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query_id": 1}, "查询 ID必须是字符串"),
        ({"required_information": ["信息"]}, "所需信息必须是元组"),
        ({"required_information": ()}, "所需信息不能为空"),
        ({"required_information": ("重复", "重复")}, "所需信息不能重复"),
    ],
)
def test_primary_agent_query_rejects_invalid_values(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "query_id": "q1",
        "question": "问题",
        "intent": "意图",
        "required_information": ("信息",),
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError), match=message):
        PrimaryAgentQuery(**values)  # type: ignore[arg-type]


def test_primary_agent_models_are_immutable() -> None:
    query = PrimaryAgentQuery("q1", "问题", "意图", ("信息",))
    plan = PrimaryAgentPlan("问题", "分析摘要", (query,))

    with pytest.raises(FrozenInstanceError):
        query.intent = "其他意图"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.analysis_summary = "其他摘要"  # type: ignore[misc]


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
    assert response.summary is None
    assert not hasattr(response, "cypher")
    assert not hasattr(response, "result")


def test_text2cypher_response_requires_at_least_one_sub_query() -> None:
    with pytest.raises(ValueError, match="1 到 3"):
        Text2CypherResponse("问题", (), "{}")


def test_text2cypher_response_rejects_unknown_summary_type() -> None:
    with pytest.raises(TypeError, match="ResultSummary"):
        Text2CypherResponse(
            "问题",
            (SubQueryResponse("问题", "RETURN 1", QueryResult((), ())),),
            "{}",
            "摘要",  # type: ignore[arg-type]
        )
