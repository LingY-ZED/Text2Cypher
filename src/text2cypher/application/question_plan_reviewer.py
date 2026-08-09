"""使用共享 LLMClient 对问题拆分计划进行一次性审查。"""

from __future__ import annotations

from text2cypher.components.question_decomposition import (
    QuestionDecompositionResponseParser,
    QuestionPlanReviewPromptBuilder,
)
from text2cypher.domain.models import GraphSchema, QuestionDecomposition
from text2cypher.domain.ports import LLMClient


class LLMQuestionPlanReviewer:
    """生成候选计划的单次修正版，不负责重试或回退。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        max_subquestions: int = 3,
        prompt_builder: QuestionPlanReviewPromptBuilder | None = None,
        response_parser: QuestionDecompositionResponseParser | None = None,
    ) -> None:
        if not 2 <= max_subquestions <= 3:
            raise ValueError("max_subquestions 必须在 2 到 3 之间")
        self._llm_client = llm_client
        self._max_subquestions = max_subquestions
        self._prompt_builder = prompt_builder or QuestionPlanReviewPromptBuilder()
        self._response_parser = (
            response_parser or QuestionDecompositionResponseParser()
        )

    def review(
        self,
        question: str,
        schema: GraphSchema,
        candidate_plan: str,
        reason: str,
    ) -> QuestionDecomposition:
        prompt = self._prompt_builder.build(
            schema,
            question,
            candidate_plan,
            reason,
            self._max_subquestions,
        )
        response = self._llm_client.generate(prompt)
        return self._response_parser.parse(
            response.content,
            question,
            self._max_subquestions,
        )
