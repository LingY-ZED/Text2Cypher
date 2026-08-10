from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from text2cypher.domain.models import (
    CypherFailureContext,
    CypherFailureKind,
    CypherFailureSource,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    Text2CypherResponse,
)


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
