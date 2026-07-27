from __future__ import annotations

from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    RelationshipPattern,
    SchemaGraphEdge,
)


def test_builder_preserves_patterns_direction_and_isolated_nodes() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema(name="方法"),
            NodeSchema(name="类"),
            NodeSchema(name="微服务"),
            NodeSchema(name="API端点"),
            NodeSchema(name="配置文件"),
        ),
        patterns=(
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
            RelationshipPattern(("方法",), "调用", ("API端点",)),
        ),
    )

    graph = SchemaGraphBuilder().build(schema)

    assert set(graph.nodes) == {
        "方法",
        "类",
        "微服务",
        "API端点",
        "配置文件",
    }
    assert SchemaGraphEdge(("方法",), "归属于", ("类",)) in graph.edges
    assert SchemaGraphEdge(("类",), "归属于", ("方法",)) not in graph.edges
    assert SchemaGraphEdge(("方法",), "归属于", ("微服务",)) not in graph.edges
    assert graph.outgoing["方法"] == (
        SchemaGraphEdge(("方法",), "归属于", ("类",)),
        SchemaGraphEdge(("方法",), "调用", ("API端点",)),
    )
    assert graph.incoming["类"] == (
        SchemaGraphEdge(("方法",), "归属于", ("类",)),
    )
    assert graph.outgoing["配置文件"] == ()
    assert graph.incoming["配置文件"] == ()


def test_builder_keeps_multiple_relationship_types_between_same_nodes() -> None:
    schema = GraphSchema(
        patterns=(
            RelationshipPattern(("A",), "R1", ("B",)),
            RelationshipPattern(("A",), "R2", ("B",)),
            RelationshipPattern(("A",), "R1", ("B",)),
        )
    )

    graph = SchemaGraphBuilder().build(schema)

    assert graph.edges == (
        SchemaGraphEdge(("A",), "R1", ("B",)),
        SchemaGraphEdge(("A",), "R2", ("B",)),
    )
    assert graph.outgoing["A"] == graph.edges
    assert graph.incoming["B"] == graph.edges


def test_builder_handles_cycles_and_self_loops_without_traversal() -> None:
    schema = GraphSchema(
        patterns=(
            RelationshipPattern(("A",), "TO_B", ("B",)),
            RelationshipPattern(("B",), "TO_A", ("A",)),
            RelationshipPattern(("A",), "SELF", ("A",)),
        )
    )

    graph = SchemaGraphBuilder().build(schema)

    assert len(graph.edges) == 3
    assert SchemaGraphEdge(("A",), "SELF", ("A",)) in graph.outgoing["A"]
    assert SchemaGraphEdge(("A",), "SELF", ("A",)) in graph.incoming["A"]
    assert SchemaGraphEdge(("B",), "TO_A", ("A",)) in graph.incoming["A"]


def test_builder_preserves_multi_label_endpoints_as_one_edge() -> None:
    schema = GraphSchema(
        nodes=(NodeSchema(name="Isolated"),),
        patterns=(
            RelationshipPattern(
                ("Source", "Tagged"),
                "CONNECTS",
                ("Target", "Versioned"),
            ),
        ),
    )

    graph = SchemaGraphBuilder().build(schema)
    edge = SchemaGraphEdge(
        ("Source", "Tagged"),
        "CONNECTS",
        ("Target", "Versioned"),
    )

    assert graph.edges == (edge,)
    assert set(graph.nodes) == {
        "Isolated",
        "Source",
        "Tagged",
        "Target",
        "Versioned",
    }
    assert graph.outgoing["Source"] == (edge,)
    assert graph.outgoing["Tagged"] == (edge,)
    assert graph.incoming["Target"] == (edge,)
    assert graph.incoming["Versioned"] == (edge,)
