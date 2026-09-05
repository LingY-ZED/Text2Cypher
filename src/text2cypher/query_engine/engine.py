"""一条已规划图查询的翻译、只读执行和兼容恢复编排。"""

from __future__ import annotations

import logging

from text2cypher.components.cypher_correction import CypherCorrectionPromptBuilder
from text2cypher.components.recovery_logging import log_correction_event
from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherParseError,
    CypherValidationError,
    LLMGenerationError,
    Neo4jAccessError,
    Neo4jConnectionError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    CypherFailureKind,
    CypherFailureSource,
    ExecutedCypher,
    FewShotExample,
    GraphQueryRequest,
    PrimaryAgentQuery,
    QueryContext,
)
from text2cypher.domain.ports import (
    CypherCorrector,
    FewShotRouter,
    LLMClient,
    PlannedFewShotRouter,
    PlannedPromptBuilder,
    PromptBuilder,
    ReadOnlyCypherGateway,
)
from text2cypher.graph_core.readonly_cypher_gateway import (
    CandidateExecutionFailure,
)

_LOGGER = logging.getLogger(__name__)


class DefaultGraphQueryEngine:
    """保持现有行为地执行一条自然语言到只读图查询的完整链路。"""

    def __init__(
        self,
        *,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        read_only_cypher_gateway: ReadOnlyCypherGateway,
        few_shot_router: FewShotRouter | None = None,
        cypher_corrector: CypherCorrector | None = None,
        recover_empty_results: bool = False,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._read_only_cypher_gateway = read_only_cypher_gateway
        self._few_shot_router = few_shot_router
        self._cypher_corrector = cypher_corrector
        self._recover_empty_results = recover_empty_results

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher:
        """生成、准入并执行一条请求，保持既有一次性恢复语义。"""

        primary_query = request.as_primary_agent_query()
        examples = self._route_examples(primary_query, context)
        prompt = self._build_prompt(primary_query, context, examples)
        response = self._llm_client.generate(prompt)
        try:
            executed = self._read_only_cypher_gateway.execute_candidate(
                response.content
            )
        except CandidateExecutionFailure as failure:
            error = failure.error
            if self._cypher_corrector is None:
                raise error from None
            executed = self._correct_and_execute(
                prompt,
                failure.candidate,
                self._failure_context(self._failure_kind(error), error),
            )

        if (
            not executed.result.rows
            and self._recover_empty_results
            and self._cypher_corrector is not None
        ):
            executed = self._recover_empty_result(prompt, executed)
        return executed

    def _route_examples(
        self,
        query: PrimaryAgentQuery,
        context: QueryContext,
    ) -> tuple[FewShotExample, ...]:
        router = self._few_shot_router
        if router is None:
            return ()
        if isinstance(router, PlannedFewShotRouter):
            return router.route_planned(query, context.schema)
        return router.route(query.question, context.schema)

    def _build_prompt(
        self,
        query: PrimaryAgentQuery,
        context: QueryContext,
        examples: tuple[FewShotExample, ...],
    ) -> ChatPrompt:
        if isinstance(self._prompt_builder, PlannedPromptBuilder):
            return self._prompt_builder.build_planned(
                context.schema,
                query,
                examples,
            )
        return self._prompt_builder.build(
            context.schema,
            query.question,
            examples,
        )

    def _correct_and_execute(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure: CypherFailureContext,
    ) -> ExecutedCypher:
        corrector = self._cypher_corrector
        if corrector is None:
            raise AssertionError("纠错调用前必须注入 CypherCorrector")
        log_correction_event(
            _LOGGER,
            event="cypher_correction_started",
            reason=failure.kind.value,
            outcome="started",
        )
        try:
            corrected = corrector.correct(
                base_prompt,
                failed_candidate,
                failure,
            )
            executed = self._execute_corrected_candidate(corrected.content)
        except Exception:
            log_correction_event(
                _LOGGER,
                event="cypher_correction_exhausted",
                reason=failure.kind.value,
                outcome="failed",
            )
            raise
        log_correction_event(
            _LOGGER,
            event="cypher_correction_succeeded",
            reason=failure.kind.value,
            outcome="corrected",
        )
        return executed

    def _recover_empty_result(
        self,
        base_prompt: ChatPrompt,
        original: ExecutedCypher,
    ) -> ExecutedCypher:
        try:
            corrected = self._correct_and_execute(
                base_prompt,
                original.cypher,
                CypherFailureContext(
                    kind=CypherFailureKind.EMPTY_RESULT,
                    source=CypherFailureSource.RESULT,
                    message=CypherCorrectionPromptBuilder.fallback_failure(
                        CypherFailureKind.EMPTY_RESULT
                    ),
                ),
            )
        except (
            LLMGenerationError,
            CypherParseError,
            CypherValidationError,
            CypherExecutionError,
            Neo4jAccessError,
            Neo4jConnectionError,
        ):
            log_correction_event(
                _LOGGER,
                event="empty_result_original_kept",
                reason=CypherFailureKind.EMPTY_RESULT.value,
                outcome="recovery_failed",
            )
            return original
        if corrected.result.rows:
            return corrected
        log_correction_event(
            _LOGGER,
            event="empty_result_original_kept",
            reason=CypherFailureKind.EMPTY_RESULT.value,
            outcome="no_improvement",
        )
        return original

    def _execute_corrected_candidate(self, candidate: str) -> ExecutedCypher:
        try:
            return self._read_only_cypher_gateway.execute_candidate(candidate)
        except CandidateExecutionFailure as failure:
            raise failure.error from None

    @staticmethod
    def _failure_kind(
        error: CypherParseError | CypherValidationError | CypherExecutionError,
    ) -> CypherFailureKind:
        if isinstance(error, CypherParseError):
            return CypherFailureKind.PARSE
        if isinstance(error, CypherValidationError):
            return CypherFailureKind.VALIDATION
        return CypherFailureKind.EXECUTION

    @staticmethod
    def _failure_context(
        kind: CypherFailureKind,
        error: CypherParseError | CypherValidationError | CypherExecutionError,
    ) -> CypherFailureContext:
        if isinstance(error, (CypherValidationError, CypherExecutionError)):
            if error.failure_context is not None:
                return error.failure_context
        message = str(error).strip()
        if not message:
            message = CypherCorrectionPromptBuilder.fallback_failure(kind)
        return CypherFailureContext(
            kind=kind,
            source=CypherFailureSource.LOCAL,
            message=message,
        )
