from __future__ import annotations

import pytest

from text2cypher.components.question_decomposition import (
    QuestionDecompositionPromptBuilder,
    QuestionDecompositionResponseParser,
)
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def _external_schema() -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema("Person", (PropertySchema("name", ("STRING",)),)),
            NodeSchema("Company", (PropertySchema("legal-name", ("STRING",)),)),
        ),
        relationships=(
            RelationshipSchema("WORKS-AT", (PropertySchema("since", ("INTEGER",)),)),
        ),
        patterns=(
            RelationshipPattern(("Person",), "WORKS-AT", ("Company",)),
        ),
    )


def test_decomposition_prompt_contains_complete_dynamic_schema() -> None:
    prompt = QuestionDecompositionPromptBuilder().build(
        _external_schema(),
        "查询 Alice 的任职公司和同事",
        3,
    )

    assert "节点属性：" in prompt.user
    assert "- (:Person) {name: STRING}" in prompt.user
    assert "- [:`WORKS-AT`] {since: INTEGER}" in prompt.user
    assert "- (:Person)-[:`WORKS-AT`]->(:Company)" in prompt.user
    assert "查询 Alice 的任职公司和同事" in prompt.user
    assert "最多拆成 3 个子问题" in prompt.user
    assert "只属于一个分句的限定传播给其他意图" in prompt.system
    assert "不同分组和返回形状不要合并" in prompt.system
    assert "代码知识图谱业务语义：" in prompt.system
    assert "方法-[:服务于]->上游API" in prompt.system
    assert "不得再用 `调用深度` 判断是否直接" in prompt.system
    assert "只有查询可达集合时才对 `调用` 使用可变长度遍历" in prompt.system
    assert "完整入口链可以是目标方法自身" in prompt.system
    assert "完整下游链必须用 `UNION` 分开" in prompt.system
    assert "在每个分支完整投影 `目标方法`、`方法路径`、`下游API`" in prompt.system
    assert "无论有无‘分别’二字，都是三个独立集合" in prompt.system
    assert "第三个子问题必须原样写明" in prompt.system
    assert "‘外部服务’也不得输出该泛称" in prompt.system
    assert "外部服务绝不是调用该变更方法的上游方法" in prompt.system


def test_decomposition_keeps_complete_call_chain_as_one_intent() -> None:
    prompt = QuestionDecompositionPromptBuilder().build(
        _external_schema(),
        "查询 getAllFood 的完整上游调用链",
        3,
    )

    assert "多列 API、REST、调用链和 MQ 结果表示逐行对应的事实" in prompt.system
    assert "不得把方法、API、服务、交换机或队列拆成" in prompt.system
    assert "详细消息路径固定为" in prompt.system
    assert "资源子问题必须重复 A 锚点并独立推导 B" in prompt.system


@pytest.mark.parametrize(
    "content",
    [
        '{"sub_questions":["查询上游","查询下游"]}',
        '```json\n{"sub_questions":["查询上游","查询下游"]}\n```',
        (
            '{"sub_questions":["查询上游","查询下游"],'
            '"ignored":"metadata"}'
        ),
    ],
)
def test_response_parser_accepts_supported_json_shapes(content: str) -> None:
    decomposition = QuestionDecompositionResponseParser().parse(
        content,
        "分析上下游",
        3,
    )

    assert decomposition.sub_questions == ("查询上游", "查询下游")
    assert decomposition.decomposed is True


def test_response_parser_restores_original_for_single_rewritten_question() -> None:
    decomposition = QuestionDecompositionResponseParser().parse(
        '{"sub_questions":["模型擅自改写"]}',
        "  原始问题  ",
        3,
    )

    assert decomposition.sub_questions == ("原始问题",)


@pytest.mark.parametrize(
    "content",
    [
        '解释文字\n{"sub_questions":["问题"]}',
        '{"questions":["问题"]}',
        '["问题"]',
        '```python\n{"sub_questions":["问题"]}\n```',
        '{"sub_questions":[]}',
        '{"sub_questions":[1]}',
        '{"sub_questions":["   "]}',
        '{"sub_questions":["重复","重复"]}',
        '{"sub_questions":["一","二","三","四"]}',
    ],
)
def test_response_parser_rejects_invalid_documents(content: str) -> None:
    with pytest.raises(ValueError):
        QuestionDecompositionResponseParser().parse(content, "问题", 3)


def test_prompt_and_parser_reject_invalid_max_subquestions() -> None:
    with pytest.raises(ValueError, match="2 到 3"):
        QuestionDecompositionPromptBuilder().build(
            GraphSchema(),
            "问题",
            1,
        )

    with pytest.raises(ValueError, match="2 到 3"):
        QuestionDecompositionResponseParser().parse(
            '{"sub_questions":["问题"]}',
            "问题",
            4,
        )
