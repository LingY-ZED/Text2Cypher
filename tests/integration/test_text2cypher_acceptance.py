"""显式启用时执行真实 Text2Cypher 验收问题。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from text2cypher.application.bootstrap import build_pipeline
from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.config import Settings
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader
from text2cypher.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher

pytestmark = pytest.mark.skipif(
    os.getenv("TEXT2CYPHER_RUN_ACCEPTANCE") != "1",
    reason="仅在显式设置 TEXT2CYPHER_RUN_ACCEPTANCE=1 时调用真实模型和数据库",
)


@dataclass(frozen=True)
class AcceptanceCase:
    """一条按查询意图而非参考 Cypher 文本断言的验收用例。"""

    question: str
    key_terms: tuple[str, ...]
    expected_example_id: str


CASES = (
    AcceptanceCase(
        question="ts-food-service 有哪些 API 端点？",
        key_terms=("ts-food-service",),
        expected_example_id="ownership-service-apis",
    ),
    AcceptanceCase(
        question="getAllFood 方法调用了哪些下游服务？",
        key_terms=("getAllFood",),
        expected_example_id="call-method-downstream-services",
    ),
    AcceptanceCase(
        question="getAllFood 方法调用了哪些方法？",
        key_terms=("getAllFood",),
        expected_example_id="call-method-downstream-methods",
    ),
    AcceptanceCase(
        question="哪些服务调用了 ts-train-food-service？",
        key_terms=("ts-train-food-service",),
        expected_example_id="impact-upstream-services",
    ),
    AcceptanceCase(
        question="ts-food-service 和 ts-delivery-service 之间有哪些 MQ 通信？",
        key_terms=("ts-food-service", "ts-delivery-service"),
        expected_example_id="mq-between-services",
    ),
    AcceptanceCase(
        question="列出所有微服务之间的 REST 调用关系统计",
        key_terms=("跨服务调用",),
        expected_example_id="aggregate-rest-service-pairs",
    ),
)

ROUTER_CASES = (
    *CASES,
    AcceptanceCase(
        question="修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务？",
        key_terms=("FoodServiceImpl", "getAllFood"),
        expected_example_id="compound-method-impact",
    ),
)


@pytest.fixture
def pipeline() -> Iterator[Text2CypherPipeline]:
    """为每条真实验收用例创建并关闭完整生产流水线。"""

    instance = build_pipeline(Settings.from_environment())
    try:
        yield instance
    finally:
        instance.close()


def test_llm_router_selects_expected_golden_examples(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证真实 Router 在七类问题中选择相应的兼容黄金示例。"""

    settings = Settings.from_environment()
    provider = Neo4jDriverProvider(settings)
    llm_client = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        disable_thinking=settings.llm_disable_thinking,
    )
    try:
        schema = Neo4jSchemaFetcher(
            provider.driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
        ).fetch()
        router = LLMFewShotRouter(
            JsonFewShotExampleLoader(settings.few_shot_library_path).load(),
            llm_client,
            top_k=settings.few_shot_top_k,
            max_chars=settings.few_shot_max_chars,
        )
        for case in ROUTER_CASES:
            selected = router.route(case.question, schema)
            if any(
                "LLMGenerationError" in record.message
                for record in caplog.records
            ):
                pytest.skip("外部 Router 模型服务暂不可用")
            assert case.expected_example_id in {
                example.id for example in selected
            }
    finally:
        llm_client.close()
        provider.close()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.question)
def test_text2cypher_acceptance(
    pipeline: Text2CypherPipeline,
    case: AcceptanceCase,
) -> None:
    """验证完整链路生成语义字段齐全且有实际结果的只读查询。"""

    try:
        response = pipeline.run(case.question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    assert response.result.rows
    assert response.result.columns
    assert all(term in response.cypher for term in case.key_terms)


def test_compound_method_impact_acceptance(
    pipeline: Text2CypherPipeline,
) -> None:
    """验证复合影响问题由一条查询同时表达上游和下游分支。"""

    question = (
        "修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务？"
    )
    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    assert response.result.rows
    assert {"上游调用方", "下游服务"} <= set(response.result.columns)
    assert "FoodServiceImpl" in response.cypher
    assert "getAllFood" in response.cypher
    assert response.cypher.upper().count("OPTIONAL MATCH") >= 2
    assert ";" not in response.cypher
