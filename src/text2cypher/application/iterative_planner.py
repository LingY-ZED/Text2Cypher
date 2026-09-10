"""基于共享 LLMClient 的 IterativeRuntime Planner。"""

from __future__ import annotations

import logging

from text2cypher.components.iterative_planner import (
    IterativePlannerPromptBuilder,
    IterativePlannerResponseParser,
)
from text2cypher.domain.iterative import IterativePlan, IterativePlanningContext
from text2cypher.domain.ports import LLMClient

_LOGGER = logging.getLogger(__name__)


class LLMIterativePlanner:
    """每次调用根据已压缩 Observation 生成一份下一轮计划。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        prompt_builder: IterativePlannerPromptBuilder | None = None,
        response_parser: IterativePlannerResponseParser | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_builder = prompt_builder or IterativePlannerPromptBuilder()
        self._response_parser = response_parser or IterativePlannerResponseParser()

    def plan(self, context: IterativePlanningContext) -> IterativePlan:
        prompt = self._prompt_builder.build(context)
        response = self._llm_client.generate(prompt)
        plan = self._response_parser.parse(response.content, context)
        _LOGGER.info(
            "iterative_planner",
            extra={
                "iterative_planner_event": {
                    "component": "iterative_planner",
                    "stage": "planning",
                    "round": context.next_round,
                    "decision": plan.decision.value,
                    "action_count": len(plan.actions),
                }
            },
        )
        return plan
