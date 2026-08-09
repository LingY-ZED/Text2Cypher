"""为问题拆分器提供精简、动态的 Schema 词汇摘要。"""

from __future__ import annotations

from text2cypher.domain.models import GraphSchema, PropertySchema


class SchemaSummarySerializer:
    """只描述可用标签、关系类型及其属性名，不暴露图拓扑。"""

    def serialize(self, schema: GraphSchema) -> str:
        node_lines = [
            self._render_element(f"(:{self._identifier(node.name)})", node.properties)
            for node in sorted(schema.nodes, key=lambda item: item.name)
        ] or ["- （未观察到节点标签）"]
        relationship_lines = [
            self._render_element(
                f"[:{self._identifier(relationship.name)}]",
                relationship.properties,
            )
            for relationship in sorted(
                schema.relationships,
                key=lambda item: item.name,
            )
        ] or ["- （未观察到关系类型）"]
        return "\n".join(
            (
                "节点标签与属性：",
                *node_lines,
                "",
                "关系类型与属性：",
                *relationship_lines,
            )
        )

    @classmethod
    def _render_element(
        cls,
        element: str,
        properties: tuple[PropertySchema, ...],
    ) -> str:
        names = sorted({property_schema.name for property_schema in properties})
        property_text = "、".join(cls._identifier(name) for name in names)
        return f"- {element} {{属性：{property_text or '（未观察到属性）'}}}"

    @staticmethod
    def _identifier(value: str) -> str:
        if value.isidentifier():
            return value
        return f"`{value.replace('`', '``')}`"
