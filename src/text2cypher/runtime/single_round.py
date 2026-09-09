"""保持当前单轮计划语义的 Agent Runtime。"""

from __future__ import annotations

from dataclasses import replace

from text2cypher.components.call_chain_anchor import build_call_chain_plan
from text2cypher.domain.errors import QuestionValidationError
from text2cypher.domain.models import (
    GraphQueryRequest,
    GraphSchema,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryContext,
    QuestionDecomposition,
    SingleRoundRun,
    SubQueryResponse,
)
from text2cypher.domain.ports import (
    CallChainTool,
    CodeGraphQueryTool,
    PrimaryAgent,
    QuestionDecomposer,
    ResultSummarizer,
    SchemaTool,
)
from text2cypher.domain.query_shapes import QueryShape


class SingleRoundRuntime:
    """获取一次 Schema，计划并顺序执行一至三条独立查询。"""

    def __init__(
        self,
        *,
        schema_tool: SchemaTool,
        call_chain_tool: CallChainTool | None = None,
        query_code_graph_tool: CodeGraphQueryTool,
        result_summarizer: ResultSummarizer | None = None,
        primary_agent: PrimaryAgent | None = None,
        question_decomposer: QuestionDecomposer | None = None,
    ) -> None:
        if primary_agent is not None and question_decomposer is not None:
            raise ValueError("primary_agent 与 question_decomposer 不能同时注入")
        self._call_chain_tool = call_chain_tool
        self._schema_tool = schema_tool
        self._query_code_graph_tool = query_code_graph_tool
        self._result_summarizer = result_summarizer
        self._primary_agent = primary_agent
        self._question_decomposer = question_decomposer

    def run(self, question: str) -> SingleRoundRun:
        """运行当前兼容模式，不格式化接口展示文本。"""

        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("问题不能为空")

        schema = self._schema_tool.get_schema()
        plan = self._plan_question(normalized_question, schema)
        sub_queries = tuple(
            self._run_sub_query(schema, query) for query in plan.queries
        )
        summary = (
            self._result_summarizer.summarize(normalized_question, sub_queries)
            if self._result_summarizer is not None
            else None
        )
        return SingleRoundRun(
            question=normalized_question,
            plan=plan,
            sub_queries=sub_queries,
            summary=summary,
        )

    def _plan_question(
        self,
        question: str,
        schema: GraphSchema,
    ) -> PrimaryAgentPlan:
        complete_chain = build_call_chain_plan(question)
        if complete_chain is not None:
            return complete_chain
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
        executed = None
        if query.effective_query_shape is QueryShape.FULL_METHOD_CALL_CHAIN:
            if self._call_chain_tool is not None:
                executed = self._call_chain_tool.query(query)
            if executed is None:
                query = replace(query, query_shape=QueryShape.GENERAL)
        if executed is None:
            executed = self._query_code_graph_tool.query(
                GraphQueryRequest.from_primary_agent_query(query),
                QueryContext(schema),
            )
        return SubQueryResponse(
            question=query.question,
            cypher=executed.cypher,
            result=executed.result,
        )
