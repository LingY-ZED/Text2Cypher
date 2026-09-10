"""基于共享 LLMClient 的 IterativeRuntime Answerer。"""

from __future__ import annotations

import logging

from text2cypher.components.iterative_answerer import (
    IterativeAnswerPromptBuilder,
    ResultSummaryResponseParser,
    TemplateIterativeAnswerer,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.iterative import IterativeAnswerContext
from text2cypher.domain.models import (
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
)
from text2cypher.domain.ports import LLMClient

_LOGGER = logging.getLogger(__name__)


class LLMIterativeAnswerer:
    """结束时只调用一次 LLM；预期失败时使用确定性部分答案。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        enabled: bool = True,
        max_input_chars: int = 12000,
        prompt_builder: IterativeAnswerPromptBuilder | None = None,
        response_parser: ResultSummaryResponseParser | None = None,
        template_answerer: TemplateIterativeAnswerer | None = None,
    ) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled必须是bool")
        if type(max_input_chars) is not int or max_input_chars <= 0:
            raise ValueError("max_input_chars必须是正整数")
        self._llm_client = llm_client
        self._enabled = enabled
        self._max_input_chars = max_input_chars
        self._prompt_builder = prompt_builder or IterativeAnswerPromptBuilder()
        self._response_parser = response_parser or ResultSummaryResponseParser()
        self._template_answerer = template_answerer or TemplateIterativeAnswerer()

    def answer(self, context: IterativeAnswerContext) -> ResultSummary:
        if not self._enabled:
            return self._fallback(context, ResultSummaryFallbackReason.DISABLED)
        prompt = self._prompt_builder.build(context)
        if len(prompt.system) + len(prompt.user) > self._max_input_chars:
            return self._fallback(
                context,
                ResultSummaryFallbackReason.INPUT_TOO_LARGE,
            )
        try:
            response = self._llm_client.generate(prompt)
        except LLMGenerationError:
            return self._fallback(context, ResultSummaryFallbackReason.LLM_FAILURE)
        try:
            answer = self._response_parser.parse(response.content)
        except ValueError:
            return self._fallback(context, ResultSummaryFallbackReason.INVALID_RESPONSE)
        summary = ResultSummary(answer, ResultSummaryMode.LLM)
        self._log("generated", summary)
        return summary

    def _fallback(
        self,
        context: IterativeAnswerContext,
        reason: ResultSummaryFallbackReason,
    ) -> ResultSummary:
        summary = ResultSummary(
            self._template_answerer.answer(context),
            ResultSummaryMode.TEMPLATE,
            reason,
        )
        self._log("fallback", summary)
        return summary

    @staticmethod
    def _log(outcome: str, summary: ResultSummary) -> None:
        _LOGGER.info(
            "iterative_answer",
            extra={
                "iterative_answer_event": {
                    "component": "iterative_answerer",
                    "stage": "answer",
                    "outcome": outcome,
                    "mode": summary.mode.value,
                    "reason": (
                        None
                        if summary.fallback_reason is None
                        else summary.fallback_reason.value
                    ),
                }
            },
        )
