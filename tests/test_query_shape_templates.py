from __future__ import annotations

import json
from pathlib import Path

import pytest

from text2cypher.components.query_shape_templates import (
    JsonQueryShapeTemplateLoader,
    QueryShapeTemplateSelector,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.domain.errors import QueryShapeTemplateError
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)
from text2cypher.domain.query_shapes import QueryShape


def _full_chain_schema() -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema(
                "方法",
                tuple(
                    PropertySchema(name)
                    for name in ("所属类名", "方法名", "全限定名")
                ),
            ),
            NodeSchema("上游API", (PropertySchema("API路径"),)),
            NodeSchema("下游API", (PropertySchema("API路径"),)),
            NodeSchema("微服务", (PropertySchema("服务名称"),)),
        ),
        relationships=(
            RelationshipSchema("服务于"),
            RelationshipSchema("接口调用"),
            RelationshipSchema(
                "链中下一节点",
                (PropertySchema("路径签名"), PropertySchema("位置索引")),
            ),
            RelationshipSchema("下游调用"),
            RelationshipSchema("目标服务"),
            RelationshipSchema("外部调用"),
        ),
        patterns=(
            RelationshipPattern(("方法",), "服务于", ("上游API",)),
            RelationshipPattern(("方法",), "接口调用", ("方法",)),
            RelationshipPattern(("方法",), "链中下一节点", ("方法",)),
            RelationshipPattern(("方法",), "下游调用", ("下游API",)),
            RelationshipPattern(("下游API",), "目标服务", ("微服务",)),
            RelationshipPattern(("下游API",), "外部调用", ("上游API",)),
        ),
    )


def test_complete_chain_templates_are_entity_neutral_and_preserve_branches() -> None:
    templates = JsonQueryShapeTemplateLoader().load()

    assert {template.query_shape for template in templates} == {
        QueryShape.FULL_ENTRY_CHAIN,
        QueryShape.FULL_DOWNSTREAM_CHAIN,
    }
    catalog = "\n".join(template.template for template in templates)
    assert "FoodServiceImpl" not in catalog
    assert "InsidePaymentServiceImpl" not in catalog
    assert catalog.count("<TARGET_METHOD_FILTER>") == 6

    by_shape = {template.query_shape: template for template in templates}
    entry = by_shape[QueryShape.FULL_ENTRY_CHAIN].template
    downstream = by_shape[QueryShape.FULL_DOWNSTREAM_CHAIN].template
    assert entry.count("UNION") == 3
    assert "[:接口调用]" in entry
    assert "路径签名" in entry and "位置索引" in entry
    assert downstream.count("UNION") == 1
    assert "MATCH methodPath = (anchorMethod:方法)" in downstream
    assert "OPTIONAL MATCH (downstreamApi)-[:外部调用]" in downstream
    assert "relationships(methodPath)" in downstream


def test_template_selector_requires_matching_shape_and_schema() -> None:
    schema = _full_chain_schema()
    graph = SchemaGraphBuilder().build(schema)
    selector = QueryShapeTemplateSelector()

    assert selector.select(QueryShape.FULL_ENTRY_CHAIN, schema, graph) is not None
    assert (
        selector.select(QueryShape.FULL_DOWNSTREAM_CHAIN, schema, graph)
        is not None
    )
    assert selector.select(QueryShape.UPSTREAM_REACHABILITY, schema, graph) is None
    empty_schema = GraphSchema()
    assert (
        selector.select(
            QueryShape.FULL_ENTRY_CHAIN,
            empty_schema,
            SchemaGraphBuilder().build(empty_schema),
        )
        is None
    )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("<UNKNOWN_FILTER>", "占位符"),
        ("<TARGET_METHOD_FILTER> CREATE (n)", "内容不合法"),
    ],
)
def test_template_loader_rejects_unknown_placeholders_and_writes(
    tmp_path: Path,
    replacement: str,
    message: str,
) -> None:
    resource = Path(
        "src/text2cypher/resources/query_shape_templates.json"
    ).read_text(encoding="utf-8")
    payload = json.loads(resource)
    payload[0]["template"] = payload[0]["template"].replace(
        "<TARGET_METHOD_FILTER>",
        replacement,
    )
    path = tmp_path / "templates.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(QueryShapeTemplateError, match=message):
        JsonQueryShapeTemplateLoader(path).load()
