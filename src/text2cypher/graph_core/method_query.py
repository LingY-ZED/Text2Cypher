"""Deterministic, parameterized method-query compilers for stable shapes."""

from __future__ import annotations

from collections.abc import Iterable

from text2cypher.domain.models import QueryStatement, ResolvedMethod
from text2cypher.domain.query_shapes import QueryShape


class MethodQueryCypherCompiler:
    """Compile selected method-level query shapes without interpolating anchors."""

    _SUPPORTED_SHAPES = frozenset(
        {
            QueryShape.UPSTREAM_REACHABILITY,
            QueryShape.DIRECT_UPSTREAM,
            QueryShape.DIRECT_DOWNSTREAM_METHOD,
            QueryShape.REACHABLE_ENTRY_API,
            QueryShape.FULL_ENTRY_CHAIN,
            QueryShape.DIRECT_REST_EGRESS,
        }
    )

    def compile(
        self,
        query_shape: QueryShape,
        methods: Iterable[ResolvedMethod],
    ) -> QueryStatement:
        """Return one read-only statement for resolved method anchors."""

        if query_shape not in self._SUPPORTED_SHAPES:
            raise ValueError(f"不支持确定性方法查询形状：{query_shape}")
        qualified_names = tuple(
            dict.fromkeys(
                method.qualified_name
                for method in sorted(
                    methods,
                    key=lambda method: (method.qualified_name, method.graph_version),
                )
            )
        )
        if not qualified_names:
            raise ValueError("确定性方法查询至少需要一个已解析的方法锚点")

        predicate, parameters = _anchor_predicate("target", qualified_names)
        if query_shape is QueryShape.UPSTREAM_REACHABILITY:
            cypher = _upstream_reachability(predicate)
        elif query_shape is QueryShape.DIRECT_UPSTREAM:
            cypher = _direct_upstream(predicate)
        elif query_shape is QueryShape.DIRECT_DOWNSTREAM_METHOD:
            cypher = _direct_downstream(predicate)
        elif query_shape is QueryShape.REACHABLE_ENTRY_API:
            cypher = _reachable_entry_api(predicate)
        elif query_shape is QueryShape.FULL_ENTRY_CHAIN:
            cypher = _full_entry_chain(predicate)
        else:
            cypher = _direct_rest_egress(predicate)
        return QueryStatement(cypher=cypher, parameters=parameters)


def _anchor_predicate(
    variable: str,
    qualified_names: tuple[str, ...],
) -> tuple[str, dict[str, str]]:
    if len(qualified_names) == 1:
        return (
            f"{variable}.全限定名 = $anchorQualifiedName",
            {"anchorQualifiedName": qualified_names[0]},
        )
    parameters = {
        f"anchorQualifiedName{index}": value
        for index, value in enumerate(qualified_names)
    }
    predicate = "(" + " OR ".join(
        f"{variable}.全限定名 = ${name}" for name in parameters
    ) + ")"
    return predicate, parameters


def _direct_upstream(target_filter: str) -> str:
    return f"""
MATCH (caller:方法)-[call:调用]->(target:方法)
WHERE {target_filter}
  AND caller.图谱版本 = target.图谱版本
  AND call.图谱版本 = target.图谱版本
RETURN DISTINCT target.全限定名 AS 目标方法,
                caller.全限定名 AS 调用方法
ORDER BY 调用方法
""".strip()


def _upstream_reachability(target_filter: str) -> str:
    return f"""
MATCH (target:方法), (upstream:方法)
WHERE {target_filter}
  AND upstream <> target
  AND upstream.图谱版本 = target.图谱版本
  AND EXISTS {{
    MATCH path = (upstream)-[:调用*1..]->(target)
    WHERE ALL(pathRelation IN relationships(path)
              WHERE pathRelation.图谱版本 = target.图谱版本)
      AND ALL(pathNode IN nodes(path)
              WHERE pathNode.图谱版本 = target.图谱版本)
  }}
RETURN DISTINCT target.全限定名 AS 目标方法,
                upstream.全限定名 AS 上游方法
ORDER BY 上游方法
""".strip()


def _direct_downstream(target_filter: str) -> str:
    return f"""
MATCH (target:方法)-[call:调用]->(called:方法)
WHERE {target_filter}
  AND called.图谱版本 = target.图谱版本
  AND call.图谱版本 = target.图谱版本
RETURN DISTINCT called.全限定名 AS 被调用方法
ORDER BY 被调用方法
""".strip()


def _reachable_entry_api(target_filter: str) -> str:
    return f"""
MATCH (entry:方法)-[serves:服务于]->(entryApi:上游API)
MATCH (target:方法)
WHERE {target_filter}
  AND entry.图谱版本 = target.图谱版本
  AND serves.图谱版本 = target.图谱版本
  AND entryApi.图谱版本 = target.图谱版本
  AND (
    entry = target
    OR EXISTS {{
      MATCH path = (entry)-[:调用*1..]->(target)
      WHERE ALL(pathRelation IN relationships(path)
                WHERE pathRelation.图谱版本 = target.图谱版本)
    }}
  )
RETURN DISTINCT target.全限定名 AS 目标方法,
                entry.全限定名 AS 入口方法,
                entryApi.API路径 AS 入口API,
                entryApi.HTTP方法 AS HTTP方法
ORDER BY 入口API, 入口方法
""".strip()


def _full_entry_chain(target_filter: str) -> str:
    return f"""
MATCH (entry:方法)-[serves:服务于]->(entryApi:上游API)
MATCH (entry)-[interfaceCall:接口调用]->(target:方法)
WHERE {target_filter}
  AND entry.图谱版本 = target.图谱版本
  AND serves.图谱版本 = target.图谱版本
  AND entryApi.图谱版本 = target.图谱版本
  AND interfaceCall.图谱版本 = target.图谱版本
RETURN DISTINCT target.全限定名 AS 目标方法,
                entryApi.API路径 AS 入口API,
                [entry.全限定名, target.全限定名] AS 方法路径
UNION
MATCH (entry:方法)-[serves:服务于]->(entryApi:上游API)
MATCH path = (entry)-[:链中下一节点*1..5]->(target:方法)
WITH entry, serves, entryApi, path, target, relationships(path) AS pathRelations
WHERE {target_filter}
  AND entry.图谱版本 = target.图谱版本
  AND serves.图谱版本 = target.图谱版本
  AND entryApi.图谱版本 = target.图谱版本
  AND ALL(pathRelation IN pathRelations
          WHERE pathRelation.图谱版本 = target.图谱版本
            AND pathRelation.路径签名 = pathRelations[0].路径签名)
  AND (size(pathRelations) = 1 OR ALL(index IN range(1, size(pathRelations) - 1)
      WHERE pathRelations[index].位置索引 = pathRelations[index - 1].位置索引 + 1))
RETURN DISTINCT target.全限定名 AS 目标方法,
                entryApi.API路径 AS 入口API,
                [method IN nodes(path) | method.全限定名] AS 方法路径
UNION
MATCH (entry:方法)-[serves:服务于]->(entryApi:上游API)
MATCH (entry)-[interfaceCall:接口调用]->(first:方法)
MATCH path = (first)-[:链中下一节点*1..4]->(target:方法)
WITH entry, serves, entryApi, interfaceCall, path, target,
     relationships(path) AS pathRelations
WHERE {target_filter}
  AND entry.图谱版本 = target.图谱版本
  AND serves.图谱版本 = target.图谱版本
  AND entryApi.图谱版本 = target.图谱版本
  AND interfaceCall.图谱版本 = target.图谱版本
  AND first.图谱版本 = target.图谱版本
  AND ALL(pathRelation IN pathRelations
          WHERE pathRelation.图谱版本 = target.图谱版本
            AND pathRelation.路径签名 = pathRelations[0].路径签名)
  AND (size(pathRelations) = 1 OR ALL(index IN range(1, size(pathRelations) - 1)
      WHERE pathRelations[index].位置索引 = pathRelations[index - 1].位置索引 + 1))
RETURN DISTINCT target.全限定名 AS 目标方法,
                entryApi.API路径 AS 入口API,
                [entry.全限定名] + [method IN nodes(path) | method.全限定名] AS 方法路径
UNION
MATCH (target:方法)-[serves:服务于]->(entryApi:上游API)
WHERE {target_filter}
  AND serves.图谱版本 = target.图谱版本
  AND entryApi.图谱版本 = target.图谱版本
RETURN DISTINCT target.全限定名 AS 目标方法,
                entryApi.API路径 AS 入口API,
                [target.全限定名] AS 方法路径
""".strip()


def _direct_rest_egress(target_filter: str) -> str:
    return f"""
MATCH (target:方法)-[downstreamCall:下游调用]->(downstreamApi:下游API)
      -[targetServiceRelation:目标服务]->(targetService:微服务)
WHERE {target_filter}
  AND downstreamCall.图谱版本 = target.图谱版本
  AND downstreamApi.图谱版本 = target.图谱版本
  AND targetServiceRelation.图谱版本 = target.图谱版本
  AND targetService.图谱版本 = target.图谱版本
RETURN DISTINCT target.全限定名 AS 目标方法,
                downstreamApi.API路径 AS 下游API,
                targetService.服务名称 AS 下游服务
ORDER BY 下游服务, 下游API, 目标方法
""".strip()
