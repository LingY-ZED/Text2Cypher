"""Few-shot 示例与实时 Schema 的精确兼容判定。"""

from __future__ import annotations

from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    SchemaGraph,
    SchemaGraphEdge,
)


class FewShotSchemaCompatibilityFilter:
    """只接受 requirements 是当前 Schema 精确子集的示例。"""

    def is_compatible(
        self,
        example: FewShotExample,
        schema: GraphSchema,
        schema_graph: SchemaGraph,
    ) -> bool:
        return self.is_requirements_compatible(
            example.schema_requirements,
            schema,
            schema_graph,
        )

    def is_requirements_compatible(
        self,
        requirements: FewShotSchemaRequirements,
        schema: GraphSchema,
        schema_graph: SchemaGraph,
    ) -> bool:
        """判断任意结构资源声明的 Schema 子集是否可用。"""

        node_properties = {
            node.name: {property_schema.name for property_schema in node.properties}
            for node in schema.nodes
        }
        relationship_properties = {
            relationship.name: {
                property_schema.name
                for property_schema in relationship.properties
            }
            for relationship in schema.relationships
        }
        if not set(requirements.node_labels).issubset(node_properties):
            return False
        if not set(requirements.relationship_types).issubset(
            relationship_properties
        ):
            return False
        if any(
            not set(properties).issubset(node_properties.get(label, set()))
            for label, properties in requirements.node_properties.items()
        ):
            return False
        if any(
            not set(properties).issubset(
                relationship_properties.get(relationship_type, set())
            )
            for relationship_type, properties
            in requirements.relationship_properties.items()
        ):
            return False

        available_edges = set(schema_graph.edges)
        return all(
            SchemaGraphEdge(
                start_labels=pattern.start_labels,
                relationship_type=pattern.relationship_type,
                end_labels=pattern.end_labels,
            )
            in available_edges
            for pattern in requirements.patterns
        )
