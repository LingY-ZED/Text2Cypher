"""将 GraphSchema 与 SchemaGraph 序列化为 LLM 可读文本。"""

from __future__ import annotations

from text2cypher.domain.models import (
    GraphSchema,
    PropertySchema,
    SchemaGraph,
    SchemaGraphEdge,
)


class SchemaSerializer:
    """输出不包含数据库特定名称的通用 Schema 描述。"""

    def serialize(self, schema: GraphSchema, schema_graph: SchemaGraph) -> str:
        node_lines = [
            (
                f"- (:{self._render_identifier(node.name)}) "
                f"{{{self._render_properties(node.properties)}}}"
            )
            for node in sorted(schema.nodes, key=lambda item: item.name)
        ] or ["- （未观察到节点标签）"]
        relationship_lines = [
            (
                f"- [:{self._render_identifier(relationship.name)}] "
                f"{{{self._render_properties(relationship.properties)}}}"
            )
            for relationship in sorted(
                schema.relationships,
                key=lambda item: item.name,
            )
        ] or ["- （未观察到关系类型）"]
        pattern_lines = [
            f"- {self._render_pattern(edge)}" for edge in schema_graph.edges
        ] or ["- （未观察到关系模式）"]

        return "\n".join(
            [
                "节点属性：",
                *node_lines,
                "",
                "关系属性：",
                *relationship_lines,
                "",
                "关系模式：",
                *pattern_lines,
            ]
        )

    @classmethod
    def _render_properties(
        cls,
        properties: tuple[PropertySchema, ...],
    ) -> str:
        if not properties:
            return "（未观察到属性）"

        values = []
        for property_schema in sorted(properties, key=lambda item: item.name):
            type_text = " | ".join(property_schema.types) or "未知类型"
            required = "（必填）" if property_schema.mandatory else ""
            values.append(
                f"{cls._render_identifier(property_schema.name)}: "
                f"{type_text}{required}"
            )
        return ", ".join(values)

    @classmethod
    def _render_pattern(cls, edge: SchemaGraphEdge) -> str:
        start = cls._render_node_pattern(edge.start_labels)
        relationship_type = cls._render_identifier(edge.relationship_type)
        end = cls._render_node_pattern(edge.end_labels)
        return f"{start}-[:{relationship_type}]->{end}"

    @classmethod
    def _render_node_pattern(cls, labels: tuple[str, ...]) -> str:
        rendered_labels = ":".join(
            cls._render_identifier(label) for label in labels
        )
        return f"(:{rendered_labels})" if rendered_labels else "()"

    @staticmethod
    def _render_identifier(value: str) -> str:
        if value.isidentifier():
            return value
        escaped = value.replace("`", "``")
        return f"`{escaped}`"
