"""显式启用时执行真实 Text2Cypher 验收问题。"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
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

    assert response.sub_queries
    assert all(
        sub_query.result is not None and sub_query.result.rows
        for sub_query in response.sub_queries
    )
    combined_cypher = "\n".join(
        sub_query.cypher or "" for sub_query in response.sub_queries
    )
    assert all(term in combined_cypher for term in case.key_terms)


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
    assert 1 <= len(response.sub_queries) <= 3
    assert all(
        sub_query.result is not None and sub_query.result.rows
        for sub_query in response.sub_queries
    )
    combined_cypher = "\n".join(
        sub_query.cypher or "" for sub_query in response.sub_queries
    )
    assert "FoodServiceImpl" in combined_cypher
    assert "getAllFood" in combined_cypher
    assert ";" not in combined_cypher
    assert "foodsearch.controller.FoodController.getAllFood" in _all_values(
        response
    )
    assert {
        "ts-station-food-service",
        "ts-train-food-service",
        "ts-travel-service",
    } <= _all_values(response)


def test_rest_and_mq_dependencies_execute_with_golden_facts(
    pipeline: Text2CypherPipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "综合查询 ts-preserve-service 的 REST 和 MQ 下游依赖。"

    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    _skip_after_decomposer_transport_failure(caplog)
    assert 1 <= len(response.sub_queries) <= 3
    assert all(
        sub_query.result is not None and sub_query.result.rows
        for sub_query in response.sub_queries
    )
    combined_cypher = "\n".join(
        sub_query.cypher or "" for sub_query in response.sub_queries
    )
    assert "远程调用" in combined_cypher
    assert "服务间消息依赖" in combined_cypher
    assert "ts-notification-service" in _all_values(response)
    assert "ts-assurance-service" in _all_values(response)


def test_three_compatible_results_execute_with_golden_facts(
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
    assert 1 <= len(response.sub_queries) <= 3
    assert all(
        sub_query.result is not None and sub_query.result.rows
        for sub_query in response.sub_queries
    )
    combined_cypher = "\n".join(
        sub_query.cypher or "" for sub_query in response.sub_queries
    )
    assert "ts-preserve-service" in combined_cypher
    assert "远程调用" in combined_cypher
    assert "服务间消息依赖" in combined_cypher
    values = _all_values(response)
    assert "/api/v1/preserveservice/preserve" in values
    assert "ts-assurance-service" in values
    assert "ts-notification-service" in values


def test_dependent_upstream_method_chain_executes_with_bound_parameter(
    pipeline: Text2CypherPipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证父方法结果只经参数传给所属服务查询。"""

    question = (
        "先找出调用 FoodServiceImpl.getAllFood 的上游方法并返回方法全限定名，"
        "再查询这些方法所属的微服务。"
    )
    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    _skip_after_decomposer_transport_failure(caplog)
    assert response.decomposed is True
    assert len(response.sub_queries) == 2
    parent, child = response.sub_queries
    assert parent.id == "q1"
    assert child.id == "q2"
    assert child.depends_on == ("q1",)
    assert parent.result is not None
    assert child.result is not None
    assert parent.result.rows
    assert child.result.rows
    assert "FoodServiceImpl" in (parent.cypher or "")
    assert "getAllFood" in (parent.cypher or "")
    assert len(child.parameter_sources) == 1
    parameter_name, parameter = next(iter(child.parameter_sources.items()))
    assert parameter.source_id == "q1"
    assert parameter.columns
    assert f"${parameter_name}" in (child.cypher or "")
    assert "ts-food-service" in _all_values(response)


def test_dependent_service_api_query_uses_bound_parameter(
    pipeline: Text2CypherPipeline,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证消费父结果的服务 API 查询。"""

    question = (
        "分别查询 ts-food-service 自身 API 和它调用的下游服务，"
        "再查询这些下游服务暴露的 API。"
    )
    try:
        response = pipeline.run(question)
    except LLMGenerationError as error:
        pytest.skip(f"外部模型服务暂不可用：{error}")

    _skip_after_decomposer_transport_failure(caplog)
    assert response.decomposed is True
    assert 2 <= len(response.sub_queries) <= 3
    dependent = next(
        sub_query for sub_query in response.sub_queries if sub_query.depends_on
    )
    assert all(sub_query.result is not None for sub_query in response.sub_queries)
    assert all(sub_query.result.rows for sub_query in response.sub_queries)
    parameter_name, parameter = next(
        iter(dependent.parameter_sources.items())
    )
    assert parameter.source_id in dependent.depends_on
    assert parameter.columns
    assert f"${parameter_name}" in (dependent.cypher or "")
    assert "ts-food-service" in "\n".join(
        sub_query.cypher or "" for sub_query in response.sub_queries
    )


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
        combined = "\n".join(
            sub_question.question
            for sub_question in decomposition.sub_questions
        )
        assert decomposition.decomposed is True
        assert 2 <= len(decomposition.sub_questions) <= 3
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
    assert {"上游调用方", "下游服务"} <= set(
        response.sub_queries[0].result.columns
    )


def _all_values(response: Text2CypherResponse) -> set[object]:
    values: set[object] = set()
    for sub_query in response.sub_queries:
        if sub_query.result is None:
            continue
        for row in sub_query.result.rows:
            for value in row.values():
                _add_json_values(values, value)
    return values


def _add_json_values(values: set[object], value: object) -> None:
    if isinstance(value, Mapping):
        for nested_value in value.values():
            _add_json_values(values, nested_value)
        return
    if isinstance(value, (list, tuple, set)):
        for nested_value in value:
            _add_json_values(values, nested_value)
        return
    values.add(value)


def _skip_after_decomposer_transport_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    if any(
        "QuestionDecomposer 失败" in record.message
        and "LLMGenerationError" in record.message
        for record in caplog.records
    ):
        pytest.skip("外部 Decomposer 模型服务暂不可用")
