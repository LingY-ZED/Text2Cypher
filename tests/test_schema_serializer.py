from __future__ import annotations

from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.components.schema_serializer import SchemaSerializer
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def _serialize(schema: GraphSchema) -> str:
    graph = SchemaGraphBuilder().build(schema)
    return SchemaSerializer().serialize(schema, graph)


def test_serializer_outputs_properties_and_all_relationship_patterns() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="方法",
                properties=(
                    PropertySchema("方法名", ("STRING",), mandatory=True),
                ),
            ),
            NodeSchema(
                name="类",
                properties=(PropertySchema("限定名", ("STRING", "NULL")),),
            ),
            NodeSchema(name="微服务"),
        ),
        relationships=(
            RelationshipSchema(
                name="归属于",
                properties=(PropertySchema("图谱版本", ("STRING",)),),
            ),
        ),
        patterns=(
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        ),
    )

    serialized = _serialize(schema)

    assert "节点属性：" in serialized
    assert "- (:方法) {方法名: STRING（必填）}" in serialized
    assert "- (:类) {限定名: STRING | NULL}" in serialized
    assert "- (:微服务) {（未观察到属性）}" in serialized
    assert "关系属性：" in serialized
    assert "- [:归属于] {图谱版本: STRING}" in serialized
    assert "关系模式：" in serialized
    assert "- (:方法)-[:归属于]->(:类)" in serialized
    assert "- (:类)-[:归属于]->(:微服务)" in serialized


def test_serializer_preserves_multi_labels_and_escapes_identifiers() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="Primary Label",
                properties=(PropertySchema("display-name", ("STRING",)),),
            ),
            NodeSchema(name="Tag"),
            NodeSchema(name="Target"),
        ),
        relationships=(RelationshipSchema(name="REL-TYPE"),),
        patterns=(
            RelationshipPattern(
                ("Primary Label", "Tag"),
                "REL-TYPE",
                ("Target",),
            ),
        ),
    )

    serialized = _serialize(schema)

    assert "- (:`Primary Label`) {`display-name`: STRING}" in serialized
    assert "- [:`REL-TYPE`] {（未观察到属性）}" in serialized
    assert (
        "- (:`Primary Label`:Tag)-[:`REL-TYPE`]->(:Target)" in serialized
    )


def test_serializer_always_outputs_all_sections_for_empty_schema() -> None:
    serialized = _serialize(GraphSchema())

    assert serialized == "\n".join(
        [
            "节点属性：",
            "- （未观察到节点标签）",
            "",
            "关系属性：",
            "- （未观察到关系类型）",
            "",
            "关系模式：",
            "- （未观察到关系模式）",
        ]
    )


def test_serializer_does_not_generate_compressed_relationships() -> None:
    schema = GraphSchema(
        patterns=(
            RelationshipPattern(("A",), "R1", ("B",)),
            RelationshipPattern(("B",), "R2", ("C",)),
        )
    )

    serialized = _serialize(schema)

    assert "- (:A)-[:R1]->(:B)" in serialized
    assert "- (:B)-[:R2]->(:C)" in serialized
    assert "(:A)-[:R1]->(:C)" not in serialized
    assert "(:A)-->(:C)" not in serialized
