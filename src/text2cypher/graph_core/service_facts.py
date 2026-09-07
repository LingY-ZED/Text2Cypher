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


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()
