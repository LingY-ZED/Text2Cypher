"""一条已规划图查询的翻译、只读执行和兼容恢复编排。"""

from __future__ import annotations

import logging
import re

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
    CallChainQuerySpec,
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
    ParameterizedReadOnlyCypherGateway,
    PlannedFewShotRouter,
    PlannedPromptBuilder,
    PromptBuilder,
    ReadOnlyCypherGateway,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.graph_core.call_chain import CallChainCypherCompiler
from text2cypher.graph_core.method_query import MethodQueryCypherCompiler
from text2cypher.graph_core.readonly_cypher_gateway import (
    CandidateExecutionFailure,
)
from text2cypher.graph_core.service_dependency import ServiceDependencyCypherCompiler
from text2cypher.graph_core.service_facts import ServiceFactCypherCompiler
from text2cypher.tools.resolve_symbol import ResolveSymbolTool

_LOGGER = logging.getLogger(__name__)


class DefaultGraphQueryEngine:
    """保持现有行为地执行一条自然语言到只读图查询的完整链路。"""

    _ENTRY_API = re.compile(
        r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s，。；！？?]+)",
        flags=re.IGNORECASE,
    )
    _SERVICE_NAME = re.compile(r"\b(ts-[A-Za-z0-9-]+-service)\b")
    _QUEUE_NAME = re.compile(r"(?:名为|名叫)\s*([A-Za-z0-9_.-]+)")
    _METHOD_REFERENCE = re.compile(
        r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)",
    )
    _SIMPLE_METHOD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
    _SIMPLE_METHOD_REFERENCE = re.compile(
        r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*的?"
        r"(?:完整|端到端)?调用链",
    )

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
        deterministic = self._try_deterministic_service_dependency(primary_query)
        if deterministic is None:
            deterministic = self._try_deterministic_service_fact(primary_query)
        if deterministic is None:
            deterministic = self._try_deterministic_method_query(primary_query)
        if deterministic is not None:
            return deterministic
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

    def _try_deterministic_service_fact(
        self,
        query: PrimaryAgentQuery,
    ) -> ExecutedCypher | None:
        """Run a small fact query only when its graph semantics are explicit."""

        if query.effective_query_shape is not QueryShape.GENERAL:
            return None
        gateway = self._read_only_cypher_gateway
        if not isinstance(gateway, ParameterizedReadOnlyCypherGateway):
            return None
        compact = "".join(query.question.lower().split())
        compiler = ServiceFactCypherCompiler()
        statement = None
        queue_match = self._QUEUE_NAME.search(query.question)
        if (
            queue_match is not None
            and "消息队列" in compact
            and any(cue in compact for cue in ("多少", "几个", "数量", "数目"))
        ):
            statement = compiler.compile_message_queue_count(queue_match.group(1))
        elif "默认" in compact and "消息交换机" in compact:
            statement = compiler.compile_exchange_owners("(default)")
        else:
            service_match = self._SERVICE_NAME.search(query.question)
            if (
                service_match is not None
                and "rest" in compact
                and "哪些微服务" in compact
                and any(cue in compact for cue in ("调用目标", "当作"))
            ):
                statement = compiler.compile_rest_callers(service_match.group(1))
        if statement is None:
            return None
        executed = gateway.execute_statement(statement)
        if executed.result.truncated:
            raise CypherExecutionError("确定性服务事实查询结果超过安全行数上限")
        return executed

    def _try_deterministic_service_dependency(
        self,
        query: PrimaryAgentQuery,
    ) -> ExecutedCypher | None:
        """Compile an explicit MQ-sender to REST-target matrix when unambiguous."""

        if query.effective_query_shape is not QueryShape.GENERAL:
            return None
        compact = "".join(query.question.lower().split())
        required_cues = ("mq", "rest", "发送", "下游服务")
        if not all(cue in compact for cue in required_cues):
            return None
        gateway = self._read_only_cypher_gateway
        if not isinstance(gateway, ParameterizedReadOnlyCypherGateway):
            return None
        service_match = self._SERVICE_NAME.search(query.question)
        if service_match is None:
            return None
        executed = gateway.execute_statement(
            ServiceDependencyCypherCompiler().compile_message_senders_rest_targets(
                service_match.group(1)
            )
        )
        if executed.result.truncated:
            raise CypherExecutionError("确定性服务依赖查询结果超过安全行数上限")
        return executed

    def _try_deterministic_method_query(
        self,
        query: PrimaryAgentQuery,
    ) -> ExecutedCypher | None:
        """Run supported method shapes through the parameterized Core when possible."""

        query_shape = query.effective_query_shape
        supported_shapes = MethodQueryCypherCompiler._SUPPORTED_SHAPES | {
            QueryShape.FULL_METHOD_CALL_CHAIN
        }
        if query_shape not in supported_shapes:
            return None
        gateway = self._read_only_cypher_gateway
        if not isinstance(gateway, ParameterizedReadOnlyCypherGateway):
            return None

        resolver = ResolveSymbolTool(gateway)
        entry_api = self._entry_api_anchor(query.question)
        if query_shape is QueryShape.FULL_METHOD_CALL_CHAIN and entry_api is not None:
            http_method, api_path, service_name = entry_api
            methods = resolver.resolve_entry_api(
                http_method,
                api_path,
                service_name=service_name,
            )
        else:
            anchor = self._method_anchor(query)
            if anchor is None:
                return None
            methods = resolver.resolve_symbol(anchor)
        if not methods:
            return None

        ordered_methods = tuple(
            sorted(
                methods,
                key=lambda method: (method.qualified_name, method.graph_version),
            )
        )
        qualified_names = tuple(
            dict.fromkeys(method.qualified_name for method in ordered_methods)
        )
        graph_versions = {method.graph_version for method in ordered_methods}
        if query_shape is QueryShape.FULL_METHOD_CALL_CHAIN:
            statement = CallChainCypherCompiler().compile(
                CallChainQuerySpec(
                    anchor_qualified_name=qualified_names[0],
                    additional_anchor_qualified_names=qualified_names[1:],
                    graph_version=(
                        next(iter(graph_versions)) if len(graph_versions) == 1 else None
                    ),
                )
            )
        else:
            statement = MethodQueryCypherCompiler().compile(
                query_shape,
                ordered_methods,
            )
        executed = gateway.execute_statement(statement)
        if executed.result.truncated:
            raise CypherExecutionError("确定性方法查询结果超过安全行数上限")
        return executed

    @classmethod
    def _entry_api_anchor(
        cls,
        question: str,
    ) -> tuple[str, str, str | None] | None:
        match = cls._ENTRY_API.search(question)
        if match is None:
            return None
        service_match = cls._SERVICE_NAME.search(question)
        return (
            match.group(1).upper(),
            match.group(2),
            service_match.group(1) if service_match is not None else None,
        )

    @classmethod
    def _method_anchor(cls, query: PrimaryAgentQuery) -> str | None:
        if (
            query.anchor is not None
            and cls._SIMPLE_METHOD_NAME.fullmatch(query.anchor) is not None
        ):
            return query.anchor
        for value in (query.anchor, query.question):
            if value is None:
                continue
            match = cls._METHOD_REFERENCE.search(value)
            if match is not None:
                return match.group(1)
        simple_match = cls._SIMPLE_METHOD_REFERENCE.search(query.question)
        if simple_match is not None:
            return simple_match.group(1)
        return None

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
