from __future__ import annotations

import inspect
import re

from text2cypher.components.schema_summary_serializer import (
    SchemaSummarySerializer,
)
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def test_summary_serializes_only_labels_relationship_types_and_property_names() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema("Beta", (PropertySchema("name", ("STRING",)),)),
            NodeSchema("Alpha", (PropertySchema("external-id", ("INTEGER",)),)),
        ),
        relationships=(
            RelationshipSchema("REL-TYPE", (PropertySchema("weight", ("FLOAT",)),)),
        ),
        patterns=(RelationshipPattern(("Beta",), "REL-TYPE", ("Alpha",)),),
    )

    result = SchemaSummarySerializer().serialize(schema)

    assert result == (
        "节点标签与属性：\n"
        "- (:Alpha) {属性：`external-id`}\n"
        "- (:Beta) {属性：name}\n\n"
        "关系类型与属性：\n"
        "- [:`REL-TYPE`] {属性：weight}"
    )
    assert "STRING" not in result
    assert "INTEGER" not in result
    assert "(:Beta)-[:" not in result


def test_summary_serializer_has_no_current_schema_specific_names() -> None:
    source = inspect.getsource(SchemaSummarySerializer)

    for database_name in ("方法", "类", "微服务", "API端点", "归属于", "调用"):
        assert re.search(rf"['\"]{database_name}['\"]", source) is None
