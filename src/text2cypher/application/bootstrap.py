"""应用层的生产适配器装配入口。"""

from __future__ import annotations

from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.components.few_shot_selector import HybridFewShotSelector
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.components.result_formatter import JsonResultFormatter
from text2cypher.config import Settings
from text2cypher.domain.ports import FewShotRouter
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader
from text2cypher.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator

from .pipeline import Text2CypherPipeline


def build_pipeline(settings: Settings) -> Text2CypherPipeline:
    """在真实适配器完成后构造运行流水线。"""

    driver_provider = Neo4jDriverProvider(settings)
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
        )
        few_shot_router = _build_few_shot_router(settings)
        return Text2CypherPipeline(
            schema_fetcher=Neo4jSchemaFetcher(
                driver,
                settings.neo4j_database,
                settings.schema_timeout_seconds,
            ),
            prompt_builder=DefaultPromptBuilder(),
            llm_client=llm_client,
            cypher_parser=DefaultCypherParser(),
            cypher_validator=Neo4jCypherValidator(
                driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
            ),
            cypher_executor=Neo4jCypherExecutor(
                driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
                settings.max_result_rows,
            ),
            result_formatter=JsonResultFormatter(),
            few_shot_router=few_shot_router,
            close_callback=lambda: _close_resources(llm_client, driver_provider),
        )
    except Exception:
        if llm_client is not None:
            llm_client.close()
        driver_provider.close()
        raise


def _build_few_shot_router(settings: Settings) -> FewShotRouter | None:
    """按配置构造本地过渡 Router；后续由 LLM Router 替换。"""

    if not settings.few_shot_enabled:
        return None

    examples = JsonFewShotExampleLoader(settings.few_shot_library_path).load()
    return HybridFewShotSelector(
        examples,
        top_k=settings.few_shot_top_k,
        min_score=settings.few_shot_min_score,
        max_chars=settings.few_shot_max_chars,
    )


def _close_resources(
    llm_client: OpenAICompatibleLLMClient,
    driver_provider: Neo4jDriverProvider,
) -> None:
    """按创建顺序的反向关闭外部资源。"""

    llm_client.close()
    driver_provider.close()
