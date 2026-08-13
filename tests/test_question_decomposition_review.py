from __future__ import annotations

import inspect

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
    assert "完整重复原始问题中的固定对象和筛选条件并独立重算" in prompt.system
    assert "固定对象上的独立多意图不做此检查" in prompt.system
    assert "跨意图传播对象、范围和直接性限定" in prompt.system
    assert "变更对象作为发起方、直接、远程" in prompt.system
    assert "哪些外部对象需要回归" in prompt.system
    assert "必须返回 COVERAGE_MISMATCH" in prompt.system
    assert "哪些外部服务需要回归验证" in prompt.system
    assert "合并互不兼容的分组和返回形状" in prompt.system
    assert "按维度分布、按另一对象分组计数和实体明细" in prompt.system
    assert "优先于其他接受规则的强制拒绝" in prompt.system
    assert "固定起点沿一条连续路径询问多个位置" in prompt.system
    assert "重复固定起点和完整路径前缀" in prompt.system
    assert "RESULT_DEPENDENCY" in prompt.system


def test_review_prompt_builder_has_no_current_schema_names() -> None:
    source = inspect.getsource(QuestionDecompositionReviewPromptBuilder)

    for database_name in ("方法", "类", "微服务", "API端点", "归属于", "调用"):
        assert database_name not in source


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
