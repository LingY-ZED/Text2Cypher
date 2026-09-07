"""受限地渲染完整方法调用链评测 Oracle 与冻结快照。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
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

LOCAL_CALL_CHAIN_COLUMNS = (
    "根方法",
    "图谱版本",
    "源服务",
    "源API路径",
    "源HTTP方法",
    "方法路径",
    "叶子方法",
)
REST_CALL_CHAIN_COLUMNS = (
    "根方法",
    "图谱版本",
    "REST层级",
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
)
MQ_CALL_CHAIN_COLUMNS = (
    "根方法",
    "图谱版本",
    "源服务",
    "源API路径",
    "源HTTP方法",
    "发布方法",
    "发布方法路径",
    "消息交换机",
    "消息队列",
    "路由键",
    "目标服务",
    "消费方法",
    "消费者方法路径",
)


class CallChainView(StrEnum):
    """The three independently executable complete-call-chain views."""

    LOCAL = "local"
    REST = "rest"
    MQ = "mq"


CALL_CHAIN_VIEW_COLUMNS = {
    CallChainView.LOCAL: LOCAL_CALL_CHAIN_COLUMNS,
    CallChainView.REST: REST_CALL_CHAIN_COLUMNS,
    CallChainView.MQ: MQ_CALL_CHAIN_COLUMNS,
}

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


def render_call_chain_view_oracle(
    specification: object,
    view: CallChainView,
) -> str:
    """Render one independent local, REST, or MQ Oracle statement."""

    view = CallChainView(view)
    branches = render_call_chain_oracle(specification).split("\nUNION\n")
    if len(branches) != 6:
        raise ValueError("完整调用链模板必须包含六个物理分支")
    if view is CallChainView.LOCAL:
        return _replace_return(
            branches[0],
            "RETURN DISTINCT anchorMethod.全限定名 AS 根方法, "
            "graphVersion AS 图谱版本, sourceService.服务名称 AS 源服务, "
            "sourceApi.API路径 AS 源API路径, sourceApi.HTTP方法 AS 源HTTP方法, "
            "[method IN nodes(rootPath) | method.全限定名] AS 方法路径, "
            "leafMethod.全限定名 AS 叶子方法",
        )
    if view is CallChainView.REST:
        first = _replace_return(
            branches[1],
            _rest_return(level=1, downstream_prefix="", target_prefix=""),
        )
        second = _replace_return(
            branches[3],
            _rest_return(
                level=2,
                downstream_prefix="second",
                target_prefix="second",
            ),
        )
        return "\nUNION\n".join((first, second))
    return _render_mq_view(branches[5])


def project_call_chain_rows(
    rows: Sequence[Mapping[str, Any]],
    view: CallChainView,
) -> tuple[dict[str, Any], ...]:
    """Project a frozen six-branch snapshot into an independent view."""

    view = CallChainView(view)
    if view is CallChainView.LOCAL:
        return tuple(
            {
                "根方法": row["根方法"],
                "图谱版本": row["图谱版本"],
                "源服务": row["源服务"],
                "源API路径": row["源API路径"],
                "源HTTP方法": row["源HTTP方法"],
                "方法路径": row["方法路径"],
                "叶子方法": row["目标方法"],
            }
            for row in rows
            if row["链路类型"] == "local" and row["层级"] == 0
        )
    if view is CallChainView.REST:
        return tuple(
            {
                "根方法": row["根方法"],
                "图谱版本": row["图谱版本"],
                "REST层级": row["层级"],
                "源服务": row["源服务"],
                "源API路径": row["源API路径"],
                "源HTTP方法": row["源HTTP方法"],
                "源方法": row["源方法"],
                "方法路径": row["方法路径"],
                "下游API路径": row["下游API路径"],
                "目标服务": row["目标服务"],
                "目标API路径": row["目标API路径"],
                "目标HTTP方法": row["目标HTTP方法"],
                "目标方法": row["目标方法"],
            }
            for row in rows
            if row["链路类型"] == "rest"
        )
    return _project_mq_rows(rows)


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


def _replace_return(branch: str, return_clause: str) -> str:
    head, separator, _ = branch.rpartition("\nRETURN DISTINCT ")
    if not separator:
        raise ValueError("完整调用链模板分支缺少 RETURN DISTINCT")
    return f"{head}\n{return_clause}"


def _rest_return(
    *,
    level: int,
    downstream_prefix: str,
    target_prefix: str,
) -> str:
    downstream = (
        f"{downstream_prefix}DownstreamApi"
        if downstream_prefix
        else "downstreamApi"
    )
    target_service = (
        f"{target_prefix}TargetService" if target_prefix else "targetService"
    )
    target_api = f"{target_prefix}TargetApi" if target_prefix else "targetApi"
    target_method = (
        f"{target_prefix}TargetEntryMethod"
        if target_prefix
        else "targetEntryMethod"
    )
    if level == 1:
        source_service = "sourceService"
        source_api = "sourceApi"
        source_method = "leafMethod"
        path = "rootPath"
    else:
        source_service = "targetService"
        source_api = "targetApi"
        source_method = "targetLeafMethod"
        path = "targetPath"
    return (
        "RETURN DISTINCT anchorMethod.全限定名 AS 根方法, "
        f"graphVersion AS 图谱版本, {level} AS REST层级, "
        f"{source_service}.服务名称 AS 源服务, "
        f"{source_api}.API路径 AS 源API路径, "
        f"{source_api}.HTTP方法 AS 源HTTP方法, "
        f"{source_method}.全限定名 AS 源方法, "
        f"[method IN nodes({path}) | method.全限定名] AS 方法路径, "
        f"{downstream}.API路径 AS 下游API路径, "
        f"{target_service}.服务名称 AS 目标服务, "
        f"{target_api}.API路径 AS 目标API路径, "
        f"{target_api}.HTTP方法 AS 目标HTTP方法, "
        f"{target_method}.全限定名 AS 目标方法"
    )


def _render_mq_view(branch: str) -> str:
    ownership = (
        "MATCH (anchorMethod)-[anchorClassRelation:归属于]->(anchorClass:类)"
        "-[anchorServiceRelation:归属于]->(sourceService:微服务)\n"
        "OPTIONAL MATCH (anchorMethod)-[sourceEntryRelation:服务于]->"
        "(sourceApi:上游API)\n"
    )
    branch = branch.replace(
        "MATCH (leafMethod)-[publishRelation:发布至]",
        ownership + "MATCH (leafMethod)-[publishRelation:发布至]",
        1,
    )
    branch = branch.replace(
        "WITH anchorMethod, rootPath, exchange,",
        "WITH anchorMethod, leafMethod, rootPath, sourceService, sourceApi, "
        "anchorClassRelation, anchorClass, anchorServiceRelation, "
        "sourceEntryRelation, exchange,",
        1,
    )
    branch = branch.replace(
        "\n  AND publishRelation.图谱版本 = graphVersion",
        "\n  AND anchorClassRelation.图谱版本 = graphVersion"
        "\n  AND anchorClass.图谱版本 = graphVersion"
        "\n  AND anchorServiceRelation.图谱版本 = graphVersion"
        "\n  AND sourceService.图谱版本 = graphVersion"
        "\n  AND (sourceEntryRelation IS NULL OR "
        "(sourceEntryRelation.图谱版本 = graphVersion "
        "AND sourceApi.图谱版本 = graphVersion))"
        "\n  AND publishRelation.图谱版本 = graphVersion",
        1,
    )
    return _replace_return(
        branch,
        "RETURN DISTINCT anchorMethod.全限定名 AS 根方法, "
        "graphVersion AS 图谱版本, sourceService.服务名称 AS 源服务, "
        "sourceApi.API路径 AS 源API路径, sourceApi.HTTP方法 AS 源HTTP方法, "
        "leafMethod.全限定名 AS 发布方法, "
        "[method IN nodes(rootPath) | method.全限定名] AS 发布方法路径, "
        "exchange.交换机名称 AS 消息交换机, queue.队列名称 AS 消息队列, "
        "publishRelation.路由键 AS 路由键, "
        "consumerService.服务名称 AS 目标服务, "
        "consumerMethod.全限定名 AS 消费方法, "
        "[method IN nodes(consumerPath) | method.全限定名] AS 消费者方法路径",
    )


def _project_mq_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    projected: list[dict[str, Any]] = []
    boundaries = (row for row in rows if row["链路类型"] == "mq")
    for boundary in boundaries:
        consumers = tuple(
            row
            for row in rows
            if row["链路类型"] == "local"
            and row["层级"] == boundary["层级"]
            and row["源服务"] == boundary["目标服务"]
            and row["源方法"] == boundary["目标方法"]
            and row["消息交换机"] == boundary["消息交换机"]
            and row["消息队列"] == boundary["消息队列"]
            and row["路由键"] == boundary["路由键"]
        )
        for consumer in consumers:
            projected.append(
                {
                    "根方法": boundary["根方法"],
                    "图谱版本": boundary["图谱版本"],
                    "源服务": boundary["源服务"],
                    "源API路径": boundary["源API路径"],
                    "源HTTP方法": boundary["源HTTP方法"],
                    "发布方法": boundary["源方法"],
                    "发布方法路径": boundary["方法路径"],
                    "消息交换机": boundary["消息交换机"],
                    "消息队列": boundary["消息队列"],
                    "路由键": boundary["路由键"],
                    "目标服务": boundary["目标服务"],
                    "消费方法": boundary["目标方法"],
                    "消费者方法路径": consumer["方法路径"],
                }
            )
    return tuple(projected)


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
