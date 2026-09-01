"""旧 QuestionDecomposer API 到 Primary Agent 的兼容适配。"""

from __future__ import annotations

from text2cypher.application.primary_agent import LLMPrimaryAgent
from text2cypher.domain.models import GraphSchema, QuestionDecomposition
from text2cypher.domain.ports import LLMClient, PrimaryAgent


class LLMQuestionDecomposer:
    """保留旧 `decompose` 签名的弃用兼容包装器。"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        review_llm_client: LLMClient | None = None,
        max_subquestions: int = 3,
        prompt_builder: object | None = None,
        response_parser: object | None = None,
        review_prompt_builder: object | None = None,
        review_response_parser: object | None = None,
        primary_agent: PrimaryAgent | None = None,
    ) -> None:
        """接受旧构造参数，但不再执行 Schema 感知拆分或 Reviewer 调用。"""

        del (
            review_llm_client,
            prompt_builder,
            response_parser,
            review_prompt_builder,
            review_response_parser,
        )
        self._primary_agent = primary_agent or LLMPrimaryAgent(
            llm_client,
            max_queries=max_subquestions,
        )

    def decompose(
        self,
        question: str,
        schema: GraphSchema,
    ) -> QuestionDecomposition:
        """忽略旧 Schema 参数，并投影新的结构化计划。"""

        del schema
        plan = self._primary_agent.plan(question)
        return QuestionDecomposition(
            original_question=plan.original_question,
            sub_questions=plan.sub_questions,
        )
