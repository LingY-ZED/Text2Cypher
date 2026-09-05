"""应用层的确定性 Text2Cypher 用例编排。"""

from __future__ import annotations

from collections.abc import Callable

from text2cypher.domain.errors import QuestionValidationError
from text2cypher.domain.models import (
    GraphQueryRequest,
    GraphSchema,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryContext,
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
from text2cypher.graph_core.readonly_cypher_gateway import DefaultReadOnlyCypherGateway
from text2cypher.query_engine.engine import DefaultGraphQueryEngine


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
        read_only_cypher_gateway: ReadOnlyCypherGateway | None = None,
        graph_query_engine: GraphQueryEngine | None = None,
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
        read_only_gateway = (
            read_only_cypher_gateway
            or DefaultReadOnlyCypherGateway(
                cypher_parser,
                cypher_validator,
                cypher_executor,
            )
        )
        self._graph_query_engine = graph_query_engine or DefaultGraphQueryEngine(
            prompt_builder=prompt_builder,
            llm_client=llm_client,
            read_only_cypher_gateway=read_only_gateway,
            few_shot_router=few_shot_router,
            cypher_corrector=cypher_corrector,
            recover_empty_results=recover_empty_results,
        )
        self._result_formatter = result_formatter
        self._result_summarizer = result_summarizer
        self._primary_agent = primary_agent
        self._question_decomposer = question_decomposer
        self._close_callback = close_callback
        self._closed = False

    def run(self, question: str) -> Text2CypherResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("问题不能为空")

        schema = self._schema_fetcher.fetch()
        plan = self._plan_question(normalized_question, schema)
        sub_queries = tuple(
            self._run_sub_query(schema, query)
            for query in plan.queries
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
        query: PrimaryAgentQuery,
    ) -> SubQueryResponse:
        """委托单次 Graph Query Engine，并投影为现有公开子查询结果。"""

        executed = self._graph_query_engine.query(
            GraphQueryRequest.from_primary_agent_query(query),
            QueryContext(schema),
        )
        return SubQueryResponse(
            question=query.question,
            cypher=executed.cypher,
            result=executed.result,
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
