"""Deterministic prompt construction for the skeleton implementation."""

from __future__ import annotations

from text2cypher.errors import PromptBuildError
from text2cypher.models import (
    ChatPrompt,
    GraphSchema,
    PropertySchema,
    RelationshipPattern,
)


class DefaultPromptBuilder:
    """Builds a minimal schema-constrained prompt without few-shot selection."""

    system_instruction = (
        "你是 Neo4j Cypher 专家。根据提供的图谱 Schema 和用户问题生成一条只读 "
        "Cypher 查询。只能使用 Schema 中出现的节点标签、关系类型和属性名。"
        "不得生成写入、管理、过程调用或解释性文字。响应只能包含 Cypher。"
    )

    def build(self, schema: GraphSchema, question: str) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("question must not be blank")

        return ChatPrompt(
            system=self.system_instruction,
            user=(
                "Schema:\n"
                f"{self._render_schema(schema)}\n\n"
                "Question:\n"
                f"{normalized_question}\n\n"
                "Cypher output:"
            ),
        )

    @staticmethod
    def _render_properties(properties: tuple[PropertySchema, ...]) -> str:
        if not properties:
            return "(no observed properties)"
        values = []
        for property_schema in properties:
            type_text = " | ".join(property_schema.types) or "UNKNOWN"
            required = " required" if property_schema.mandatory else ""
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
        ] or ["- (no observed node labels)"]
        relationship_lines = [
            (
                f"- {relationship.name} "
                f"{{{cls._render_properties(relationship.properties)}}}"
            )
            for relationship in schema.relationships
        ] or ["- (no observed relationship types)"]
        pattern_lines = [
            f"- {cls._render_pattern(pattern)}" for pattern in schema.patterns
        ] or ["- (no observed relationship patterns)"]
        return "\n".join(
            [
                "Node properties:",
                *node_lines,
                "Relationship properties:",
                *relationship_lines,
                "Relationship patterns:",
                *pattern_lines,
            ]
        )
