from __future__ import annotations

import pytest

from text2cypher.components.question_decomposition_review import (
    QuestionDecompositionReviewPromptBuilder,
    QuestionDecompositionReviewResponseParser,
)
from text2cypher.domain.models import QuestionDecompositionReviewReason


def test_review_prompt_contains_original_and_all_candidates_without_schema() -> None:
    prompt = QuestionDecompositionReviewPromptBuilder().build(
        "查询服务的 REST 和 MQ 下游依赖",
        ("查询 REST 下游依赖", "查询 MQ 下游依赖"),
    )

    assert "查询服务的 REST 和 MQ 下游依赖" in prompt.user
    assert "1. 查询 REST 下游依赖" in prompt.user
    assert "2. 查询 MQ 下游依赖" in prompt.user
    assert "Schema" not in prompt.user
    assert "固定对象与完整筛选条件可在各项重复并独立重算" in prompt.system
    assert "没有新增、丢失或跨意图传播限定" in prompt.system
    assert "完整调用链是一个逐行对应的意图" in prompt.system
    assert "把同一链的方法、API、服务拆开" in prompt.system
    assert "无法恢复原始行" in prompt.system
    assert "三者是可重复锚点独立重算的集合" in prompt.system
    assert "泛化为‘外部服务’则返回 COVERAGE_MISMATCH" in prompt.system
    assert "返回 COVERAGE_MISMATCH" in prompt.system
    assert "合并不兼容返回形状" in prompt.system
    assert "RESULT_DEPENDENCY" in prompt.system
    assert len(prompt.system) <= 1400


def test_review_prompt_has_abstract_call_chain_semantics_without_graph_schema() -> None:
    prompt = QuestionDecompositionReviewPromptBuilder().build(
        "查询 getAllFood 的完整上游调用链",
        ("查询 getAllFood 的上游方法", "查询 getAllFood 的入口 API"),
    )

    assert "上游反向、下游正向" in prompt.system
    assert "调用链最多五跳" in prompt.system
    assert "图谱 Schema：" not in prompt.user
    assert "关系模式：" not in prompt.user


@pytest.mark.parametrize(
    ("content", "valid", "reason"),
    [
        (
            '{"valid":true,"reason":"VALID"}',
            True,
            QuestionDecompositionReviewReason.VALID,
        ),
        (
            '```json\n{"valid":false,"reason":"RESULT_DEPENDENCY"}\n```',
            False,
            QuestionDecompositionReviewReason.RESULT_DEPENDENCY,
        ),
    ],
)
def test_review_parser_accepts_supported_json(
    content: str,
    valid: bool,
    reason: QuestionDecompositionReviewReason,
) -> None:
    result = QuestionDecompositionReviewResponseParser().parse(content)

    assert result.valid is valid
    assert result.reason is reason


@pytest.mark.parametrize(
    "reason",
    [
        "CORRELATION_LOSS",
        "NOT_SELF_CONTAINED",
        "COVERAGE_MISMATCH",
        "MECHANICAL_SPLIT",
        "UNCERTAIN",
    ],
)
def test_review_parser_accepts_all_rejection_reason_codes(reason: str) -> None:
    result = QuestionDecompositionReviewResponseParser().parse(
        f'{{"valid":false,"reason":"{reason}"}}'
    )

    assert result.valid is False
    assert result.reason.value == reason


@pytest.mark.parametrize(
    "content",
    [
        "",
        "说明\n{\"valid\":true,\"reason\":\"VALID\"}",
        "[]",
        "```python\n{\"valid\":true,\"reason\":\"VALID\"}\n```",
        "{\"valid\":\"true\",\"reason\":\"VALID\"}",
        "{\"valid\":true}",
        "{\"valid\":false,\"reason\":\"UNKNOWN\"}",
        "{\"valid\":true,\"reason\":\"RESULT_DEPENDENCY\"}",
        "{\"valid\":false,\"reason\":\"VALID\"}",
    ],
)
def test_review_parser_rejects_invalid_documents(content: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        QuestionDecompositionReviewResponseParser().parse(content)


@pytest.mark.parametrize(
    "args",
    [
        ("", ("一", "二")),
        ("问题", ("一",)),
        ("问题", ("一", "二", "三", "四")),
        ("问题", ("一", "")),
        ("问题", ("一", "一")),
    ],
)
def test_review_prompt_builder_rejects_invalid_input(
    args: tuple[str, tuple[str, ...]],
) -> None:
    with pytest.raises(ValueError):
        QuestionDecompositionReviewPromptBuilder().build(*args)
