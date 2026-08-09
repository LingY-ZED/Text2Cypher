from __future__ import annotations

import inspect

import pytest

from text2cypher.components.question_decomposition import (
    QuestionDecompositionPromptBuilder,
    QuestionDecompositionResponseParser,
)
from text2cypher.domain.models import (
    DependencyInput,
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
    SubQuestionPlan,
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

    assert "节点标签与属性：" in prompt.user
    assert "- (:Person) {属性：name}" in prompt.user
    assert "- [:`WORKS-AT`] {属性：since}" in prompt.user
    assert "关系模式" not in prompt.user
    assert "STRING" not in prompt.user
    assert "查询 Alice 的任职公司和同事" in prompt.user
    assert "最多拆成 3 个子问题" in prompt.user
    assert '"source_id":"q1"' in prompt.system
    assert '"columns":["实体名称"]' in prompt.user
    assert "优先返回原始问题作为唯一 q1" in prompt.system
    assert "多跳仍为单查询" in prompt.system
    assert "双父汇合" in prompt.system
    assert "不得单独查询 nodeId、内部 ID" in prompt.system
    assert "不得为了传递原问题已提供的实体值或技术标识" in prompt.system


def test_decomposition_implementation_has_no_current_schema_names() -> None:
    source = inspect.getsource(QuestionDecompositionPromptBuilder)

    for database_name in ("方法", "类", "微服务", "API端点", "归属于", "调用"):
        assert database_name not in source


@pytest.mark.parametrize(
    "content",
    [
        (
            '{"sub_questions":['
            '{"id":"q1","question":"查询上游","inputs":[]},'
            '{"id":"q2","question":"查询下游","inputs":[]}'
            ']}'
        ),
        (
            '```json\n{"sub_questions":['
            '{"id":"q1","question":"查询上游","inputs":[]},'
            '{"id":"q2","question":"查询下游","inputs":[]}'
            ']}\n```'
        ),
        (
            '{"sub_questions":['
            '{"id":"q1","question":"查询上游","inputs":[]},'
            '{"id":"q2","question":"查询下游","inputs":[]}'
            '],'
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

    assert decomposition.sub_questions == (
        SubQuestionPlan("q1", "查询上游"),
        SubQuestionPlan("q2", "查询下游"),
    )
    assert decomposition.decomposed is True


def test_response_parser_restores_original_for_single_rewritten_question() -> None:
    decomposition = QuestionDecompositionResponseParser().parse(
        '{"sub_questions":[{"id":"q1","question":"模型擅自改写","inputs":[]}]}',
        "  原始问题  ",
        3,
    )

    assert decomposition.sub_questions == (SubQuestionPlan("q1", "原始问题"),)


def test_response_parser_supports_a_dependent_sub_question() -> None:
    decomposition = QuestionDecompositionResponseParser().parse(
        (
            '{"sub_questions":['
            '{"id":"q1","question":"查询实体","inputs":[]},'
            '{"id":"q2","question":"查询实体归属","inputs":['
            '{"source_id":"q1","columns":["实体名称"]}]}'
            ']}'
        ),
        "查询实体及归属",
        3,
    )

    assert decomposition.sub_questions[1] == SubQuestionPlan(
        "q2",
        "查询实体归属",
        (DependencyInput("q1", ("实体名称",)),),
    )


def test_response_parser_rejects_implicit_technical_identifier_step() -> None:
    with pytest.raises(ValueError, match="技术标识"):
        QuestionDecompositionResponseParser().parse(
            (
                '{"sub_questions":['
                '{"id":"q1","question":"查找实体并返回业务字段","inputs":[]},'
                '{"id":"q2","question":"根据实体查询归属","inputs":['
                '{"source_id":"q1","columns":["实体标识"]}]}]}'
            ),
            "查询实体归属",
            3,
        )


@pytest.mark.parametrize(
    "content",
    [
        '解释文字\n{"sub_questions":["问题"]}',
        '{"questions":["问题"]}',
        '["问题"]',
        '```python\n{"sub_questions":["问题"]}\n```',
        '{"sub_questions":[]}',
        '{"sub_questions":[1]}',
        '{"sub_questions":[{"id":"q1","question":"   ","inputs":[]}]}',
        (
            '{"sub_questions":['
            '{"id":"q1","question":"一","inputs":[]},'
            '{"id":"q1","question":"二","inputs":[]}]}'
        ),
        (
            '{"sub_questions":['
            '{"id":"q1","question":"一","inputs":[]},'
            '{"id":"q2","question":"二","inputs":['
            '{"source_id":"q2","columns":["值"]}]}]}'
        ),
        (
            '{"sub_questions":['
            '{"id":"q1","question":"一","inputs":[]},'
            '{"id":"q2","question":"二","inputs":['
            '{"source_id":"q1","columns":["值","值"]}]}]}'
        ),
        (
            '{"sub_questions":['
            '{"id":"q1","question":"一","inputs":[]},'
            '{"id":"q2","question":"二","inputs":['
            '{"id":"q1","outputs":["值"]}]}]}'
        ),
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
