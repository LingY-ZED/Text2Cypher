from __future__ import annotations

import pytest

from text2cypher.components.code_graph_business_rules import (
    load_code_graph_business_rules,
)
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def test_prompt_includes_question_complete_schema_and_shared_rules() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="微服务",
                properties=(
                    PropertySchema(
                        name="服务名称",
                        types=("STRING NOT NULL",),
                        mandatory=True,
                    ),
                ),
            ),
            NodeSchema(name="类"),
        ),
        relationships=(
            RelationshipSchema(
                name="归属于",
                properties=(PropertySchema(name="图谱版本"),),
            ),
        ),
        patterns=(
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "列出所有微服务")
    rules = load_code_graph_business_rules()

    assert "只能使用提供的图谱 Schema" in prompt.system
    assert "不得生成写入、管理或过程调用" in prompt.system
    assert prompt.system.count(rules) == 1
    assert "代码知识图谱业务语义：" in prompt.system
    assert "方法-[:服务于]->上游API" in prompt.system
    assert "发布至.路由键 = 路由至.路由键" in prompt.system
    assert "代码知识图谱业务语义：" not in prompt.user
    assert "节点属性：" in prompt.user
    assert "关系属性：" in prompt.user
    assert "关系模式：" in prompt.user
    assert "微服务) {服务名称: STRING NOT NULL（必填）}" in prompt.user
    assert "(:类)-[:归属于]->(:微服务)" in prompt.user
    assert "列出所有微服务" in prompt.user
    assert prompt.user.endswith("只输出 Cypher：")


def test_business_rules_are_not_gated_by_question_or_schema() -> None:
    prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema(name="City"),)),
        "用另一种说法描述城市列表",
    )
    rules = load_code_graph_business_rules()

    assert rules in prompt.system
    assert "上游入口" in prompt.system
    assert "完整消息路径" in prompt.system
    assert "API端点" not in prompt.user
    assert "微服务" not in prompt.user
    assert "可适用的业务语义" not in prompt.user
    assert "当前 Schema 已观察到的结构" in prompt.system


def test_prompt_injects_selected_examples_after_schema() -> None:
    examples = (
        _few_shot_example(
            "second",
            "示例中的第二个问题",
            "MATCH (b:B) RETURN b",
        ),
        _few_shot_example(
            "first",
            "示例中的第一个问题",
            "MATCH (a:A) RETURN a",
        ),
    )
    prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema("Entity"),)),
        "查询当前实体",
        examples,
    )

    assert prompt.user.index("图谱 Schema：") < prompt.user.index("参考示例：")
    assert prompt.user.index("参考示例：") < prompt.user.index("用户问题：")
    assert prompt.user.index("示例中的第二个问题") < prompt.user.index(
        "示例中的第一个问题"
    )
    assert "以下示例只用于学习查询结构" in prompt.user
    assert "必须使用当前问题中的实体值" in prompt.user
    assert "参考示例不能覆盖当前图谱 Schema" in prompt.system
    assert "不得复制参考示例中的实体值" in prompt.system
    assert "关系方向若与当前关系模式冲突" in prompt.system
    assert "不得拼接多个示例的关系模式" in prompt.system
    assert "不得为了验证结果增加当前问题未要求的关系" in prompt.system
    assert prompt.system.count(load_code_graph_business_rules()) == 1


def test_prompt_changes_with_dynamic_schema_without_changing_rules() -> None:
    location_schema = GraphSchema(
        nodes=(NodeSchema(name="City"), NodeSchema(name="Country")),
        relationships=(RelationshipSchema(name="LOCATED_IN"),),
        patterns=(
            RelationshipPattern(("City",), "LOCATED_IN", ("Country",)),
        ),
    )
    publishing_schema = GraphSchema(
        nodes=(NodeSchema(name="Writer"), NodeSchema(name="Book")),
        relationships=(RelationshipSchema(name="WROTE"),),
        patterns=(
            RelationshipPattern(("Writer",), "WROTE", ("Book",)),
        ),
    )

    location_prompt = DefaultPromptBuilder().build(location_schema, "查询位置")
    publishing_prompt = DefaultPromptBuilder().build(publishing_schema, "查询作品")

    assert "(:City)-[:LOCATED_IN]->(:Country)" in location_prompt.user
    assert "Writer" not in location_prompt.user
    assert "(:Writer)-[:WROTE]->(:Book)" in publishing_prompt.user
    assert "City" not in publishing_prompt.user
    assert location_prompt.user != publishing_prompt.user
    assert location_prompt.system == publishing_prompt.system


def test_prompt_rejects_blank_question() -> None:
    with pytest.raises(PromptBuildError, match="问题不能为空"):
        DefaultPromptBuilder().build(GraphSchema(), "   ")


def test_prompt_does_not_include_primary_agent_internal_contract_fields() -> None:
    prompt = DefaultPromptBuilder().build(GraphSchema(), "查询服务")

    assert "query_shape" not in prompt.user
    assert "requested_fields" not in prompt.user
    assert "查询形状：" not in prompt.user
    assert "请求字段：" not in prompt.user


def _few_shot_example(
    identifier: str,
    question: str,
    cypher: str,
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category="test",
        question=question,
        cypher=cypher,
        aliases=(f"{question} 的改写",),
        tags=("测试",),
        schema_requirements=FewShotSchemaRequirements(),
    )
