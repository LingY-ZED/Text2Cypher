"""完整方法调用链的确定性查询 Tool。"""

from __future__ import annotations

from text2cypher.domain.errors import CypherExecutionError
from text2cypher.domain.models import CallChainQuerySpec, CallChainSegment
from text2cypher.domain.ports import ParameterizedReadOnlyCypherGateway
from text2cypher.graph_core.call_chain import CallChainCypherCompiler
from text2cypher.tools.resolve_symbol import ResolveSymbolTool


class FindCallChainTool:
    """解析方法锚点后编译、准入并执行参数化完整调用链。"""

    def __init__(
        self,
        resolver: ResolveSymbolTool,
        gateway: ParameterizedReadOnlyCypherGateway,
        compiler: CallChainCypherCompiler | None = None,
    ) -> None:
        self._resolver = resolver
        self._gateway = gateway
        self._compiler = compiler or CallChainCypherCompiler()

    def find_call_chain(
        self,
        anchor: str,
        *,
        graph_version: str | None = None,
        local_hops: int = 10,
        rest_hops: int = 2,
        mq_hops: int = 1,
        service_name: str | None = None,
        http_method: str | None = None,
        api_path: str | None = None,
    ) -> tuple[CallChainSegment, ...]:
        """返回稳定表格分段；同名方法会按服务和图谱版本分别保留。"""

        methods = self._resolver.resolve_symbol(
            anchor,
            graph_version=graph_version,
            service_name=service_name,
            http_method=http_method,
            api_path=api_path,
        )
        segments: set[CallChainSegment] = set()
        for method in methods:
            specification = CallChainQuerySpec(
                anchor_qualified_name=method.qualified_name,
                graph_version=graph_version or method.graph_version,
                local_hops=local_hops,
                rest_hops=rest_hops,
                mq_hops=mq_hops,
            )
            statement = self._compiler.compile(specification)
            executed = self._gateway.execute_statement(statement)
            if executed.result.truncated:
                raise CypherExecutionError("完整调用链结果超过安全行数上限")
            segments.update(
                CallChainSegment.from_row(row) for row in executed.result.rows
            )
        return tuple(sorted(segments, key=_segment_sort_key))


def _segment_sort_key(segment: CallChainSegment) -> tuple[object, ...]:
    link_order = {"local": 0, "rest": 1, "mq": 2}
    return (
        segment.root_method,
        segment.graph_version,
        segment.level,
        link_order[segment.link_type.value],
        segment.source_service or "",
        segment.source_method or "",
        segment.method_path,
        segment.downstream_api_path or "",
        segment.target_service or "",
        segment.target_method or "",
        segment.message_exchange or "",
        segment.message_queue or "",
        segment.routing_key or "",
    )
