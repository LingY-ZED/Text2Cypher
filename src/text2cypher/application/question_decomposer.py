"""基于共享 LLMClient 的 Schema 感知问题拆分。"""

from __future__ import annotations

import logging
import re

from text2cypher.application.question_plan_reviewer import LLMQuestionPlanReviewer
from text2cypher.components.question_decomposition import (
    QuestionDecompositionPromptBuilder,
    QuestionDecompositionResponseParser,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import GraphSchema, QuestionDecomposition
from text2cypher.domain.ports import LLMClient

_LOGGER = logging.getLogger(__name__)

_DEPENDENCY_SIGNAL = re.compile(
    r"先|再|然后|之后|基于|上述|这些|first|then|after|based on|those|previous",
    re.IGNORECASE,
)
_NONSCALAR_SIGNAL = re.compile(
    r"列表|集合|数组|Map|map|collect|list<",
    re.IGNORECASE,
)
_PARALLEL_ROOT_SIGNAL = re.compile(r"分别|各自|respectively|each", re.IGNORECASE)
_PRESERVE_DATA_FLOW_REASONS = frozenset(
    {
        "dependency_signal_without_plan",
        "parallel_roots_with_dependency",
    }
)


class LLMQuestionDecomposer:
    """把一个问题安全规划为一到三个带显式依赖的子问题。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        max_subquestions: int = 3,
        prompt_builder: QuestionDecompositionPromptBuilder | None = None,
        response_parser: QuestionDecompositionResponseParser | None = None,
        reviewer: LLMQuestionPlanReviewer | None = None,
    ) -> None:
        if not 2 <= max_subquestions <= 3:
            raise ValueError("max_subquestions 必须在 2 到 3 之间")
        self._llm_client = llm_client
        self._max_subquestions = max_subquestions
        self._prompt_builder = (
            prompt_builder or QuestionDecompositionPromptBuilder()
        )
        self._response_parser = (
            response_parser or QuestionDecompositionResponseParser()
        )
        self._reviewer = reviewer or LLMQuestionPlanReviewer(
            llm_client,
            max_subquestions=max_subquestions,
        )

    def decompose(
        self,
        question: str,
        schema: GraphSchema,
    ) -> QuestionDecomposition:
        normalized_question = question.strip()
        fallback = QuestionDecomposition.original(normalized_question)
        try:
            prompt = self._prompt_builder.build(
                schema,
                normalized_question,
                self._max_subquestions,
            )
            response = self._llm_client.generate(prompt)
        except (LLMGenerationError, ValueError) as error:
            _LOGGER.warning(
                "QuestionDecomposer 失败，回退原问题：%s",
                type(error).__name__,
            )
            return fallback

        try:
            decomposition = self._response_parser.parse(
                response.content,
                normalized_question,
                self._max_subquestions,
            )
        except (LLMGenerationError, ValueError) as error:
            _LOGGER.warning(
                "QuestionDecomposer 失败，转入一次性审查：%s",
                type(error).__name__,
            )
            return self._review_or_fallback(
                normalized_question,
                schema,
                response.content,
                "invalid_plan",
                fallback,
            )

        review_reason = self._review_reason(normalized_question, decomposition)
        if review_reason is None:
            return decomposition
        return self._review_or_fallback(
            normalized_question,
            schema,
            response.content,
            review_reason,
            fallback,
            candidate_decomposition=decomposition,
        )

    def _review_or_fallback(
        self,
        question: str,
        schema: GraphSchema,
        candidate_plan: str,
        reason: str,
        fallback: QuestionDecomposition,
        *,
        candidate_decomposition: QuestionDecomposition | None = None,
    ) -> QuestionDecomposition:
        try:
            reviewed = self._reviewer.review(
                question,
                schema,
                candidate_plan,
                reason,
            )
        except (LLMGenerationError, ValueError) as error:
            _LOGGER.warning(
                "QuestionDecomposer 审查失败，回退原问题：%s",
                type(error).__name__,
            )
            return fallback
        if (
            reason in _PRESERVE_DATA_FLOW_REASONS
            and not reviewed.decomposed
            and candidate_decomposition is not None
            and candidate_decomposition.decomposed
        ):
            _LOGGER.warning(
                "QuestionDecomposer 审查移除了明确结果数据流，保留候选计划"
            )
            return candidate_decomposition
        return reviewed

    @staticmethod
    def _review_reason(
        question: str,
        decomposition: QuestionDecomposition,
    ) -> str | None:
        if decomposition.decomposed:
            if (
                _DEPENDENCY_SIGNAL.search(question)
                and _PARALLEL_ROOT_SIGNAL.search(question)
            ):
                return "parallel_roots_with_dependency"
            return "multi_node_plan"
        if _DEPENDENCY_SIGNAL.search(question):
            return "dependency_signal_without_plan"
        if _NONSCALAR_SIGNAL.search(question):
            return "non_scalar_output_risk"
        return None
