from __future__ import annotations

from text2cypher.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)
from text2cypher.prompt_builder import DefaultPromptBuilder


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
        patterns=(
            RelationshipPattern(
                start_labels=("方法",),
                relationship_type="归属于",
                end_labels=("类",),
            ),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "列出所有微服务")

    assert "只能使用 Schema 中出现的节点标签、关系类型和属性名" in prompt.system
    assert "微服务 {服务名称: STRING NOT NULL required}" in prompt.user
    assert "(:方法)-[:归属于]->(:类)" in prompt.user
    assert "列出所有微服务" in prompt.user

