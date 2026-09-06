"""受限地渲染完整方法调用链评测 Oracle 与冻结快照。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from text2cypher.components.query_shape_templates import JsonQueryShapeTemplateLoader
from text2cypher.domain.query_shapes import QueryShape

CALL_CHAIN_COLUMNS = (
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

_TEMPLATE_ID = "full-method-call-chain-structure"
_METHOD_KEYS = frozenset({"kind", "qualified_name"})
_ENTRY_API_KEYS = frozenset({"kind", "http_method", "api_path", "service_name"})


def render_call_chain_oracle(specification: object) -> str:
    """把受限锚点规格渲染为一条完整调用链只读 Cypher。

    评测数据不能存放任意 Cypher 片段。这里仅支持方法全限定名，或由
    HTTP 方法、API 路径与服务名称共同限定的入口方法；两者都使用与生产
    Prompt 相同的查询形状模板。
    """

    specification = _mapping(specification, "oracle_template")
    if set(specification) != {"id", "anchor"}:
        raise ValueError("oracle_template 只能包含 id 和 anchor")
    if specification["id"] != QueryShape.FULL_METHOD_CALL_CHAIN.value:
        raise ValueError("oracle_template 必须是完整方法调用链形状")

    anchor = _mapping(specification["anchor"], "oracle_template.anchor")
    predicate = _anchor_predicate(anchor)
    template = next(
        item.template
        for item in JsonQueryShapeTemplateLoader().load()
        if item.id == _TEMPLATE_ID
    )
    rendered = template.replace("<TARGET_METHOD_FILTER>", predicate)
    if "<TARGET_METHOD_FILTER>" in rendered:
        raise ValueError("完整调用链 Oracle 含有未替换的锚点占位符")
    return rendered


def expand_call_chain_snapshot(
    specification: object,
    columns: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """将去重后的调用链快照还原为完整、稳定的表格行。"""

    specification = _mapping(specification, "call_chain_snapshot")
    if set(specification) != {"defaults", "rows"}:
        raise ValueError("call_chain_snapshot 只能包含 defaults 和 rows")
    defaults = _mapping(specification["defaults"], "call_chain_snapshot.defaults")
    rows = specification["rows"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("call_chain_snapshot.rows 必须是非空对象列表")

    required_columns = set(columns)
    unknown_defaults = set(defaults) - required_columns
    if unknown_defaults:
        raise ValueError("call_chain_snapshot.defaults 包含未知列")

    expanded: list[dict[str, Any]] = []
    for index, value in enumerate(rows):
        row = _mapping(value, f"call_chain_snapshot.rows[{index}]")
        unknown_columns = set(row) - required_columns
        if unknown_columns:
            raise ValueError("call_chain_snapshot.rows 包含未知列")
        expanded_row = {**defaults, **row}
        missing_columns = required_columns - set(expanded_row)
        if missing_columns:
            raise ValueError("call_chain_snapshot 的行缺少统一表格列")
        expanded.append(expanded_row)
    return tuple(expanded)


def _anchor_predicate(anchor: Mapping[str, Any]) -> str:
    kind = anchor.get("kind")
    if kind == "method":
        _require_exact_keys(anchor, _METHOD_KEYS, "method 锚点")
        return f"anchorMethod.全限定名 = {_literal(anchor['qualified_name'])}"
    if kind == "entry_api":
        _require_exact_keys(anchor, _ENTRY_API_KEYS, "entry_api 锚点")
        return " ".join(
            (
                "EXISTS {",
                "MATCH (anchorMethod)-[:服务于]->(api:上游API)",
                "MATCH (anchorMethod)-[:归属于]->(:类)-[:归属于]->(service:微服务)",
                "WHERE",
                f"api.HTTP方法 = {_literal(anchor['http_method'])}",
                "AND",
                f"api.API路径 = {_literal(anchor['api_path'])}",
                "AND",
                f"service.服务名称 = {_literal(anchor['service_name'])}",
                "}",
            )
        )
    raise ValueError("完整调用链 Oracle 锚点 kind 必须是 method 或 entry_api")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是对象")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} 字段不完整或包含未知字段")
    for key in expected - {"kind"}:
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"{name}.{key} 必须是非空字符串")


def _literal(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("完整调用链 Oracle 的锚点值必须是非空字符串")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
