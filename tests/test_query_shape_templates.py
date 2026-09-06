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


def _method_call_chain_schema() -> GraphSchema:
    def property_schema(name: str) -> PropertySchema:
        return PropertySchema(name)

    return GraphSchema(
        nodes=(
            NodeSchema(
                "方法",
                tuple(
                    property_schema(name)
                    for name in ("所属类名", "方法名", "全限定名", "图谱版本")
                ),
            ),
            NodeSchema(
                "上游API",
                tuple(
                    property_schema(name)
                    for name in ("API路径", "HTTP方法", "图谱版本")
                ),
            ),
            NodeSchema(
                "下游API",
                tuple(property_schema(name) for name in ("API路径", "图谱版本")),
            ),
            NodeSchema(
                "微服务",
                tuple(property_schema(name) for name in ("服务名称", "图谱版本")),
            ),
            NodeSchema("类", (property_schema("图谱版本"),)),
            NodeSchema(
                "消息交换机",
                tuple(
                    property_schema(name) for name in ("交换机名称", "图谱版本")
                ),
            ),
            NodeSchema(
                "消息队列",
                tuple(
                    property_schema(name) for name in ("队列名称", "图谱版本")
                ),
            ),
        ),
        relationships=(
            RelationshipSchema("归属于", (property_schema("图谱版本"),)),
            RelationshipSchema("服务于", (property_schema("图谱版本"),)),
            RelationshipSchema("接口调用", (property_schema("图谱版本"),)),
            RelationshipSchema(
                "链中下一节点",
                tuple(
                    property_schema(name)
                    for name in ("路径签名", "位置索引", "图谱版本")
                ),
            ),
            RelationshipSchema("下游调用", (property_schema("图谱版本"),)),
            RelationshipSchema("目标服务", (property_schema("图谱版本"),)),
            RelationshipSchema("外部调用", (property_schema("图谱版本"),)),
            RelationshipSchema(
                "发布至",
                tuple(property_schema(name) for name in ("路由键", "图谱版本")),
            ),
            RelationshipSchema(
                "路由至",
                tuple(property_schema(name) for name in ("路由键", "图谱版本")),
            ),
            RelationshipSchema("消费自", (property_schema("图谱版本"),)),
        ),
        patterns=(
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
            RelationshipPattern(("方法",), "服务于", ("上游API",)),
            RelationshipPattern(("方法",), "接口调用", ("方法",)),
            RelationshipPattern(("方法",), "链中下一节点", ("方法",)),
            RelationshipPattern(("方法",), "下游调用", ("下游API",)),
            RelationshipPattern(("下游API",), "目标服务", ("微服务",)),
            RelationshipPattern(("下游API",), "外部调用", ("上游API",)),
            RelationshipPattern(("方法",), "发布至", ("消息交换机",)),
            RelationshipPattern(("消息交换机",), "路由至", ("消息队列",)),
            RelationshipPattern(("消息队列",), "消费自", ("方法",)),
        ),
    )


def test_complete_chain_templates_are_entity_neutral_and_preserve_paths() -> None:
    templates = JsonQueryShapeTemplateLoader().load()

    assert {template.query_shape for template in templates} == {
        QueryShape.FULL_DOWNSTREAM_CHAIN,
        QueryShape.FULL_METHOD_CALL_CHAIN,
    }
    catalog = "\n".join(template.template for template in templates)
    assert "FoodServiceImpl" not in catalog
    assert "InsidePaymentServiceImpl" not in catalog
    assert catalog.count("<TARGET_METHOD_FILTER>") == 7

    by_shape = {template.query_shape: template for template in templates}
    downstream = by_shape[QueryShape.FULL_DOWNSTREAM_CHAIN].template
    assert "UNION" not in downstream
    assert "MATCH methodPath = (anchorMethod:方法)" in downstream
    assert "链中下一节点*0..5" in downstream
    assert "OPTIONAL MATCH (downstreamApi)-[:外部调用]" in downstream
    assert "relationships(methodPath)" in downstream

    method_chain = by_shape[QueryShape.FULL_METHOD_CALL_CHAIN].template
    assert method_chain.count("UNION") == 5
    assert method_chain.count("<TARGET_METHOD_FILTER>") == 6
    assert "接口调用|链中下一节点*0..10" in method_chain
    assert "publishRelation.路由键 = routeRelation.路由键" in method_chain
    assert "RETURN DISTINCT rootPath" not in method_chain
    assert " AS rootPath" not in method_chain
    assert "图谱版本 = 'v1'" not in method_chain
    assert "CALL" not in method_chain
    assert "//" not in method_chain

    expected_columns = (
        "根方法",
        "图谱版本",
        "层级",
        "链路类型",
        "源服务",
        "源API路径",
        "源HTTP方法",
        "源方法",
        "方法路径",
        "下游API路径",
        "目标服务",
        "目标API路径",
        "目标HTTP方法",
        "目标方法",
        "消息交换机",
        "消息队列",
        "路由键",
    )
    assert all(
        _return_columns(branch) == expected_columns
        for branch in method_chain.split("\nUNION\n")
    )


def test_template_selector_requires_matching_shape_and_schema() -> None:
    schema = _full_chain_schema()
    graph = SchemaGraphBuilder().build(schema)
    selector = QueryShapeTemplateSelector()

    assert selector.select(QueryShape.FULL_ENTRY_CHAIN, schema, graph) is None
    assert (
        selector.select(QueryShape.FULL_DOWNSTREAM_CHAIN, schema, graph)
        is not None
    )
    method_schema = _method_call_chain_schema()
    method_graph = SchemaGraphBuilder().build(method_schema)
    assert selector.select(
        QueryShape.FULL_METHOD_CALL_CHAIN,
        method_schema,
        method_graph,
    ) is not None
    assert selector.select(
        QueryShape.FULL_METHOD_CALL_CHAIN,
        schema,
        graph,
    ) is None
    assert selector.select(QueryShape.UPSTREAM_REACHABILITY, schema, graph) is None
    empty_schema = GraphSchema()
    assert selector.select(
        QueryShape.FULL_DOWNSTREAM_CHAIN,
        empty_schema,
        SchemaGraphBuilder().build(empty_schema),
    ) is None


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


def _return_columns(branch: str) -> tuple[str, ...]:
    projection = branch.split("RETURN DISTINCT ", maxsplit=1)[1]
    return tuple(item.rsplit(" AS ", maxsplit=1)[1] for item in projection.split(", "))
