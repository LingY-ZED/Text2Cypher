"""Parameterized compilers for stable service and message-graph facts."""

from __future__ import annotations

from text2cypher.domain.models import QueryStatement


class ServiceFactCypherCompiler:
    """Compile reusable ownership and REST-dependency fact queries."""

    def compile_message_queue_count(self, queue_name: str) -> QueryStatement:
        """Count queue nodes with one exact queue-name anchor."""

        return QueryStatement(
            cypher="""
MATCH (queue:消息队列)
WHERE queue.队列名称 = $queueName
RETURN count(DISTINCT queue) AS 队列节点数
""".strip(),
            parameters={"queueName": _text(queue_name, "queue_name")},
        )

    def compile_exchange_owners(self, exchange_name: str) -> QueryStatement:
        """Return the owning service and type for an exact exchange name."""

        return QueryStatement(
            cypher="""
MATCH (exchange:消息交换机)-[:归属于]->(service:微服务)
WHERE exchange.交换机名称 = $exchangeName
RETURN service.服务名称 AS 服务名称,
       exchange.交换机名称 AS 交换机名称,
       exchange.交换机类型 AS 交换机类型
ORDER BY 服务名称, 交换机名称
""".strip(),
            parameters={"exchangeName": _text(exchange_name, "exchange_name")},
        )

    def compile_rest_callers(self, target_service_name: str) -> QueryStatement:
        """Return services whose methods directly target one REST service."""

        return QueryStatement(
            cypher="""
MATCH (caller:微服务)<-[:归属于]-(:类)<-[:归属于]-(method:方法)
      -[:下游调用]->(:下游API)-[:目标服务]->(target:微服务)
WHERE target.服务名称 = $targetServiceName
RETURN DISTINCT caller.服务名称 AS 上游服务
ORDER BY 上游服务
""".strip(),
            parameters={
                "targetServiceName": _text(
                    target_service_name,
                    "target_service_name",
                )
            },
        )

    def compile_entry_api_http_distribution(
        self,
        service_name: str,
    ) -> QueryStatement:
        """Group a service's entry APIs by HTTP method."""

        return QueryStatement(
            cypher="""
MATCH (method:方法)-[serves:服务于]->(api:上游API)
MATCH (method)-[methodOwnership:归属于]->(:类)
      -[serviceOwnership:归属于]->(service:微服务)
WHERE service.服务名称 = $serviceName
  AND serves.图谱版本 = method.图谱版本
  AND api.图谱版本 = method.图谱版本
  AND methodOwnership.图谱版本 = method.图谱版本
  AND serviceOwnership.图谱版本 = method.图谱版本
  AND service.图谱版本 = method.图谱版本
RETURN api.HTTP方法 AS 请求方式,
       count(DISTINCT api) AS API数量
ORDER BY 请求方式
""".strip(),
            parameters={"serviceName": _text(service_name, "service_name")},
        )

    def compile_rest_target_call_counts(self, service_name: str) -> QueryStatement:
        """Count direct REST-call relations grouped by target service."""

        return QueryStatement(
            cypher="""
MATCH (service:微服务)<-[serviceOwnership:归属于]-(:类)
      <-[methodOwnership:归属于]-(method:方法)
      -[remote:下游调用]->(downstreamApi:下游API)
      -[targetOwnership:目标服务]->(target:微服务)
WHERE service.服务名称 = $serviceName
  AND serviceOwnership.图谱版本 = service.图谱版本
  AND methodOwnership.图谱版本 = service.图谱版本
  AND method.图谱版本 = service.图谱版本
  AND remote.图谱版本 = service.图谱版本
  AND downstreamApi.图谱版本 = service.图谱版本
  AND targetOwnership.图谱版本 = service.图谱版本
  AND target.图谱版本 = service.图谱版本
RETURN target.服务名称 AS 下游服务,
       count(DISTINCT remote) AS 调用关系数
ORDER BY 调用关系数 DESC, 下游服务
""".strip(),
            parameters={"serviceName": _text(service_name, "service_name")},
        )

    def compile_implementation_classes(self, service_name: str) -> QueryStatement:
        """Return classes in a service that implement an interface."""

        return QueryStatement(
            cypher="""
MATCH (implementation:类)-[implements:接口实现]->(interface:类)
MATCH (implementation)-[ownership:归属于]->(service:微服务)
WHERE service.服务名称 = $serviceName
  AND implements.图谱版本 = implementation.图谱版本
  AND interface.图谱版本 = implementation.图谱版本
  AND ownership.图谱版本 = implementation.图谱版本
  AND service.图谱版本 = implementation.图谱版本
RETURN DISTINCT implementation.全限定名 AS 实现类
ORDER BY 实现类
""".strip(),
            parameters={"serviceName": _text(service_name, "service_name")},
        )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()
