"""基于共享 LLMClient 的结果自然语言总结编排。"""

from __future__ import annotations

import logging

from text2cypher.components.result_summarizer import (
    ResultSummaryPromptBuilder,
    ResultSummaryResponseParser,
    TemplateResultSummarizer,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import (
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
    SubQueryResponse,
)
from text2cypher.domain.ports import LLMClient

_LOGGER = logging.getLogger(__name__)


class LLMResultSummarizer:
    """一次成功查询最多调用一次 LLM，并在预期失败时模板降级。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        enabled: bool = True,
        max_input_chars: int = 16000,
        prompt_builder: ResultSummaryPromptBuilder | None = None,
        response_parser: ResultSummaryResponseParser | None = None,
        template_summarizer: TemplateResultSummarizer | None = None,
    ) -> None:
        if max_input_chars <= 0:
            raise ValueError("总结输入字符上限必须为正数")
        self._llm_client = llm_client
        self._enabled = enabled
        self._max_input_chars = max_input_chars
        self._prompt_builder = prompt_builder or ResultSummaryPromptBuilder()
        self._response_parser = response_parser or ResultSummaryResponseParser()
        self._template_summarizer = template_summarizer or TemplateResultSummarizer()

    def summarize(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> ResultSummary:
        normalized_sub_queries = tuple(sub_queries)
        self._validate_input(question, normalized_sub_queries)

        if not self._enabled:
            return self._fallback(
                question,
                normalized_sub_queries,
                ResultSummaryFallbackReason.DISABLED,
            )
        if all(not sub_query.result.rows for sub_query in normalized_sub_queries):
            return self._fallback(
                question,
                normalized_sub_queries,
                ResultSummaryFallbackReason.EMPTY_RESULT,
            )

        prompt = self._prompt_builder.build(question, normalized_sub_queries)
        if len(prompt.system) + len(prompt.user) > self._max_input_chars:
            return self._fallback(
                question,
                normalized_sub_queries,
                ResultSummaryFallbackReason.INPUT_TOO_LARGE,
            )

        try:
            response = self._llm_client.generate(prompt)
        except LLMGenerationError:
            return self._fallback(
                question,
                normalized_sub_queries,
                ResultSummaryFallbackReason.LLM_FAILURE,
            )
        try:
            answer = self._response_parser.parse(response.content)
        except ValueError:
            return self._fallback(
                question,
                normalized_sub_queries,
                ResultSummaryFallbackReason.INVALID_RESPONSE,
            )

        summary = ResultSummary(answer=answer, mode=ResultSummaryMode.LLM)
        self._log(outcome="generated", summary=summary)
        return summary

    @staticmethod
    def _validate_input(
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> None:
        if not question.strip():
            raise ValueError("问题不能为空")
        if not 1 <= len(sub_queries) <= 3:
            raise ValueError("子查询结果数量必须在 1 到 3 之间")

    def _fallback(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
        reason: ResultSummaryFallbackReason,
    ) -> ResultSummary:
        summary = ResultSummary(
            answer=self._template_summarizer.summarize(question, sub_queries),
            mode=ResultSummaryMode.TEMPLATE,
            fallback_reason=reason,
        )
        self._log(outcome="fallback", summary=summary)
        return summary

    @staticmethod
    def _log(*, outcome: str, summary: ResultSummary) -> None:
        reason = summary.fallback_reason
        level = (
            logging.WARNING
            if reason
            in {
                ResultSummaryFallbackReason.LLM_FAILURE,
                ResultSummaryFallbackReason.INVALID_RESPONSE,
            }
            else logging.INFO
        )
        _LOGGER.log(
            level,
            "result_summary",
            extra={
                "result_summary_event": {
                    "component": "result_summarizer",
                    "stage": "summary",
                    "outcome": outcome,
                    "mode": summary.mode.value,
                    "reason": reason.value if reason is not None else None,
                }
            },
        )
