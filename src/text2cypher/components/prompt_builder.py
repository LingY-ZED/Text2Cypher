"""组件层中基于实时 Schema 构造模型提示词的实现。"""

from __future__ import annotations

from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    ChatPrompt,
    GraphSchema,
    PropertySchema,
    RelationshipPattern,
)


class DefaultPromptBuilder:
    """构造不含 Few-shot 选择的最小 Schema 约束提示词。"""

    system_instruction = (
        "你是 Neo4j Cypher 专家。根据提供的图谱 Schema 和用户问题生成一条只读 "
        "Cypher 查询。只能使用 Schema 中出现的节点标签、关系类型和属性名。"
        "不得生成写入、管理、过程调用或解释性文字。响应只能包含 Cypher。"
    )

    def build(self, schema: GraphSchema, question: str) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("问题不能为空")

        return ChatPrompt(
            system=self.system_instruction,
            user=(
                "图谱结构：\n"
                f"{self._render_schema(schema)}\n\n"
                "用户问题：\n"
                f"{normalized_question}\n\n"
                "只输出 Cypher："
            ),
        )

    @staticmethod
    def _render_properties(properties: tuple[PropertySchema, ...]) -> str:
        if not properties:
            return "（未观察到属性）"
        values = []
        for property_schema in properties:
            type_text = " | ".join(property_schema.types) or "未知类型"
            required = "（必填）" if property_schema.mandatory else ""
            values.append(f"{property_schema.name}: {type_text}{required}")
        return ", ".join(values)

    @classmethod
    def _render_pattern(cls, pattern: RelationshipPattern) -> str:
        starts = ":".join(pattern.start_labels) or "?"
        ends = ":".join(pattern.end_labels) or "?"
        return f"(:{starts})-[:{pattern.relationship_type}]->(:{ends})"

    @classmethod
    def _render_schema(cls, schema: GraphSchema) -> str:
        node_lines = [
            f"- {node.name} {{{cls._render_properties(node.properties)}}}"
            for node in schema.nodes
        ] or ["- （未观察到节点标签）"]
        relationship_lines = [
            (
                f"- {relationship.name} "
                f"{{{cls._render_properties(relationship.properties)}}}"
            )
            for relationship in schema.relationships
        ] or ["- （未观察到关系类型）"]
        pattern_lines = [
            f"- {cls._render_pattern(pattern)}" for pattern in schema.patterns
        ] or ["- （未观察到关系模式）"]
        return "\n".join(
            [
                "节点属性：",
                *node_lines,
                "关系属性：",
                *relationship_lines,
                "关系模式：",
                *pattern_lines,
            ]
        )
