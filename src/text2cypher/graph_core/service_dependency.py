"""Deterministic service-dependency query compilation."""

from __future__ import annotations

from text2cypher.domain.models import QueryStatement


class ServiceDependencyCypherCompiler:
    """Compile explicitly requested cross-service dependency matrices."""

    def compile_message_senders_rest_targets(
        self,
        receiver_service_name: str,
    ) -> QueryStatement:
        """Return MQ senders and each sender's REST target services."""

        normalized = receiver_service_name.strip()
        if not normalized:
            raise ValueError("消息接收服务名称不能为空")
        return QueryStatement(
            cypher="""
MATCH (senderService:微服务)-[:消息依赖]->(receiverService:微服务)
MATCH (senderService)<-[:归属于]-(:类)<-[:归属于]-(callerMethod:方法)
      -[:下游调用]->(:下游API)-[:目标服务]->(restTargetService:微服务)
WHERE receiverService.服务名称 = $receiverServiceName
RETURN DISTINCT senderService.服务名称 AS 发送服务,
                restTargetService.服务名称 AS 下游服务
ORDER BY 发送服务, 下游服务
""".strip(),
            parameters={"receiverServiceName": normalized},
        )
