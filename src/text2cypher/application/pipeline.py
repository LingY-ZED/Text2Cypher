"""应用层的依赖感知 Text2Cypher 用例编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

from text2cypher.components.cypher_parameter_guard import CypherParameterGuard
from text2cypher.components.recovery_logging import log_correction_event
from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherOutputContractError,
    CypherParseError,
    CypherValidationError,
    DependencyBindingError,
    LLMGenerationError,
    Neo4jAccessError,
    Neo4jConnectionError,
    PromptBuildError,
    QuestionValidationError,
    SubQueryExecutionError,
    Text2CypherError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureKind,
    DependencyParameter,
    GraphSchema,
    QueryResult,
    QuestionDecomposition,
    SubQueryError,
    SubQueryResponse,
    SubQueryStatus,
    SubQuestionPlan,
    Text2CypherResponse,
    Text2CypherStatus,
)
from text2cypher.domain.ports import (
    CypherCorrector,
    CypherExecutor,
    CypherParser,
    CypherValidator,
    FewShotRouter,
    LLMClient,
    PromptBuilder,
    QuestionDecomposer,
    ResultFormatter,
    SchemaFetcher,
)

_LOGGER = logging.getLogger(__name__)


class Text2CypherPipeline:
    """以显式 DAG 依赖按层并行运行最多三个只读子查询。"""

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
        question_decomposer: QuestionDecomposer | None = None,
        few_shot_router: FewShotRouter | None = None,
        cypher_corrector: CypherCorrector | None = None,
        recover_empty_results: bool = False,
        max_subquery_workers: int = 3,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if not 1 <= max_subquery_workers <= 3:
            raise ValueError("max_subquery_workers 必须在 1 到 3 之间")
        self._schema_fetcher = schema_fetcher
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._cypher_parser = cypher_parser
        self._cypher_validator = cypher_validator
        self._cypher_executor = cypher_executor
        self._result_formatter = result_formatter
        self._question_decomposer = question_decomposer
        self._few_shot_router = few_shot_router
        self._cypher_corrector = cypher_corrector
        self._recover_empty_results = recover_empty_results
        self._max_subquery_workers = max_subquery_workers
        self._parameter_guard = CypherParameterGuard()
        self._close_callback = close_callback
        self._closed = False

    def run(self, question: str) -> Text2CypherResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("问题不能为空")

        schema = self._schema_fetcher.fetch()
        decomposition = (
            self._question_decomposer.decompose(normalized_question, schema)
            if self._question_decomposer is not None
            else QuestionDecomposition.original(normalized_question)
        )
        sub_queries = self._run_decomposition(schema, decomposition)
        successful_count = sum(
            item.status is SubQueryStatus.SUCCESS for item in sub_queries
        )
        if not successful_count:
            raise SubQueryExecutionError("所有子查询均未成功执行")
        status = (
            Text2CypherStatus.SUCCESS
            if successful_count == len(sub_queries)
            else Text2CypherStatus.PARTIAL
        )
        formatted = self._result_formatter.format(normalized_question, sub_queries)
        return Text2CypherResponse(
            question=normalized_question,
            sub_queries=sub_queries,
            formatted=formatted,
            status=status,
        )

    def _run_decomposition(
        self,
        schema: GraphSchema,
        decomposition: QuestionDecomposition,
    ) -> tuple[SubQueryResponse, ...]:
        required_columns = self._required_output_columns(decomposition)
        pending = list(decomposition.sub_questions)
        outcomes: dict[str, SubQueryResponse] = {}

        while pending:
            blocked = [
                plan
                for plan in pending
                if any(
                    source_id in outcomes
                    and outcomes[source_id].status is not SubQueryStatus.SUCCESS
                    for source_id in plan.depends_on
                )
            ]
            for plan in blocked:
                outcomes[plan.id] = self._blocked_response(plan)
            pending = [plan for plan in pending if plan not in blocked]
            if not pending:
                break

            ready = [
                plan
                for plan in pending
                if all(source_id in outcomes for source_id in plan.depends_on)
            ]
            if not ready:
                raise AssertionError("已校验的子问题依赖图必须存在 ready 节点")

            futures: dict[Future[SubQueryResponse], SubQuestionPlan] = {}
            fatal_access_error: Neo4jAccessError | None = None
            with ThreadPoolExecutor(
                max_workers=self._max_subquery_workers,
                thread_name_prefix="text2cypher-subquery",
            ) as executor:
                for plan in ready:
                    try:
                        specifications, parameters, suppress_empty_correction = (
                            self._dependency_context(plan, outcomes)
                        )
                    except DependencyBindingError as error:
                        outcomes[plan.id] = self._failed_response(
                            plan,
                            self._specifications_for_plan(plan, outcomes),
                            error,
                        )
                        continue
                    future = executor.submit(
                        self._run_sub_query,
                        schema,
                        plan,
                        specifications,
                        parameters,
                        required_columns[plan.id],
                        suppress_empty_correction,
                    )
                    futures[future] = plan

                for future in as_completed(futures):
                    plan = futures[future]
                    try:
                        outcomes[plan.id] = future.result()
                    except Neo4jAccessError as error:
                        fatal_access_error = error
                    except Text2CypherError as error:
                        outcomes[plan.id] = self._failed_response(
                            plan,
                            self._specifications_for_plan(plan, outcomes),
                            error,
                        )

            if fatal_access_error is not None:
                raise fatal_access_error
            pending = [plan for plan in pending if plan not in ready]

        return tuple(outcomes[plan.id] for plan in decomposition.sub_questions)

    @staticmethod
    def _required_output_columns(
        decomposition: QuestionDecomposition,
    ) -> dict[str, tuple[str, ...]]:
        columns_by_source: dict[str, list[str]] = {
            plan.id: [] for plan in decomposition.sub_questions
        }
        for plan in decomposition.sub_questions:
            for dependency in plan.inputs:
                source_columns = columns_by_source[dependency.source_id]
                for column in dependency.columns:
                    if column not in source_columns:
                        source_columns.append(column)
        return {
            source_id: tuple(columns)
            for source_id, columns in columns_by_source.items()
        }

    def _dependency_context(
        self,
        plan: SubQuestionPlan,
        outcomes: Mapping[str, SubQueryResponse],
    ) -> tuple[tuple[DependencyParameter, ...], dict[str, Any], bool]:
        specifications: list[DependencyParameter] = []
        parameters: dict[str, Any] = {}
        suppress_empty_correction = False
        for dependency in plan.inputs:
            source = outcomes[dependency.source_id]
            result = source.result
            if result is None:
                raise AssertionError("成功父节点必须包含查询结果")
            if result.truncated:
                raise DependencyBindingError("dependency_source_truncated")
            if any(column not in result.columns for column in dependency.columns):
                raise DependencyBindingError("dependency_output_missing")
            rows = self._project_dependency_rows(result, dependency.columns)
            specification = DependencyParameter(
                name=f"dep_{dependency.source_id}_rows",
                source_id=dependency.source_id,
                columns=dependency.columns,
            )
            specifications.append(specification)
            parameters[specification.name] = rows
            suppress_empty_correction = suppress_empty_correction or not rows
        return tuple(specifications), parameters, suppress_empty_correction

    @staticmethod
    def _project_dependency_rows(
        result: QueryResult,
        columns: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in result.rows:
            projected: dict[str, Any] = {}
            for column in columns:
                value = row.get(column)
                if column not in row:
                    raise DependencyBindingError("dependency_output_missing")
                if not Text2CypherPipeline._is_bindable_scalar(value):
                    raise DependencyBindingError("dependency_value_unsupported")
                projected[column] = value
            rows.append(projected)
        return rows

    @staticmethod
    def _is_bindable_scalar(value: Any) -> bool:
        return value is None or isinstance(value, (str, int, float, bool))

    @staticmethod
    def _specifications_for_plan(
        plan: SubQuestionPlan,
        outcomes: Mapping[str, SubQueryResponse],
    ) -> tuple[DependencyParameter, ...]:
        del outcomes
        return tuple(
            DependencyParameter(
                name=f"dep_{dependency.source_id}_rows",
                source_id=dependency.source_id,
                columns=dependency.columns,
            )
            for dependency in plan.inputs
        )

    def _run_sub_query(
        self,
        schema: GraphSchema,
        plan: SubQuestionPlan,
        specifications: tuple[DependencyParameter, ...],
        parameters: Mapping[str, Any],
        required_output_columns: tuple[str, ...],
        suppress_empty_correction: bool,
    ) -> SubQueryResponse:
        """运行一个已具备全部父参数的子查询分支。"""

        examples = (
            self._few_shot_router.route(plan.question, schema)
            if self._few_shot_router is not None
            else ()
        )
        prompt = self._build_prompt(
            schema,
            plan.question,
            examples,
            specifications,
            required_output_columns,
        )
        llm_response = self._llm_client.generate(prompt)
        try:
            cypher = self._cypher_parser.parse(llm_response.content)
        except CypherParseError:
            if self._cypher_corrector is None:
                raise
            cypher, result = self._correct_and_execute(
                prompt,
                llm_response.content,
                CypherFailureKind.PARSE,
                parameters,
                specifications,
                required_output_columns,
            )
        else:
            try:
                result = self._validate_and_execute(
                    cypher,
                    parameters,
                    specifications,
                    required_output_columns,
                )
            except CypherOutputContractError:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    CypherFailureKind.OUTPUT_CONTRACT,
                    parameters,
                    specifications,
                    required_output_columns,
                )
            except CypherValidationError:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    CypherFailureKind.VALIDATION,
                    parameters,
                    specifications,
                    required_output_columns,
                )
            except CypherExecutionError:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    CypherFailureKind.EXECUTION,
                    parameters,
                    specifications,
                    required_output_columns,
                )

        if (
            not result.rows
            and self._recover_empty_results
            and not suppress_empty_correction
            and self._cypher_corrector is not None
        ):
            cypher, result = self._recover_empty_result(
                prompt,
                cypher,
                result,
                parameters,
                specifications,
                required_output_columns,
            )
        return SubQueryResponse(
            question=plan.question,
            cypher=cypher,
            result=result,
            id=plan.id,
            depends_on=plan.depends_on,
            parameter_sources={
                specification.name: specification
                for specification in specifications
            },
        )

    def _build_prompt(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[Any, ...],
        specifications: tuple[DependencyParameter, ...],
        required_output_columns: tuple[str, ...],
    ) -> ChatPrompt:
        if not specifications and not required_output_columns:
            return self._prompt_builder.build(schema, question, examples)
        return self._prompt_builder.build(
            schema,
            question,
            examples,
            dependency_parameters=specifications,
            required_output_columns=required_output_columns,
        )

    def _validate_and_execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any],
        specifications: tuple[DependencyParameter, ...],
        required_output_columns: tuple[str, ...],
    ) -> QueryResult:
        self._parameter_guard.validate(cypher, parameters, specifications)
        if parameters:
            self._cypher_validator.validate(cypher, parameters)
            result = self._cypher_executor.execute(cypher, parameters)
        else:
            self._cypher_validator.validate(cypher)
            result = self._cypher_executor.execute(cypher)
        self._ensure_required_output_columns(result, required_output_columns)
        return result

    @staticmethod
    def _ensure_required_output_columns(
        result: QueryResult,
        required_output_columns: tuple[str, ...],
    ) -> None:
        if any(column not in result.columns for column in required_output_columns):
            raise CypherOutputContractError("Cypher 未返回依赖所需列")

    def _correct_and_execute(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure_kind: CypherFailureKind,
        parameters: Mapping[str, Any],
        specifications: tuple[DependencyParameter, ...],
        required_output_columns: tuple[str, ...],
    ) -> tuple[str, QueryResult]:
        """仅执行一次修正，并让修正版重新通过完整的依赖安全链路。"""

        corrector = self._cypher_corrector
        if corrector is None:
            raise AssertionError("纠错调用前必须注入 CypherCorrector")
        log_correction_event(
            _LOGGER,
            event="cypher_correction_started",
            reason=failure_kind.value,
            outcome="started",
        )
        try:
            corrected = corrector.correct(
                base_prompt,
                failed_candidate,
                failure_kind,
            )
            cypher = self._cypher_parser.parse(corrected.content)
            result = self._validate_and_execute(
                cypher,
                parameters,
                specifications,
                required_output_columns,
            )
        except Exception:
            log_correction_event(
                _LOGGER,
                event="cypher_correction_exhausted",
                reason=failure_kind.value,
                outcome="failed",
            )
            raise
        log_correction_event(
            _LOGGER,
            event="cypher_correction_succeeded",
            reason=failure_kind.value,
            outcome="corrected",
        )
        return cypher, result

    def _recover_empty_result(
        self,
        base_prompt: ChatPrompt,
        original_cypher: str,
        original_result: QueryResult,
        parameters: Mapping[str, Any],
        specifications: tuple[DependencyParameter, ...],
        required_output_columns: tuple[str, ...],
    ) -> tuple[str, QueryResult]:
        """尽力复核空结果；无安全改进时保留原结果。"""

        try:
            corrected_cypher, corrected_result = self._correct_and_execute(
                base_prompt,
                original_cypher,
                CypherFailureKind.EMPTY_RESULT,
                parameters,
                specifications,
                required_output_columns,
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
    def _failed_response(
        plan: SubQuestionPlan,
        specifications: tuple[DependencyParameter, ...],
        error: Text2CypherError,
    ) -> SubQueryResponse:
        return SubQueryResponse(
            question=plan.question,
            cypher=None,
            result=None,
            id=plan.id,
            depends_on=plan.depends_on,
            parameter_sources={
                specification.name: specification
                for specification in specifications
            },
            status=SubQueryStatus.FAILED,
            error=SubQueryError(
                kind=Text2CypherPipeline._error_kind(error),
                message="子查询未能安全完成执行",
            ),
        )

    @staticmethod
    def _blocked_response(plan: SubQuestionPlan) -> SubQueryResponse:
        specifications = tuple(
            DependencyParameter(
                name=f"dep_{dependency.source_id}_rows",
                source_id=dependency.source_id,
                columns=dependency.columns,
            )
            for dependency in plan.inputs
        )
        return SubQueryResponse(
            question=plan.question,
            cypher=None,
            result=None,
            id=plan.id,
            depends_on=plan.depends_on,
            parameter_sources={
                specification.name: specification
                for specification in specifications
            },
            status=SubQueryStatus.BLOCKED,
            error=SubQueryError(
                kind="dependency_failed",
                message="依赖子查询未成功完成",
            ),
        )

    @staticmethod
    def _error_kind(error: Text2CypherError) -> str:
        if isinstance(error, DependencyBindingError):
            return error.kind
        if isinstance(error, LLMGenerationError):
            return "generation_failed"
        if isinstance(error, CypherParseError):
            return "parse_failed"
        if isinstance(error, CypherOutputContractError):
            return "output_contract_failed"
        if isinstance(error, CypherValidationError):
            return "validation_failed"
        if isinstance(error, CypherExecutionError):
            return "execution_failed"
        if isinstance(error, Neo4jConnectionError):
            return "connection_failed"
        if isinstance(error, PromptBuildError):
            return "prompt_failed"
        return "subquery_failed"

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
