"""应用层的生产适配器装配入口。"""

from __future__ import annotations

from text2cypher.application.cypher_corrector import LLMCypherCorrector
from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.application.primary_agent import LLMPrimaryAgent
from text2cypher.application.question_decomposer import LLMQuestionDecomposer
from text2cypher.application.result_summarizer import LLMResultSummarizer
from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.components.result_formatter import JsonResultFormatter
from text2cypher.components.retry import RetryPolicy
from text2cypher.config import Settings
from text2cypher.domain.ports import (
    CypherCorrector,
    FewShotRouter,
    LLMClient,
    PrimaryAgent,
    QuestionDecomposer,
)
from text2cypher.graph_core.readonly_cypher_gateway import (
    DefaultReadOnlyCypherGateway,
)
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader
from text2cypher.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator
from text2cypher.query_engine.engine import DefaultGraphQueryEngine
from text2cypher.runtime.single_round import SingleRoundRuntime
from text2cypher.tools.query_code_graph import QueryCodeGraphTool
from text2cypher.tools.schema import GetSchemaTool

from .pipeline import Text2CypherPipeline


def build_pipeline(settings: Settings) -> Text2CypherPipeline:
    """在真实适配器完成后构造运行流水线。"""

    retry_policy = _retry_policy(settings)
    driver_provider = Neo4jDriverProvider(settings, retry_policy=retry_policy)
    llm_client: OpenAICompatibleLLMClient | None = None
    try:
        driver = driver_provider.driver
        llm_client = OpenAICompatibleLLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
            disable_thinking=settings.llm_disable_thinking,
            retry_policy=retry_policy,
        )
        primary_agent = _build_primary_agent(settings, llm_client)
        few_shot_router = _build_few_shot_router(settings, llm_client)
        cypher_corrector = _build_cypher_corrector(settings, llm_client)
        cypher_parser = DefaultCypherParser()
        cypher_validator = Neo4jCypherValidator(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            retry_policy=retry_policy,
        )
        cypher_executor = Neo4jCypherExecutor(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            settings.max_result_rows,
            retry_policy=retry_policy,
        )
        prompt_builder = DefaultPromptBuilder()
        read_only_cypher_gateway = DefaultReadOnlyCypherGateway(
            cypher_parser,
            cypher_validator,
            cypher_executor,
        )
        schema_fetcher = Neo4jSchemaFetcher(
            driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
            retry_policy=retry_policy,
        )
        graph_query_engine = DefaultGraphQueryEngine(
            prompt_builder=prompt_builder,
            llm_client=llm_client,
            read_only_cypher_gateway=read_only_cypher_gateway,
            few_shot_router=few_shot_router,
            cypher_corrector=cypher_corrector,
            recover_empty_results=settings.empty_result_correction_enabled,
        )
        runtime = SingleRoundRuntime(
            schema_tool=GetSchemaTool(schema_fetcher),
            query_code_graph_tool=QueryCodeGraphTool(graph_query_engine),
            result_summarizer=_build_result_summarizer(settings, llm_client),
            primary_agent=primary_agent,
        )
        return Text2CypherPipeline(
            result_formatter=JsonResultFormatter(),
            single_round_runtime=runtime,
            close_callback=lambda: _close_resources(llm_client, driver_provider),
        )
    except Exception:
        if llm_client is not None:
            llm_client.close()
        driver_provider.close()
        raise


def _build_primary_agent(
    settings: Settings,
    llm_client: LLMClient,
) -> PrimaryAgent | None:
    """按配置构造复用主模型客户端的 Primary Agent。"""

    if not settings.primary_agent_enabled:
        return None
    return LLMPrimaryAgent(
        llm_client,
        max_queries=settings.primary_agent_max_queries,
    )


def _build_question_decomposer(
    settings: Settings,
    llm_client: LLMClient,
) -> QuestionDecomposer | None:
    """为旧调用方保留的 Decomposer 构造兼容函数。"""

    if not settings.primary_agent_enabled:
        return None
    return LLMQuestionDecomposer(
        llm_client,
        max_subquestions=settings.primary_agent_max_queries,
    )


def _retry_policy(settings: Settings) -> RetryPolicy:
    """从统一配置构造所有外部适配器复用的重试策略。"""

    return RetryPolicy(
        enabled=settings.retry_enabled,
        max_attempts=settings.retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
    )


def _build_few_shot_router(
    settings: Settings,
    llm_client: LLMClient,
) -> FewShotRouter | None:
    """按配置构造复用主模型客户端的 LLM Few-shot Router。"""

    if not settings.few_shot_enabled:
        return None

    examples = JsonFewShotExampleLoader(settings.few_shot_library_path).load()
    return LLMFewShotRouter(
        examples,
        llm_client,
        top_k=settings.few_shot_top_k,
        max_chars=settings.few_shot_max_chars,
    )


def _build_cypher_corrector(
    settings: Settings,
    llm_client: LLMClient,
) -> CypherCorrector | None:
    """按配置构造复用主模型客户端的一次性 Cypher Corrector。"""

    if not settings.cypher_correction_enabled:
        return None
    return LLMCypherCorrector(llm_client)


def _build_result_summarizer(
    settings: Settings,
    llm_client: LLMClient,
) -> LLMResultSummarizer:
    """构造复用主模型客户端的结果总结器。"""

    return LLMResultSummarizer(
        llm_client,
        enabled=settings.natural_language_summary_enabled,
        max_input_chars=settings.natural_language_summary_max_input_chars,
    )


def _close_resources(
    llm_client: OpenAICompatibleLLMClient,
    driver_provider: Neo4jDriverProvider,
) -> None:
    """按创建顺序的反向关闭外部资源。"""

    llm_client.close()
    driver_provider.close()
