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
    assert "完整调用链是一个逐行对应的意图" in prompt.system
    assert "上游为反向、下游为正向" in prompt.system
    assert "调用链表示最多五跳" in prompt.system
    assert "完整上游链默认包含入口 API" in prompt.system
    assert "完整下游链默认包含有序方法路径" in prompt.system
    assert "三个独立集合，应分别拆分" in prompt.system
    assert "第三个子问题必须原样写明" in prompt.system
    assert "‘外部服务’也不得输出该泛称" in prompt.system
    assert len(prompt.system) <= 1100


def test_decomposition_keeps_complete_call_chain_as_one_intent() -> None:
    prompt = QuestionDecompositionPromptBuilder().build(
        _external_schema(),
        "查询 getAllFood 的完整上游调用链",
        3,
    )

    assert "方法路径、入口或出口 API、服务必须留在同一子问题" in prompt.system
    assert "明确只问上游方法、入口 API 或直接下游服务时按原对象处理" in prompt.system
    assert "MQ 的发布方法、交换机、队列、消费方法和两端服务" in prompt.system


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
