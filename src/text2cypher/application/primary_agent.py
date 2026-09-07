"""基于共享 LLMClient 的单轮 Primary LLM Agent。"""

from __future__ import annotations

import logging
import re

from text2cypher.components.primary_agent import (
    PrimaryAgentPlanError,
    PrimaryAgentPromptBuilder,
    PrimaryAgentResponseError,
    PrimaryAgentResponseParser,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import PrimaryAgentPlan, PrimaryAgentQuery
from text2cypher.domain.ports import LLMClient
from text2cypher.domain.query_shapes import QueryShape, resolve_query_shape

_LOGGER = logging.getLogger(__name__)
_METHOD_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
)
_ENTRY_API = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s，。；！？?]+)",
    re.IGNORECASE,
)
_API_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(/[A-Za-z0-9_./{}:-]+)")
_SERVICE_NAME = re.compile(r"\b(ts-[A-Za-z0-9-]+-service)\b")
_GRAPH_VERSION = re.compile(r"图谱版本\s*(?:为|=|：|:)?\s*(v\d+)\b", re.IGNORECASE)
_SIMPLE_METHOD_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*的?"
    r"(?:完整|端到端)?调用链",
)


class LLMPrimaryAgent:
    """生成可安全回退的结构化单轮自然语言检索计划。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        max_queries: int = 3,
        prompt_builder: PrimaryAgentPromptBuilder | None = None,
        response_parser: PrimaryAgentResponseParser | None = None,
    ) -> None:
        PrimaryAgentPromptBuilder._validate_max_queries(max_queries)
        self._llm_client = llm_client
        self._max_queries = max_queries
        self._prompt_builder = prompt_builder or PrimaryAgentPromptBuilder()
        self._response_parser = response_parser or PrimaryAgentResponseParser()

    def plan(self, question: str) -> PrimaryAgentPlan:
        normalized_question = PrimaryAgentPromptBuilder._normalize_question(question)
        try:
            prompt = self._prompt_builder.build(
                normalized_question,
                self._max_queries,
            )
            response = self._llm_client.generate(prompt)
            plan = self._response_parser.parse(
                response.content,
                normalized_question,
                self._max_queries,
            )
        except LLMGenerationError:
            fallback = self._fallback_plan(normalized_question)
            self._log_event(
                outcome="fallback",
                query_count=len(fallback.queries),
                decomposed=fallback.decomposed,
                reason="llm_generation_error",
            )
            return fallback
        except PrimaryAgentResponseError:
            fallback = self._fallback_plan(normalized_question)
            self._log_event(
                outcome="fallback",
                query_count=len(fallback.queries),
                decomposed=fallback.decomposed,
                reason="invalid_response",
            )
            return fallback
        except PrimaryAgentPlanError:
            fallback = self._fallback_plan(normalized_question)
            self._log_event(
                outcome="fallback",
                query_count=len(fallback.queries),
                decomposed=fallback.decomposed,
                reason="invalid_plan",
            )
            return fallback

        self._log_event(
            outcome="planned",
            query_count=len(plan.queries),
            decomposed=plan.decomposed,
            reason=None,
        )
        return plan

    @staticmethod
    def _fallback_plan(question: str) -> PrimaryAgentPlan:
        """Retain independent impact views when a malformed LLM plan is rejected."""

        call_chain = _call_chain_fallback_plan(question)
        if call_chain is not None:
            return call_chain
        anchor = _impact_anchor(question)
        if anchor is None:
            return PrimaryAgentPlan.fallback(question)
        return PrimaryAgentPlan(
            original_question=question,
            analysis_summary="分别检索变更方法的上游、可达入口和直接 REST 下游",
            queries=(
                PrimaryAgentQuery(
                    query_id="q1",
                    question=f"哪些上游方法能够调用到 {anchor}？",
                    intent="查询能够到达锚点方法的全部上游方法",
                    required_information=("上游方法",),
                    anchor=anchor,
                    query_shape=QueryShape.UPSTREAM_REACHABILITY,
                ),
                PrimaryAgentQuery(
                    query_id="q2",
                    question=f"哪些入口 API 可以到达 {anchor}？",
                    intent="查询能够到达锚点方法的入口 API",
                    required_information=("入口 API 路径", "HTTP 方法"),
                    anchor=anchor,
                    query_shape=QueryShape.REACHABLE_ENTRY_API,
                ),
                PrimaryAgentQuery(
                    query_id="q3",
                    question=f"{anchor} 直接远程调用了哪些 REST 下游服务？",
                    intent="查询锚点方法直接发出的 REST 下游调用",
                    required_information=("下游服务", "下游 API 路径"),
                    anchor=anchor,
                    query_shape=QueryShape.DIRECT_REST_EGRESS,
                ),
            ),
        )

    @staticmethod
    def _log_event(
        *,
        outcome: str,
        query_count: int,
        decomposed: bool,
        reason: str | None,
    ) -> None:
        level = logging.INFO if outcome == "planned" else logging.WARNING
        _LOGGER.log(
            level,
            "primary_agent_planning",
            extra={
                "primary_agent_event": {
                    "component": "primary_agent",
                    "stage": "planning",
                    "outcome": outcome,
                    "query_count": query_count,
                    "decomposed": decomposed,
                    "reason": reason,
                }
            },
        )


def _impact_anchor(question: str) -> str | None:
    """Recognize fixed impact views without consuming query results."""

    compact = "".join(question.lower().split())
    required_cues = ("修改", "上游", "入口", "下游")
    if not all(cue in compact for cue in required_cues):
        return None
    match = _METHOD_REFERENCE.search(question)
    return match.group(1) if match is not None else None


def _call_chain_fallback_plan(question: str) -> PrimaryAgentPlan | None:
    """Build the three self-contained call-chain views without model output."""

    if resolve_query_shape(question) is not QueryShape.FULL_METHOD_CALL_CHAIN:
        return None
    anchor, descriptor = _call_chain_anchor_and_descriptor(question)
    if anchor is None or descriptor is None:
        return None
    return PrimaryAgentPlan(
        original_question=question,
        analysis_summary="分别查询服务内、REST 和 MQ 三类调用明细",
        queries=(
            PrimaryAgentQuery(
                query_id="q1",
                question=(
                    f"以 {descriptor} 为固定锚点，查询服务内调用明细，"
                    "服务内方法最多 10 跳。"
                ),
                intent="查询根服务内按路径签名和位置索引连续的有序方法路径",
                required_information=(
                    "根方法",
                    "图谱版本",
                    "源服务",
                    "源API路径",
                    "源HTTP方法",
                    "方法路径",
                    "叶子方法",
                ),
                anchor=anchor,
                query_shape=QueryShape.GENERAL,
            ),
            PrimaryAgentQuery(
                query_id="q2",
                question=(
                    f"以 {descriptor} 为固定锚点，查询 REST 跨服务明细，"
                    "REST 最多跨两层。"
                ),
                intent=(
                    "从根服务内有序方法路径展开 REST 出口、目标服务、"
                    "目标 API 和目标入口方法"
                ),
                required_information=(
                    "根方法",
                    "图谱版本",
                    "REST层级",
                    "源服务",
                    "源API路径",
                    "源HTTP方法",
                    "源方法",
                    "方法路径",
                    "下游API路径",
                    "目标服务",
                    "目标API路径",
                    "目标HTTP方法",
                    "目标方法",
                ),
                anchor=anchor,
                query_shape=QueryShape.GENERAL,
            ),
            PrimaryAgentQuery(
                query_id="q3",
                question=(
                    f"以 {descriptor} 为固定锚点，查询 MQ 发布消费明细，"
                    "MQ 最多跨一层。"
                ),
                intent=(
                    "从根服务内有序方法路径展开 MQ 发布、路由、消费及"
                    "消费者后续方法路径"
                ),
                required_information=(
                    "根方法",
                    "图谱版本",
                    "源服务",
                    "源API路径",
                    "源HTTP方法",
                    "发布方法",
                    "发布方法路径",
                    "消息交换机",
                    "消息队列",
                    "路由键",
                    "目标服务",
                    "消费方法",
                    "消费者方法路径",
                ),
                anchor=anchor,
                query_shape=QueryShape.GENERAL,
            ),
        ),
    )


def _call_chain_anchor_and_descriptor(
    question: str,
) -> tuple[str | None, str | None]:
    entry_match = _ENTRY_API.search(question)
    path_match = _API_PATH.search(question)
    method_match = _METHOD_REFERENCE.search(question)
    simple_match = _SIMPLE_METHOD_REFERENCE.search(question)
    if entry_match is not None:
        anchor = entry_match.group(2)
        parts = [f"{entry_match.group(1).upper()} {anchor}"]
    elif path_match is not None:
        anchor = path_match.group(1)
        parts = [anchor]
    elif method_match is not None:
        anchor = method_match.group(1)
        parts = [anchor]
    elif simple_match is not None:
        anchor = simple_match.group(1)
        parts = [anchor]
    else:
        return None, None
    service_match = _SERVICE_NAME.search(question)
    if service_match is not None:
        parts.append(f"服务 {service_match.group(1)}")
    version_match = _GRAPH_VERSION.search(question)
    if version_match is not None:
        parts.append(f"图谱版本 {version_match.group(1)}")
    if "未映射" in question or "映射缺失" in question:
        parts.append("保留未映射 REST 出口")
    return anchor, "、".join(parts)
