"""基于共享 LLMClient 的单轮 Primary LLM Agent。"""

from __future__ import annotations

import logging

from text2cypher.components.primary_agent import (
    PrimaryAgentPlanError,
    PrimaryAgentPromptBuilder,
    PrimaryAgentResponseError,
    PrimaryAgentResponseParser,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import PrimaryAgentPlan
from text2cypher.domain.ports import LLMClient

_LOGGER = logging.getLogger(__name__)


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
        fallback = PrimaryAgentPlan.fallback(normalized_question)
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
            self._log_event(
                outcome="fallback",
                query_count=1,
                decomposed=False,
                reason="llm_generation_error",
            )
            return fallback
        except PrimaryAgentResponseError:
            self._log_event(
                outcome="fallback",
                query_count=1,
                decomposed=False,
                reason="invalid_response",
            )
            return fallback
        except PrimaryAgentPlanError:
            self._log_event(
                outcome="fallback",
                query_count=1,
                decomposed=False,
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
