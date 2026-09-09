"""完整方法调用链的确定性查询 Tool。"""

from __future__ import annotations

import re
from dataclasses import asdict

from text2cypher.components.call_chain_anchor import parse_call_chain_anchor
from text2cypher.domain.errors import CypherExecutionError
from text2cypher.domain.models import (
    CALL_CHAIN_RESULT_COLUMNS,
    CallChainQuerySpec,
    CallChainSegment,
    ExecutedCypher,
    PrimaryAgentQuery,
    QueryResult,
    QueryStatement,
)
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

        executed = self.execute(
            anchor,
            graph_version=graph_version,
            local_hops=local_hops,
            rest_hops=rest_hops,
            mq_hops=mq_hops,
            service_name=service_name,
            http_method=http_method,
            api_path=api_path,
        )
        return (
            tuple(CallChainSegment.from_row(row) for row in executed.result.rows)
            if executed is not None
            else ()
        )

    def query(self, query: PrimaryAgentQuery) -> ExecutedCypher | None:
        """Runtime 入口；未识别或未匹配实体才返回 None。"""
        parsed = parse_call_chain_anchor(query.question)
        if parsed is None:
            return None
        return self.execute(**asdict(parsed))

    def execute(
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
    ) -> ExecutedCypher | None:
        """按版本编译并合并为受全局行数限制的参数化语句。"""
        if anchor.startswith("/"):
            methods = self._resolver.resolve_entry_api(
                http_method,
                api_path or anchor,
                service_name=service_name,
                graph_version=graph_version,
            )
        else:
            methods = self._resolver.resolve_symbol(
                anchor,
                graph_version=graph_version,
                service_name=service_name,
                http_method=http_method,
                api_path=api_path,
            )
        if not methods:
            return None
        groups: dict[str, set[str]] = {}
        for method in methods:
            groups.setdefault(method.graph_version, set()).add(method.qualified_name)
        statements = []
        for version, names in sorted(groups.items()):
            ordered = sorted(names)
            statements.append(
                self._compiler.compile(
                    CallChainQuerySpec(
                        anchor_qualified_name=ordered[0],
                        additional_anchor_qualified_names=tuple(ordered[1:]),
                        graph_version=version,
                        local_hops=local_hops,
                        rest_hops=rest_hops,
                        mq_hops=mq_hops,
                    )
                )
            )
        if len(statements) == 1:
            statement = statements[0]
        else:
            branches = []
            parameters = {}
            for index, item in enumerate(statements):
                branches.append(
                    re.sub(
                        r"\$([A-Za-z_][A-Za-z0-9_]*)",
                        rf"$g{index}_\g<1>",
                        item.cypher,
                    )
                )
                parameters.update(
                    {f"g{index}_{k}": v for k, v in item.parameters.items()}
                )
            statement = QueryStatement("\nUNION\n".join(branches), parameters)
        executed = self._gateway.execute_statement(statement)
        if executed.result.truncated:
            raise CypherExecutionError("完整调用链结果超过安全行数上限")
        segments = {CallChainSegment.from_row(row) for row in executed.result.rows}
        return ExecutedCypher(
            executed.cypher,
            QueryResult(
                columns=CALL_CHAIN_RESULT_COLUMNS,
                rows=tuple(s.to_row() for s in sorted(segments, key=_segment_sort_key)),
                duration_ms=executed.result.duration_ms,
            ),
        )


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
