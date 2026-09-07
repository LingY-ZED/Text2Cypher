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
from text2cypher.domain.query_shapes import QueryShape

_LOGGER = logging.getLogger(__name__)
_METHOD_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
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
