"""保留公开响应与资源生命周期的 Text2Cypher 兼容门面。"""

from __future__ import annotations

from collections.abc import Callable

from text2cypher.domain.models import Text2CypherResponse
from text2cypher.domain.ports import (
    CypherCorrector,
    CypherExecutor,
    CypherParser,
    CypherValidator,
    FewShotRouter,
    GraphQueryEngine,
    LLMClient,
    PrimaryAgent,
    PromptBuilder,
    QuestionDecomposer,
    ReadOnlyCypherGateway,
    ResultFormatter,
    ResultSummarizer,
    SchemaFetcher,
)
from text2cypher.runtime.single_round import SingleRoundRuntime


class Text2CypherPipeline:
    """兼容旧构造参数，将单轮执行委托给 Runtime 后再格式化结果。"""

    def __init__(
        self,
        *,
        result_formatter: ResultFormatter,
        schema_fetcher: SchemaFetcher | None = None,
        prompt_builder: PromptBuilder | None = None,
        llm_client: LLMClient | None = None,
        cypher_parser: CypherParser | None = None,
        cypher_validator: CypherValidator | None = None,
        cypher_executor: CypherExecutor | None = None,
        read_only_cypher_gateway: ReadOnlyCypherGateway | None = None,
        graph_query_engine: GraphQueryEngine | None = None,
        result_summarizer: ResultSummarizer | None = None,
        primary_agent: PrimaryAgent | None = None,
        question_decomposer: QuestionDecomposer | None = None,
        few_shot_router: FewShotRouter | None = None,
        cypher_corrector: CypherCorrector | None = None,
        recover_empty_results: bool = False,
        single_round_runtime: SingleRoundRuntime | None = None,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if single_round_runtime is None:
            self._single_round_runtime = self._build_legacy_runtime(
                schema_fetcher=schema_fetcher,
                prompt_builder=prompt_builder,
                llm_client=llm_client,
                cypher_parser=cypher_parser,
                cypher_validator=cypher_validator,
                cypher_executor=cypher_executor,
                read_only_cypher_gateway=read_only_cypher_gateway,
                graph_query_engine=graph_query_engine,
                result_summarizer=result_summarizer,
                primary_agent=primary_agent,
                question_decomposer=question_decomposer,
                few_shot_router=few_shot_router,
                cypher_corrector=cypher_corrector,
                recover_empty_results=recover_empty_results,
            )
        else:
            if primary_agent is not None or question_decomposer is not None:
                raise ValueError("注入 Runtime 时不能再注入规划器")
            self._single_round_runtime = single_round_runtime
        self._result_formatter = result_formatter
        self._close_callback = close_callback
        self._closed = False

    @staticmethod
    def _build_legacy_runtime(
        *,
        schema_fetcher: SchemaFetcher | None,
        prompt_builder: PromptBuilder | None,
        llm_client: LLMClient | None,
        cypher_parser: CypherParser | None,
        cypher_validator: CypherValidator | None,
        cypher_executor: CypherExecutor | None,
        read_only_cypher_gateway: ReadOnlyCypherGateway | None,
        graph_query_engine: GraphQueryEngine | None,
        result_summarizer: ResultSummarizer | None,
        primary_agent: PrimaryAgent | None,
        question_decomposer: QuestionDecomposer | None,
        few_shot_router: FewShotRouter | None,
        cypher_corrector: CypherCorrector | None,
        recover_empty_results: bool,
    ) -> SingleRoundRuntime:
        if any(
            dependency is None
            for dependency in (
                schema_fetcher,
                prompt_builder,
                llm_client,
                cypher_parser,
                cypher_validator,
                cypher_executor,
            )
        ):
            raise ValueError("未注入 Runtime 时必须提供完整的兼容依赖")
        assert schema_fetcher is not None
        assert prompt_builder is not None
        assert llm_client is not None
        assert cypher_parser is not None
        assert cypher_validator is not None
        assert cypher_executor is not None
        from text2cypher.application.factory import (
            PipelineComponents,
            build_single_round_runtime,
        )

        return build_single_round_runtime(
            PipelineComponents(
                schema_fetcher=schema_fetcher,
                prompt_builder=prompt_builder,
                llm_client=llm_client,
                cypher_parser=cypher_parser,
                cypher_validator=cypher_validator,
                cypher_executor=cypher_executor,
                read_only_cypher_gateway=read_only_cypher_gateway,
                graph_query_engine=graph_query_engine,
                few_shot_router=few_shot_router,
                cypher_corrector=cypher_corrector,
                recover_empty_results=recover_empty_results,
                result_summarizer=result_summarizer,
                primary_agent=primary_agent,
                question_decomposer=question_decomposer,
            )
        )

    def run(self, question: str) -> Text2CypherResponse:
        """运行单轮 Runtime，并按旧公开格式返回。"""

        run = self._single_round_runtime.run(question)
        formatted = (
            self._result_formatter.format(
                run.question,
                run.sub_queries,
                run.summary,
            )
            if run.summary is not None
            else self._result_formatter.format(run.question, run.sub_queries)
        )
        return Text2CypherResponse(
            question=run.question,
            sub_queries=run.sub_queries,
            formatted=formatted,
            summary=run.summary,
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
