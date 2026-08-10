"""显式启用时执行真实 Text2Cypher 验收问题。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from text2cypher.application.bootstrap import build_pipeline
from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.application.question_decomposer import LLMQuestionDecomposer
from text2cypher.config import Settings
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import Text2CypherResponse
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
        question="按下游服务统计 ts-admin-basic-info-service 的 REST 调用关系数。",
        key_terms=("ts-admin-basic-info-service",),
        expected_example_id="aggregate-service-target-calls",
    ),
)

ROUTER_CASES = (
    AcceptanceCase(
        question="系统登记了哪些服务？",
        key_terms=(),
        expected_example_id="simple-list-services",
    ),
    AcceptanceCase(
        question="POST 管理路由接口传入和返回什么数据类型？",
        key_terms=(),
        expected_example_id="simple-api-contract",
    ),
    AcceptanceCase(
        question="ts-auth-service 提供了哪些入口接口？",
        key_terms=(),
        expected_example_id="ownership-service-apis",
    ),
    AcceptanceCase(
        question="CancelServiceImpl.cancelOrder 的直接下游方法有哪些？",
        key_terms=(),
        expected_example_id="call-method-downstream-methods",
    ),
    AcceptanceCase(
        question="有哪些服务依赖 ts-route-service 的 REST 接口？",
        key_terms=(),
        expected_example_id="impact-upstream-services",
    ),
    AcceptanceCase(
        question="变更 TravelPlanServiceImpl.getRestTicketNumber 会影响哪些入口？",
        key_terms=(),
        expected_example_id="impact-entry-apis",
    ),
    AcceptanceCase(
        question="两个服务之间发送消息时经过什么 MQ 通道？",
        key_terms=(),
        expected_example_id="mq-between-services",
    ),
    AcceptanceCase(
        question="谁会往 email 队列投递消息？",
        key_terms=(),
        expected_example_id="mq-publishers-for-queue",
    ),
    AcceptanceCase(
        question="分 HTTP 方法汇总某个服务的公开接口数量。",
        key_terms=(),
        expected_example_id="aggregate-service-api-counts",
    ),
    AcceptanceCase(
        question="统计一个服务对各下游的 REST 调用次数。",
        key_terms=(),
        expected_example_id="aggregate-service-target-calls",
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
    """验证真实 Router 为五类改写问题选择相应的原子黄金示例。"""

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

    assert response.decomposed is False
    assert len(response.sub_queries) == 1
    sub_query = response.sub_queries[0]
    assert sub_query.result.rows
    assert sub_query.result.columns
    assert all(term in sub_query.cypher for term in case.key_terms)


def test_compound_method_impact_acceptance(
    pipeline: Text2CypherPipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证复合影响问题拆成两个独立且结果正确的分支。"""

    question = (
        "修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务？"
    )
    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    _skip_after_decomposer_transport_failure(caplog)
    assert response.decomposed is True
    assert len(response.sub_queries) == 2
    assert all(sub_query.result.rows for sub_query in response.sub_queries)
    columns = {
        column
        for sub_query in response.sub_queries
        for column in sub_query.result.columns
    }
    assert "下游服务" in columns
    assert any(column.startswith(("上游", "入口")) for column in columns)
    combined_cypher = "\n".join(
        sub_query.cypher for sub_query in response.sub_queries
    )
    assert "FoodServiceImpl" in combined_cypher
    assert "getAllFood" in combined_cypher
    assert ";" not in combined_cypher
    assert _all_values(response) & {
        "foodsearch.controller.FoodController.getAllFood",
        "/api/v1/foodservice/foods/{date}/{startStation}/{endStation}/{tripId}",
    }
    assert {
        "ts-station-food-service",
        "ts-train-food-service",
        "ts-travel-service",
    } <= _all_values(response)


def test_rest_and_mq_dependencies_are_decomposed_and_executed(
    pipeline: Text2CypherPipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "综合查询 ts-preserve-service 的 REST 和 MQ 下游依赖。"

    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    _skip_after_decomposer_transport_failure(caplog)
    assert response.decomposed is True
    assert len(response.sub_queries) == 2
    assert all(sub_query.result.rows for sub_query in response.sub_queries)
    combined_cypher = "\n".join(
        sub_query.cypher for sub_query in response.sub_queries
    )
    assert "远程调用" in combined_cypher
    assert "服务间消息依赖" in combined_cypher
    assert "ts-notification-service" in _all_values(response)
    assert "ts-assurance-service" in _all_values(response)


def test_three_independent_branches_execute_with_golden_facts(
    pipeline: Text2CypherPipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = (
        "分别查询 ts-preserve-service 的 API 端点、REST 下游依赖和 "
        "MQ 下游依赖。"
    )

    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    _skip_after_decomposer_transport_failure(caplog)
    assert response.decomposed is True
    assert len(response.sub_queries) == 3
    assert all(sub_query.result.rows for sub_query in response.sub_queries)
    combined_cypher = "\n".join(
        sub_query.cypher for sub_query in response.sub_queries
    )
    assert "ts-preserve-service" in combined_cypher
    assert "远程调用" in combined_cypher
    assert "服务间消息依赖" in combined_cypher
    values = _all_values(response)
    assert "/api/v1/preserveservice/preserve" in values
    assert "ts-assurance-service" in values
    assert "ts-notification-service" in values


def test_decomposer_preserves_qualified_names_paths_and_service_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    question = (
        "查询 FoodServiceImpl.getAllFood 的下游服务、"
        "/api/v1/preserveservice/preserve 的所属服务，以及 "
        "ts-preserve-service 的 MQ 下游依赖。"
    )
    try:
        schema = Neo4jSchemaFetcher(
            provider.driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
        ).fetch()
        decomposition = LLMQuestionDecomposer(llm_client).decompose(
            question,
            schema,
        )
        _skip_after_decomposer_transport_failure(caplog)
        combined = "\n".join(decomposition.sub_questions)
        assert decomposition.decomposed is True
        assert len(decomposition.sub_questions) == 3
        assert "FoodServiceImpl.getAllFood" in combined
        assert "/api/v1/preserveservice/preserve" in combined
        assert "ts-preserve-service" in combined
    finally:
        llm_client.close()
        provider.close()


def test_decomposition_can_be_disabled_with_uniform_response() -> None:
    settings = Settings.from_environment().model_copy(
        update={"question_decomposition_enabled": False}
    )
    instance = build_pipeline(settings)
    question = (
        "修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务？"
    )
    try:
        try:
            response = instance.run(question)
        except LLMGenerationError as error:
            pytest.skip(f"外部模型服务暂不可用：{error}")
    finally:
        instance.close()

    assert response.decomposed is False
    assert len(response.sub_queries) == 1
    assert response.sub_queries[0].result.rows
    columns = set(response.sub_queries[0].result.columns)
    assert "下游服务" in columns
    assert any(column.startswith(("上游", "入口")) for column in columns)
    assert _all_values(response) & {
        "foodsearch.controller.FoodController.getAllFood",
        "/api/v1/foodservice/foods/{date}/{startStation}/{endStation}/{tripId}",
    }


def _all_values(response: Text2CypherResponse) -> set[object]:
    values: set[object] = set()
    for sub_query in response.sub_queries:
        for row in sub_query.result.rows:
            for value in row.values():
                if isinstance(value, list):
                    values.update(value)
                else:
                    values.add(value)
    return values


def _skip_after_decomposer_transport_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    if any(
        "QuestionDecomposer 失败" in record.message
        and "LLMGenerationError" in record.message
        for record in caplog.records
    ):
        pytest.skip("外部 Decomposer 模型服务暂不可用")
