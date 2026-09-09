"""从显式文本提取调用链锚点，不生成数据库查询。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from text2cypher.domain.models import PrimaryAgentPlan, PrimaryAgentQuery
from text2cypher.domain.query_shapes import QueryShape, resolve_query_shape


@dataclass(frozen=True)
class CallChainAnchor:
    anchor: str
    api_path: str | None = None
    http_method: str | None = None
    service_name: str | None = None
    graph_version: str | None = None


def parse_call_chain_anchor(question: str) -> CallChainAnchor | None:
    """优先识别 API 路径，其次识别方法；保留显式限定。"""
    path = re.search(r"(?<![A-Za-z0-9_.-])(/[A-Za-z0-9_./{}:-]+)", question)
    method = re.search(
        r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)",
        question,
    )
    simple = re.search(
        r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*的?"
        r"(?:完整|端到端)?调用链",
        question,
    )
    match = path or method or simple
    if match is None:
        return None
    http = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", question, re.I)
    service = re.search(r"\b(ts-[A-Za-z0-9-]+-service)\b", question)
    version = re.search(
        r"图谱版本\s*(?:为|=|：|:)?\s*['\"]?([A-Za-z0-9_.-]+)",
        question,
    )
    return CallChainAnchor(
        anchor=match.group(1),
        api_path=path.group(1) if path else None,
        http_method=http.group(1).upper() if http else None,
        service_name=service.group(1) if service else None,
        graph_version=version.group(1) if version else None,
    )


def build_call_chain_plan(question: str) -> PrimaryAgentPlan | None:
    """保留完整调用链为一个确定性任务。"""
    if resolve_query_shape(question) is not QueryShape.FULL_METHOD_CALL_CHAIN:
        return None
    parsed = parse_call_chain_anchor(question)
    return PrimaryAgentPlan(
        original_question=question,
        analysis_summary="查询完整调用链",
        queries=(
            PrimaryAgentQuery(
                query_id="q1",
                question=question,
                intent="查询完整调用链",
                required_information=("调用链分段",),
                anchor=parsed.anchor if parsed else question,
                query_shape=QueryShape.FULL_METHOD_CALL_CHAIN,
            ),
        ),
    )
