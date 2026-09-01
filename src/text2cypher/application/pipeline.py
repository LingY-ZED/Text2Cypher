"""应用层的确定性 Text2Cypher 用例编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from text2cypher.components.cypher_correction import CypherCorrectionPromptBuilder
from text2cypher.components.recovery_logging import log_correction_event
from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherParseError,
    CypherValidationError,
    LLMGenerationError,
    Neo4jAccessError,
    Neo4jConnectionError,
    QuestionValidationError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    CypherFailureKind,
    CypherFailureSource,
    GraphSchema,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    Text2CypherResponse,
)
from text2cypher.domain.ports import (
    CypherCorrector,
    CypherExecutor,
    CypherParser,
    CypherValidator,
    FewShotRouter,
    LLMClient,
    PrimaryAgent,
    PromptBuilder,
    QuestionDecomposer,
    ResultFormatter,
    ResultSummarizer,
    SchemaFetcher,
)

_LOGGER = logging.getLogger(__name__)


class Text2CypherPipeline:
    """使用注入实现运行七阶段 Text2Cypher 流程。"""

    def __init__(
        self,
        *,
        schema_fetcher: SchemaFetcher,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        cypher_parser: CypherParser,
        cypher_validator: CypherValidator,
        cypher_executor: CypherExecutor,
        result_formatter: ResultFormatter,
        result_summarizer: ResultSummarizer | None = None,
        primary_agent: PrimaryAgent | None = None,
        question_decomposer: QuestionDecomposer | None = None,
        few_shot_router: FewShotRouter | None = None,
        cypher_corrector: CypherCorrector | None = None,
        recover_empty_results: bool = False,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if primary_agent is not None and question_decomposer is not None:
            raise ValueError("primary_agent 与 question_decomposer 不能同时注入")
        self._schema_fetcher = schema_fetcher
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._cypher_parser = cypher_parser
        self._cypher_validator = cypher_validator
        self._cypher_executor = cypher_executor
        self._result_formatter = result_formatter
        self._result_summarizer = result_summarizer
        self._primary_agent = primary_agent
        self._question_decomposer = question_decomposer
        self._few_shot_router = few_shot_router
        self._cypher_corrector = cypher_corrector
        self._recover_empty_results = recover_empty_results
        self._close_callback = close_callback
        self._closed = False

    def run(self, question: str) -> Text2CypherResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("问题不能为空")

        schema = self._schema_fetcher.fetch()
        plan = self._plan_question(normalized_question, schema)
        sub_queries = tuple(
            self._run_sub_query(schema, sub_question)
            for sub_question in plan.sub_questions
        )
        summary = (
            self._result_summarizer.summarize(normalized_question, sub_queries)
            if self._result_summarizer is not None
            else None
        )
        formatted = (
            self._result_formatter.format(
                normalized_question,
                sub_queries,
                summary,
            )
            if summary is not None
            else self._result_formatter.format(normalized_question, sub_queries)
        )
        return Text2CypherResponse(
            question=normalized_question,
            sub_queries=sub_queries,
            formatted=formatted,
            summary=summary,
        )

    def _plan_question(
        self,
        question: str,
        schema: GraphSchema,
    ) -> PrimaryAgentPlan:
        """优先使用新规划端口，并将旧拆分端口适配为同一计划模型。"""

        if self._primary_agent is not None:
            return self._primary_agent.plan(question)
        if self._question_decomposer is not None:
            decomposition = self._question_decomposer.decompose(question, schema)
            return self._legacy_decomposition_plan(decomposition)
        return PrimaryAgentPlan.fallback(question)

    @staticmethod
    def _legacy_decomposition_plan(
        decomposition: QuestionDecomposition,
    ) -> PrimaryAgentPlan:
        """不改写旧拆分结果地投影为新的内部计划。"""

        queries = tuple(
            PrimaryAgentQuery(
                query_id=f"q{index}",
                question=question,
                intent="执行兼容问题拆分子查询",
                required_information=("由兼容问题拆分器确定的图数据",),
            )
            for index, question in enumerate(
                decomposition.sub_questions,
                start=1,
            )
        )
        return PrimaryAgentPlan(
            original_question=decomposition.original_question,
            analysis_summary="使用兼容问题拆分器生成单轮计划",
            queries=queries,
        )

    def _run_sub_query(
        self,
        schema: GraphSchema,
        question: str,
    ) -> SubQueryResponse:
        """按固定顺序生成、校验并执行一个独立子问题。"""

        examples = (
            self._few_shot_router.route(question, schema)
            if self._few_shot_router is not None
            else ()
        )
        prompt = self._prompt_builder.build(
            schema,
            question,
            examples,
        )
        llm_response = self._llm_client.generate(prompt)
        try:
            cypher = self._cypher_parser.parse(llm_response.content)
        except CypherParseError as error:
            if self._cypher_corrector is None:
                raise
            cypher, result = self._correct_and_execute(
                prompt,
                llm_response.content,
                self._failure_context(CypherFailureKind.PARSE, error),
            )
        else:
            try:
                self._cypher_validator.validate(cypher)
                result = self._cypher_executor.execute(cypher)
            except CypherValidationError as error:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    self._failure_context(CypherFailureKind.VALIDATION, error),
                )
            except CypherExecutionError as error:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    self._failure_context(CypherFailureKind.EXECUTION, error),
                )

        if (
            not result.rows
            and self._recover_empty_results
            and self._cypher_corrector is not None
        ):
            cypher, result = self._recover_empty_result(prompt, cypher, result)
        return SubQueryResponse(
            question=question,
            cypher=cypher,
            result=result,
        )

    def _correct_and_execute(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure: CypherFailureContext,
    ) -> tuple[str, QueryResult]:
        """仅执行一次修正，并让修正版重新通过完整的安全链路。"""

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
            cypher = self._cypher_parser.parse(corrected.content)
            self._cypher_validator.validate(cypher)
            result = self._cypher_executor.execute(cypher)
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
        return cypher, result

    def _recover_empty_result(
        self,
        base_prompt: ChatPrompt,
        original_cypher: str,
        original_result: QueryResult,
    ) -> tuple[str, QueryResult]:
        """尽力复核空结果；没有安全的非空改进时保留原结果。"""

        try:
            corrected_cypher, corrected_result = self._correct_and_execute(
                base_prompt,
                original_cypher,
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
            return original_cypher, original_result
        if corrected_result.rows:
            return corrected_cypher, corrected_result
        log_correction_event(
            _LOGGER,
            event="empty_result_original_kept",
            reason=CypherFailureKind.EMPTY_RESULT.value,
            outcome="no_improvement",
        )
        return original_cypher, original_result

    @staticmethod
    def _failure_context(
        kind: CypherFailureKind,
        error: CypherParseError | CypherValidationError | CypherExecutionError,
    ) -> CypherFailureContext:
        """优先保留适配器提取的 Neo4j 诊断，否则使用本地实际原因。"""

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

    def close(self) -> None:
        """关闭流水线持有的外部资源，重复调用安全。"""

        if self._closed:
            return
        self._closed = True
        if self._close_callback is not None:
            self._close_callback()

    def __enter__(self) -> Text2CypherPipeline:
        """进入上下文时返回当前流水线。"""

        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        """离开上下文时关闭外部资源。"""

        del exception_type, exception, traceback
        self.close()
