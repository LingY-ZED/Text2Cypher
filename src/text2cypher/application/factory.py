"""生产与评测共用的 Runtime 应用装配。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from text2cypher.application.iterative_answerer import LLMIterativeAnswerer
from text2cypher.application.iterative_planner import LLMIterativePlanner
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.domain.ports import (
    CypherCorrector,
    CypherExecutor,
    CypherParser,
    CypherValidator,
    FewShotRouter,
    GraphQueryEngine,
    IterativeAnswerer,
    IterativePlanner,
    LLMClient,
    ParameterizedReadOnlyCypherGateway,
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
from text2cypher.runtime.iterative import IterativeRuntime
from text2cypher.runtime.single_round import SingleRoundRuntime
from text2cypher.tools.find_call_chain import FindCallChainTool
from text2cypher.tools.query_code_graph import QueryCodeGraphTool
from text2cypher.tools.resolve_symbol import ResolveSymbolTool
from text2cypher.tools.schema import GetSchemaTool


@dataclass(frozen=True, slots=True)
class PipelineComponents:
    """装配单轮查询服务所需的端口与兼容行为开关。"""

    schema_fetcher: SchemaFetcher
    prompt_builder: PromptBuilder
    llm_client: LLMClient
    cypher_parser: CypherParser
    cypher_validator: CypherValidator
    cypher_executor: CypherExecutor
    result_formatter: ResultFormatter | None = None
    read_only_cypher_gateway: ReadOnlyCypherGateway | None = None
    graph_query_engine: GraphQueryEngine | None = None
    result_summarizer: ResultSummarizer | None = None
    primary_agent: PrimaryAgent | None = None
    question_decomposer: QuestionDecomposer | None = None
    few_shot_router: FewShotRouter | None = None
    cypher_corrector: CypherCorrector | None = None
    recover_empty_results: bool = False
    close_callback: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class _RuntimeToolStack:
    """SingleRound 与 IterativeRuntime 共用的受限 Tool 装配结果。"""

    schema_tool: GetSchemaTool
    query_code_graph_tool: QueryCodeGraphTool
    resolve_symbol_tool: ResolveSymbolTool | None
    call_chain_tool: FindCallChainTool | None


def build_single_round_runtime(components: PipelineComponents) -> SingleRoundRuntime:
    """从显式端口装配当前兼容模式的 Runtime。"""

    tools = _build_runtime_tool_stack(components)
    return SingleRoundRuntime(
        schema_tool=tools.schema_tool,
        call_chain_tool=tools.call_chain_tool,
        query_code_graph_tool=tools.query_code_graph_tool,
        result_summarizer=components.result_summarizer,
        primary_agent=components.primary_agent,
        question_decomposer=components.question_decomposer,
    )


def build_iterative_runtime(
    components: PipelineComponents,
    *,
    planner: IterativePlanner | None = None,
    answerer: IterativeAnswerer | None = None,
    max_rounds: int = 3,
    max_actions: int = 9,
    max_consecutive_no_evidence_rounds: int = 2,
) -> IterativeRuntime:
    """显式构造 CODEXGRAPH 风格 Runtime，不改变默认单轮 Pipeline。"""

    tools = _build_runtime_tool_stack(components)
    return IterativeRuntime(
        schema_tool=tools.schema_tool,
        resolve_symbol_tool=tools.resolve_symbol_tool,
        call_chain_tool=tools.call_chain_tool,
        query_code_graph_tool=tools.query_code_graph_tool,
        planner=planner or LLMIterativePlanner(components.llm_client),
        answerer=answerer or LLMIterativeAnswerer(components.llm_client),
        max_rounds=max_rounds,
        max_actions=max_actions,
        max_consecutive_no_evidence_rounds=(
            max_consecutive_no_evidence_rounds
        ),
    )


def _build_runtime_tool_stack(components: PipelineComponents) -> _RuntimeToolStack:
    """装配两种 Runtime 都必须复用的 Gateway -> Engine -> Tool 链路。"""

    read_only_gateway = (
        components.read_only_cypher_gateway
        or DefaultReadOnlyCypherGateway(
            components.cypher_parser,
            components.cypher_validator,
            components.cypher_executor,
        )
    )
    query_engine = components.graph_query_engine or DefaultGraphQueryEngine(
        prompt_builder=components.prompt_builder,
        llm_client=components.llm_client,
        read_only_cypher_gateway=read_only_gateway,
        few_shot_router=components.few_shot_router,
        cypher_corrector=components.cypher_corrector,
        recover_empty_results=components.recover_empty_results,
    )
    resolver = None
    call_chain = None
    if isinstance(read_only_gateway, ParameterizedReadOnlyCypherGateway):
        resolver = ResolveSymbolTool(read_only_gateway)
        call_chain = FindCallChainTool(resolver, read_only_gateway)
    return _RuntimeToolStack(
        schema_tool=GetSchemaTool(components.schema_fetcher),
        query_code_graph_tool=QueryCodeGraphTool(query_engine),
        resolve_symbol_tool=resolver,
        call_chain_tool=call_chain,
    )


def build_pipeline_from_components(
    components: PipelineComponents,
) -> Text2CypherPipeline:
    """从生产或评测注入的端口构造同一条公开 Pipeline。"""

    if components.result_formatter is None:
        raise ValueError("构造 Pipeline 必须注入 ResultFormatter")
    return Text2CypherPipeline(
        result_formatter=components.result_formatter,
        single_round_runtime=build_single_round_runtime(components),
        close_callback=components.close_callback,
    )
