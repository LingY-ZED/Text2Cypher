"""Parameterized compilers for class ownership and service-entry facts."""

from __future__ import annotations

from text2cypher.domain.models import QueryStatement


class ClassFactCypherCompiler:
    """Compile class-anchored structural queries without string interpolation."""

    def compile_summary(self, class_anchor: str) -> QueryStatement:
        """Return a class's service, implemented interface, and method-name set."""

        return QueryStatement(
            cypher=f"""
MATCH (implementation:类)-[ownership:归属于]->(service:微服务)
MATCH (implementation)-[implements:接口实现]->(interface:类)
WHERE {_class_predicate('implementation')}
  AND ownership.图谱版本 = implementation.图谱版本
  AND service.图谱版本 = implementation.图谱版本
  AND implements.图谱版本 = implementation.图谱版本
  AND interface.图谱版本 = implementation.图谱版本
OPTIONAL MATCH (method:方法)-[methodOwnership:归属于]->(implementation)
WHERE methodOwnership IS NULL OR (
  methodOwnership.图谱版本 = implementation.图谱版本
  AND method.图谱版本 = implementation.图谱版本
)
RETURN service.服务名称 AS 服务名称,
       interface.全限定名 AS 接口全限定名,
       collect(DISTINCT method.全限定名) AS 方法全限定名
ORDER BY 服务名称, 接口全限定名
""".strip(),
            parameters=_class_parameters(class_anchor),
        )

    def compile_service_entry_apis(self, class_anchor: str) -> QueryStatement:
        """Return public APIs of the service that owns a class anchor."""

        return QueryStatement(
            cypher=f"""
MATCH (implementation:类)-[ownership:归属于]->(service:微服务)
MATCH (service)<-[serviceOwnership:归属于]-(:类)<-[methodOwnership:归属于]-(method:方法)
      -[serves:服务于]->(api:上游API)
WHERE {_class_predicate('implementation')}
  AND ownership.图谱版本 = implementation.图谱版本
  AND service.图谱版本 = implementation.图谱版本
  AND serviceOwnership.图谱版本 = service.图谱版本
  AND methodOwnership.图谱版本 = service.图谱版本
  AND method.图谱版本 = service.图谱版本
  AND serves.图谱版本 = service.图谱版本
  AND api.图谱版本 = service.图谱版本
RETURN DISTINCT api.API路径 AS 接口路径,
                api.HTTP方法 AS 请求方式
ORDER BY 接口路径, 请求方式
""".strip(),
            parameters=_class_parameters(class_anchor),
        )


def _class_predicate(variable: str) -> str:
    return (
        f"({variable}.全限定名 = $classAnchor "
        f"OR {variable}.全限定名 ENDS WITH $classSuffix)"
    )


def _class_parameters(class_anchor: str) -> dict[str, str]:
    normalized = _text(class_anchor)
    return {
        "classAnchor": normalized,
        "classSuffix": f".{normalized.rsplit('.', maxsplit=1)[-1]}",
    }


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("class_anchor must be a non-empty string")
    return value.strip()
