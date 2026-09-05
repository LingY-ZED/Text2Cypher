"""单次自然语言代码图谱查询 Tool。"""

from __future__ import annotations

from text2cypher.domain.models import (
    ExecutedCypher,
    GraphQueryRequest,
    QueryContext,
)
from text2cypher.domain.ports import GraphQueryEngine


class QueryCodeGraphTool:
    """把一条请求交给 Graph Query Engine，不承担整题规划或总结。"""

    def __init__(self, graph_query_engine: GraphQueryEngine) -> None:
        self._graph_query_engine = graph_query_engine

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher:
        """执行一条受固定图上下文约束的查询。"""

        return self._graph_query_engine.query(request, context)
