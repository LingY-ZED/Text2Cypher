"""生产与评测共用的单轮 Text2Cypher 应用装配。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from text2cypher.application.pipeline import Text2CypherPipeline
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
from text2cypher.runtime.single_round import SingleRoundRuntime
from text2cypher.tools.query_code_graph import QueryCodeGraphTool
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


def build_single_round_runtime(components: PipelineComponents) -> SingleRoundRuntime:
    """从显式端口装配当前兼容模式的 Runtime。"""

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
    return SingleRoundRuntime(
        schema_tool=GetSchemaTool(components.schema_fetcher),
        query_code_graph_tool=QueryCodeGraphTool(query_engine),
        result_summarizer=components.result_summarizer,
        primary_agent=components.primary_agent,
        question_decomposer=components.question_decomposer,
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
