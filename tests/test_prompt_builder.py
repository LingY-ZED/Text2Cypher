from __future__ import annotations

from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipSchema,
)


def test_prompt_includes_question_and_structured_schema() -> None:
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
        ),
        relationships=(
            RelationshipSchema(
                name="归属于",
                properties=(PropertySchema(name="图谱版本"),),
            ),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "列出所有微服务")

    assert "只能使用 Schema 中出现的节点标签、关系类型和属性名" in prompt.system
    assert "归属于关系方向为子节点到父节点" in prompt.system
    assert "必须使用 [:归属于*1..3]" in prompt.system
    assert "消息流类型必须为服务间消息依赖" in prompt.system
    assert "API类型为上游API过滤" in prompt.system
    assert "直接返回其目标微服务属性" in prompt.system
    assert "调用类型是关系属性" in prompt.system
    assert "sourceApi:API端点" in prompt.system
    assert "两个 API端点各自通过 [:归属于*1..3]" in prompt.system
    assert "微服务 {服务名称: STRING NOT NULL（必填）}" in prompt.user
    assert "关系属性：" in prompt.user
    assert "必须使用 [:归属于*1..3]" in prompt.user
    assert "列出所有微服务" in prompt.user
