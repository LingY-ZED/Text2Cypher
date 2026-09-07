"""Frozen Neo4j checks for deterministic legacy method-query shapes."""

from __future__ import annotations

import os

import pytest

from evaluation.comparator import compare_case
from evaluation.dataset import load_cases
from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.components.retry import RetryPolicy
from text2cypher.config import Settings
from text2cypher.domain.models import (
    ChatPrompt,
    GraphQueryRequest,
    GraphSchema,
    LLMResponse,
    PrimaryAgentQuery,
    QueryContext,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.graph_core.readonly_cypher_gateway import DefaultReadOnlyCypherGateway
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator
from text2cypher.query_engine.engine import DefaultGraphQueryEngine

pytestmark = pytest.mark.skipif(
    os.getenv("TEXT2CYPHER_RUN_INTEGRATION") != "1",
    reason="仅在显式设置 TEXT2CYPHER_RUN_INTEGRATION=1 时访问真实数据库",
)


@pytest.mark.parametrize(
    ("case_id", "query_shape", "anchor"),
    (
        (
            "food-service-get-all-food-direct-callers",
            QueryShape.DIRECT_UPSTREAM,
            "FoodServiceImpl.getAllFood",
        ),
        (
            "food-service-get-all-food-entry-apis",
            QueryShape.REACHABLE_ENTRY_API,
            "FoodServiceImpl.getAllFood",
        ),
        (
            "inside-payment-pay-direct-rest-apis",
            QueryShape.DIRECT_REST_EGRESS,
            "InsidePaymentServiceImpl.pay",
        ),
        (
            "consign-insert-full-upstream-chain",
            QueryShape.FULL_ENTRY_CHAIN,
            "ConsignServiceImpl.insertConsignRecord",
        ),
    ),
)
def test_engine_matches_frozen_legacy_method_oracles_without_llm(
    case_id: str,
    query_shape: QueryShape,
    anchor: str,
) -> None:
    settings = Settings.from_environment()
    provider = Neo4jDriverProvider(settings, retry_policy=RetryPolicy())
    try:
        gateway = DefaultReadOnlyCypherGateway(
            DefaultCypherParser(),
            Neo4jCypherValidator(
                provider.driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
            ),
            Neo4jCypherExecutor(
                provider.driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
                settings.max_result_rows,
            ),
        )
        query = PrimaryAgentQuery(
            query_id="q1",
            question=f"查询 {anchor}",
            intent="查询方法图谱事实",
            required_information=("冻结 Oracle",),
            anchor=anchor,
            query_shape=query_shape,
        )
        result = DefaultGraphQueryEngine(
            prompt_builder=_UnusedPromptBuilder(),
            llm_client=_UnusedLLM(),
            read_only_cypher_gateway=gateway,
        ).query(
            GraphQueryRequest.from_primary_agent_query(query),
            QueryContext(GraphSchema()),
        )
    finally:
        provider.close()

    case = next(
        item
        for item in load_cases()
        if item.id == case_id
    )
    assert "$anchorQualifiedName" in result.cypher
    assert anchor not in result.cypher
    assert compare_case(case, (result.result.rows,)).matched


def test_engine_matches_the_frozen_service_dependency_oracle_without_llm() -> None:
    settings = Settings.from_environment()
    provider = Neo4jDriverProvider(settings, retry_policy=RetryPolicy())
    try:
        gateway = DefaultReadOnlyCypherGateway(
            DefaultCypherParser(),
            Neo4jCypherValidator(
                provider.driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
            ),
            Neo4jCypherExecutor(
                provider.driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
                settings.max_result_rows,
            ),
        )
        case = next(
            item
            for item in load_cases()
            if item.id == "notification-senders-rest-matrix"
        )
        query = PrimaryAgentQuery(
            query_id="q1",
            question=case.question,
            intent="查询发送服务及其 REST 下游服务",
            required_information=("发送服务", "下游服务"),
            anchor="ts-notification-service",
            query_shape=QueryShape.GENERAL,
        )
        result = DefaultGraphQueryEngine(
            prompt_builder=_UnusedPromptBuilder(),
            llm_client=_UnusedLLM(),
            read_only_cypher_gateway=gateway,
        ).query(
            GraphQueryRequest.from_primary_agent_query(query),
            QueryContext(GraphSchema()),
        )
    finally:
        provider.close()

    assert "$receiverServiceName" in result.cypher
    assert "ts-notification-service" not in result.cypher
    assert compare_case(case, (result.result.rows,)).matched


class _UnusedPromptBuilder:
    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[object, ...] = (),
    ) -> ChatPrompt:
        del schema, question, examples
        raise AssertionError("确定性方法查询不应构造 LLM Prompt")


class _UnusedLLM:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        raise AssertionError("确定性方法查询不应调用 Cypher 生成模型")
