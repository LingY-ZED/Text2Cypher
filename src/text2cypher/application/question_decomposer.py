"""基于共享 LLMClient 的 Schema 感知问题拆分。"""

from __future__ import annotations

import logging

from text2cypher.components.question_decomposition import (
    QuestionDecompositionPromptBuilder,
    QuestionDecompositionResponseParser,
)
from text2cypher.components.question_decomposition_review import (
    QuestionDecompositionReviewPromptBuilder,
    QuestionDecompositionReviewResponseParser,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import GraphSchema, QuestionDecomposition
from text2cypher.domain.ports import LLMClient

_LOGGER = logging.getLogger(__name__)


class LLMQuestionDecomposer:
    """把一个问题安全规划为一到三个独立子问题。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        review_llm_client: LLMClient | None = None,
        max_subquestions: int = 3,
        prompt_builder: QuestionDecompositionPromptBuilder | None = None,
        response_parser: QuestionDecompositionResponseParser | None = None,
        review_prompt_builder: (
            QuestionDecompositionReviewPromptBuilder | None
        ) = None,
        review_response_parser: (
            QuestionDecompositionReviewResponseParser | None
        ) = None,
    ) -> None:
        if not 2 <= max_subquestions <= 3:
            raise ValueError("max_subquestions 必须在 2 到 3 之间")
        self._llm_client = llm_client
        self._review_llm_client = review_llm_client or llm_client
        self._max_subquestions = max_subquestions
        self._prompt_builder = (
            prompt_builder or QuestionDecompositionPromptBuilder()
        )
        self._response_parser = (
            response_parser or QuestionDecompositionResponseParser()
        )
        self._review_prompt_builder = (
            review_prompt_builder or QuestionDecompositionReviewPromptBuilder()
        )
        self._review_response_parser = (
            review_response_parser or QuestionDecompositionReviewResponseParser()
        )

    def decompose(
        self,
        question: str,
        schema: GraphSchema,
    ) -> QuestionDecomposition:
        normalized_question = question.strip()
        fallback = QuestionDecomposition(
            original_question=normalized_question,
            sub_questions=(normalized_question,),
        )
        try:
            prompt = self._prompt_builder.build(
                schema,
                normalized_question,
                self._max_subquestions,
            )
            response = self._llm_client.generate(prompt)
            candidate = self._response_parser.parse(
                response.content,
                normalized_question,
                self._max_subquestions,
            )
        except (LLMGenerationError, ValueError) as error:
            _LOGGER.warning(
                "QuestionDecomposer 失败，回退原问题：%s",
                type(error).__name__,
            )
            return fallback

        if not candidate.decomposed:
            return candidate

        try:
            review_prompt = self._review_prompt_builder.build(
                normalized_question,
                candidate.sub_questions,
            )
            review_response = self._review_llm_client.generate(review_prompt)
            review = self._review_response_parser.parse(review_response.content)
        except (LLMGenerationError, ValueError) as error:
            self._log_review(
                outcome="fallback",
                reason=type(error).__name__,
            )
            return fallback

        if not review.valid:
            self._log_review(
                outcome="rejected",
                reason=review.reason.value,
            )
            return fallback

        self._log_review(outcome="accepted", reason=review.reason.value)
        return candidate

    @staticmethod
    def _log_review(*, outcome: str, reason: str) -> None:
        level = logging.INFO if outcome == "accepted" else logging.WARNING
        _LOGGER.log(
            level,
            "question_decomposition_review",
            extra={
                "decomposition_review_event": {
                    "component": "decomposer",
                    "stage": "review",
                    "outcome": outcome,
                    "reason": reason,
                }
            },
        )
