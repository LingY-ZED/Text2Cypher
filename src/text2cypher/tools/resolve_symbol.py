"""代码图谱方法实体的确定性解析 Tool。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from text2cypher.domain.models import QueryStatement, ResolvedMethod
from text2cypher.domain.ports import ParameterizedReadOnlyCypherGateway


class ResolveSymbolTool:
    """按全限定名、类名.方法名、方法名的固定优先级解析方法锚点。"""

    _SELECT = """
MATCH (method:方法)-[methodClass:归属于]->(class:类)
      -[classService:归属于]->(service:微服务)
WHERE {anchor_predicate}
  AND methodClass.图谱版本 = method.图谱版本
  AND class.图谱版本 = method.图谱版本
  AND classService.图谱版本 = method.图谱版本
  AND service.图谱版本 = method.图谱版本
  AND ($graphVersion IS NULL OR method.图谱版本 = $graphVersion)
  AND ($serviceName IS NULL OR service.服务名称 = $serviceName)
  AND (
    ($httpMethod IS NULL AND $apiPath IS NULL)
    OR EXISTS {{
      MATCH (method)-[serves:服务于]->(api:上游API)
      WHERE serves.图谱版本 = method.图谱版本
        AND api.图谱版本 = method.图谱版本
        AND ($httpMethod IS NULL OR api.HTTP方法 = $httpMethod)
        AND ($apiPath IS NULL OR api.API路径 = $apiPath)
    }}
  )
RETURN DISTINCT method.全限定名 AS qualified_name,
                method.方法名 AS method_name,
                class.全限定名 AS class_qualified_name,
                service.服务名称 AS service_name,
                method.图谱版本 AS graph_version
ORDER BY qualified_name, graph_version
""".strip()

    def __init__(self, gateway: ParameterizedReadOnlyCypherGateway) -> None:
        self._gateway = gateway

    def resolve_symbol(
        self,
        anchor: str,
        *,
        graph_version: str | None = None,
        service_name: str | None = None,
        http_method: str | None = None,
        api_path: str | None = None,
    ) -> tuple[ResolvedMethod, ...]:
        """解析锚点；只有更高优先级没有匹配时才使用后备规则。"""

        normalized_anchor = _text(anchor, "anchor")
        parameters = {
            "anchor": normalized_anchor,
            "graphVersion": _optional_text(graph_version, "graph_version"),
            "serviceName": _optional_text(service_name, "service_name"),
            "httpMethod": _optional_text(http_method, "http_method"),
            "apiPath": _optional_text(api_path, "api_path"),
        }
        exact = self._resolve("method.全限定名 = $anchor", parameters)
        if exact:
            return exact

        class_name, separator, method_name = normalized_anchor.rpartition(".")
        if separator:
            class_method = self._resolve(
                "class.全限定名 ENDS WITH $className "
                "AND method.方法名 = $methodName",
                {
                    **parameters,
                    "className": class_name,
                    "methodName": method_name,
                },
            )
            if class_method:
                return class_method
        else:
            method_name = normalized_anchor
        return self._resolve(
            "method.方法名 = $methodName",
            {**parameters, "methodName": method_name},
        )

    def resolve_entry_api(
        self,
        http_method: str,
        api_path: str,
        *,
        service_name: str | None = None,
        graph_version: str | None = None,
    ) -> tuple[ResolvedMethod, ...]:
        """按 HTTP 方法、API 路径和可选服务确定入口方法。"""

        normalized_method = _text(http_method, "http_method")
        normalized_path = _text(api_path, "api_path")
        parameters = {
            "anchor": normalized_path,
            "graphVersion": _optional_text(graph_version, "graph_version"),
            "serviceName": _optional_text(service_name, "service_name"),
            "httpMethod": normalized_method,
            "apiPath": normalized_path,
        }
        return self._resolve("true", parameters)

    def _resolve(
        self,
        anchor_predicate: str,
        parameters: Mapping[str, str | None],
    ) -> tuple[ResolvedMethod, ...]:
        statement = QueryStatement(
            cypher=self._SELECT.format(anchor_predicate=anchor_predicate),
            parameters=parameters,
        )
        executed = self._gateway.execute_statement(statement)
        return tuple(
            ResolvedMethod(
                qualified_name=_row_value(row, "qualified_name"),
                method_name=_row_value(row, "method_name"),
                class_qualified_name=_row_value(row, "class_qualified_name"),
                service_name=_row_value(row, "service_name"),
                graph_version=_row_value(row, "graph_version"),
            )
            for row in executed.result.rows
        )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


def _row_value(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"方法实体解析结果缺少 {name}")
    return value
