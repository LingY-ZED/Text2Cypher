from __future__ import annotations

import inspect

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
    assert "不得把只属于一个分句的服务、对象或范围" in prompt.system
    assert "不得把不同分组维度或不同返回形状合并" in prompt.system
    assert "按某一维度分布" in prompt.system
    assert "按另一对象分组计数" in prompt.system
    assert "必须拆成三个独立子问题" in prompt.system
    assert "全部反向上游路径" in prompt.system
    assert "变更对象直接访问的远程下游" in prompt.system
    assert "变更对象直接远程访问的哪些外部对象" in prompt.system
    assert "会丢失方向和直接性" in prompt.system
    assert "Component.action 直接远程访问" in prompt.system
    assert "哪些外部服务需要回归验证" in prompt.system
    assert "固定起点沿一条连续路径询问多个位置" in prompt.system
    assert "重复固定起点和完整路径前缀" in prompt.system


def test_decomposition_implementation_has_no_current_schema_names() -> None:
    source = inspect.getsource(QuestionDecompositionPromptBuilder)

    for database_name in ("方法", "类", "微服务", "API端点", "归属于", "调用"):
        assert database_name not in source


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
