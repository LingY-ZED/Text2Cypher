from __future__ import annotations

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def _schema(
    *,
    patterns: tuple[RelationshipPattern, ...] = (
        RelationshipPattern(("A",), "R", ("B",)),
        RelationshipPattern(("B",), "R", ("C",)),
    ),
) -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema("A", (PropertySchema("shared"), PropertySchema("a_only"))),
            NodeSchema("B", (PropertySchema("shared"),)),
            NodeSchema("C", (PropertySchema("c_only"),)),
            NodeSchema("Tagged"),
            NodeSchema("Versioned"),
        ),
        relationships=(
            RelationshipSchema(
                "R",
                (PropertySchema("kind"), PropertySchema("weight")),
            ),
            RelationshipSchema("R2", (PropertySchema("kind"),)),
        ),
        patterns=patterns,
    )


def _example(
    requirements: FewShotSchemaRequirements,
    *,
    identifier: str = "example",
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category="test",
        question="查询测试结构",
        cypher="MATCH (a:A) RETURN a",
        aliases=("测试查询",),
        tags=("测试",),
        schema_requirements=requirements,
    )


def _is_compatible(
    requirements: FewShotSchemaRequirements,
    schema: GraphSchema | None = None,
) -> bool:
    current_schema = schema or _schema()
    graph = SchemaGraphBuilder().build(current_schema)
    return FewShotSchemaCompatibilityFilter().is_compatible(
        _example(requirements),
        current_schema,
        graph,
    )


def test_filter_accepts_exact_schema_subset() -> None:
    requirements = FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R",),
        node_properties={"A": ("shared", "a_only"), "B": ("shared",)},
        relationship_properties={"R": ("kind",)},
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )

    assert _is_compatible(requirements)


def test_filter_rejects_missing_node_or_relationship_type() -> None:
    missing_node = FewShotSchemaRequirements(node_labels=("Missing",))
    missing_relationship = FewShotSchemaRequirements(
        relationship_types=("MISSING",)
    )

    assert not _is_compatible(missing_node)
    assert not _is_compatible(missing_relationship)


def test_filter_checks_property_ownership_not_only_property_name() -> None:
    wrong_node_owner = FewShotSchemaRequirements(
        node_labels=("B",),
        node_properties={"B": ("a_only",)},
    )
    wrong_relationship_owner = FewShotSchemaRequirements(
        relationship_types=("R2",),
        relationship_properties={"R2": ("weight",)},
    )

    assert not _is_compatible(wrong_node_owner)
    assert not _is_compatible(wrong_relationship_owner)


def test_filter_never_reverses_or_compresses_patterns() -> None:
    reversed_edge = FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R",),
        patterns=(RelationshipPattern(("B",), "R", ("A",)),),
    )
    compressed_edge = FewShotSchemaRequirements(
        node_labels=("A", "C"),
        relationship_types=("R",),
        patterns=(RelationshipPattern(("A",), "R", ("C",)),),
    )

    assert not _is_compatible(reversed_edge)
    assert not _is_compatible(compressed_edge)


def test_filter_requires_relationship_type_on_exact_endpoint_pair() -> None:
    requirements = FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R2",),
        patterns=(RelationshipPattern(("A",), "R2", ("B",)),),
    )

    assert not _is_compatible(requirements)


def test_filter_compares_multi_label_patterns_without_expansion() -> None:
    current_schema = _schema(
        patterns=(
            RelationshipPattern(
                ("A", "Tagged"),
                "R",
                ("B", "Versioned"),
            ),
        )
    )
    exact = FewShotSchemaRequirements(
        node_labels=("A", "Tagged", "B", "Versioned"),
        relationship_types=("R",),
        patterns=(
            RelationshipPattern(
                ("A", "Tagged"),
                "R",
                ("B", "Versioned"),
            ),
        ),
    )
    expanded_subset = FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R",),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )

    assert _is_compatible(exact, current_schema)
    assert not _is_compatible(expanded_subset, current_schema)


def test_filter_rejects_required_pattern_for_empty_schema() -> None:
    empty_schema = GraphSchema()
    requirements = FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R",),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )

    assert not _is_compatible(requirements, empty_schema)


def test_filter_allows_requirement_free_general_example() -> None:
    assert _is_compatible(FewShotSchemaRequirements(), GraphSchema())
