"""真实 Neo4j 上参数化完整调用链 Tool 的冻结快照验收。"""

from __future__ import annotations

import json
import os

import pytest

from evaluation.dataset import load_cases
from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.components.retry import RetryPolicy
from text2cypher.config import Settings
from text2cypher.domain.models import (
    CALL_CHAIN_RESULT_COLUMNS,
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
from text2cypher.tools.find_call_chain import FindCallChainTool
from text2cypher.tools.resolve_symbol import ResolveSymbolTool

pytestmark = pytest.mark.skipif(
    os.getenv("TEXT2CYPHER_RUN_INTEGRATION") != "1",
    reason="仅在显式设置 TEXT2CYPHER_RUN_INTEGRATION=1 时访问真实数据库",
)


@pytest.mark.parametrize(
    ("anchor", "case_id"),
    (
        (
            "inside_payment.service.InsidePaymentServiceImpl.pay",
            "inside-payment-pay-complete-method-call-chain",
        ),
        (
            "preserve.mq.RabbitSend.send",
            "preserve-rabbit-send-complete-method-call-chain",
        ),
        (
            "waitorder.utils.PollThread.doPreserve",
            "poll-thread-complete-method-call-chain-missing-rest-mapping",
        ),
    ),
)
def test_parameterized_find_call_chain_matches_the_frozen_snapshot(
    anchor: str,
    case_id: str,
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
        segments = FindCallChainTool(
            ResolveSymbolTool(gateway),
            gateway,
        ).find_call_chain(anchor)
    finally:
        provider.close()

    expected = next(
        intent.expected_snapshot
        for case in load_cases()
        if case.id == case_id
        for intent in case.intents
    )
    actual = tuple(segment.to_row() for segment in segments)

    assert actual
    assert all(tuple(row) == CALL_CHAIN_RESULT_COLUMNS for row in actual)
    assert _fingerprint(actual) == _fingerprint(expected)


class _UnusedPromptBuilder:
    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[object, ...] = (),
    ) -> ChatPrompt:
        del schema, question, examples
        raise AssertionError("完整调用链不应构造 LLM Prompt")


class _UnusedLLM:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        raise AssertionError("完整调用链不应调用 Cypher 生成模型")


def test_engine_compiles_entry_api_call_chain_without_llm() -> None:
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
            question=(
                "POST /api/v1/travelservice/trips/left "
                "在 ts-travel-service 中的完整调用链是什么？"
            ),
            intent="查询完整调用链",
            required_information=("完整调用链表格",),
            anchor="travel entry api",
            query_shape=QueryShape.FULL_METHOD_CALL_CHAIN,
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

    expected = next(
        intent.expected_snapshot
        for case in load_cases()
        if case.id == "travel-left-api-complete-method-call-chain"
        for intent in case.intents
    )
    assert "$anchorQualifiedName" in result.cypher
    assert "/api/v1/travelservice/trips/left" not in result.cypher
    assert _fingerprint(result.result.rows) == _fingerprint(expected)


def _fingerprint(rows: object) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
            for row in rows  # type: ignore[union-attr]
        )
    )
