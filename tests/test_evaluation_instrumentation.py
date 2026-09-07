"""Evaluation telemetry must not retain prompts or model response bodies."""

from __future__ import annotations

import json

import pytest

from evaluation.instrumentation import (
    EvaluationLogObserver,
    EvaluationRecorder,
    RecordingFewShotRouter,
    RecordingGraphQueryEngine,
    RecordingPrimaryAgent,
    RecoveryEventHandler,
    StageLLMClient,
)
from text2cypher.domain.models import (
    ChatPrompt,
    ExecutedCypher,
    GraphQueryRequest,
    GraphSchema,
    LLMResponse,
    PrimaryAgentPlan,
    QueryContext,
    QueryResult,
)


class _SuccessfulClient:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        return LLMResponse(
            content="sensitive-model-response",
            model="model",
            finish_reason="stop",
        )

    def close(self) -> None:
        return None


class _FailingClient:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        raise RuntimeError("sensitive-response-body")

    def close(self) -> None:
        return None


class _PrimaryAgent:
    def plan(self, question: str) -> PrimaryAgentPlan:
        assert question == "查询服务"
        return PrimaryAgentPlan.fallback(question)


def test_llm_events_store_metadata_without_prompt_or_response_content() -> None:
    recorder = EvaluationRecorder()
    client = StageLLMClient(_SuccessfulClient(), recorder, "generation")

    client.generate(ChatPrompt(system="sensitive-system", user="sensitive-user"))
    serialized = json.dumps(recorder.events)

    assert "sensitive-system" not in serialized
    assert "sensitive-user" not in serialized
    assert "sensitive-model-response" not in serialized
    assert recorder.events[0]["content_length"] == len("sensitive-model-response")


def test_llm_failure_event_does_not_store_exception_response_body() -> None:
    recorder = EvaluationRecorder()
    client = StageLLMClient(_FailingClient(), recorder, "router")

    with pytest.raises(RuntimeError):
        client.generate(ChatPrompt(system="system", user="user"))

    serialized = json.dumps(recorder.events)
    assert "sensitive-response-body" not in serialized
    assert recorder.events == [
        {
            "component": "llm",
            "stage": "router",
            "outcome": "failed",
            "duration_seconds": recorder.events[0]["duration_seconds"],
            "error_type": "RuntimeError",
            "query_index": 0,
        }
    ]


def test_recording_primary_agent_keeps_plan_for_evaluation_only() -> None:
    recorder = EvaluationRecorder()

    plan = RecordingPrimaryAgent(_PrimaryAgent(), recorder).plan("查询服务")

    assert plan.sub_questions == ("查询服务",)
    assert recorder.events == [
        {
            "component": "primary_agent",
            "stage": "plan_result",
            "outcome": "succeeded",
            "decomposed": False,
            "query_count": 1,
            "analysis_summary": "使用原始问题执行单次检索",
            "queries": [
                {
                    "query_id": "q1",
                    "question": "查询服务",
                    "intent": "回答原始问题",
                    "required_information": ["回答原始问题所需的图数据"],
                    "anchor": None,
                    "query_shape": None,
                }
            ],
        }
    ]


class _Router:
    def route(self, question: str, schema: GraphSchema) -> tuple[object, ...]:
        del question, schema
        return ()


class _GraphQueryEngine:
    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher:
        del request, context
        return ExecutedCypher(
            cypher="RETURN 1",
            result=QueryResult(columns=("value",), rows=({"value": 1},)),
        )


def test_recording_graph_query_engine_assigns_an_index_to_deterministic_work() -> None:
    recorder = EvaluationRecorder()
    engine = RecordingGraphQueryEngine(_GraphQueryEngine(), recorder)

    result = engine.query(
        GraphQueryRequest(
            query_id="q1",
            question="查询服务",
            intent="查询",
            required_information=("服务",),
        ),
        QueryContext(GraphSchema()),
    )
    StageLLMClient(_SuccessfulClient(), recorder, "generation").generate(
        ChatPrompt(system="system", user="user")
    )

    assert result.result.rows == ({"value": 1},)
    assert recorder.current_query_index == 1
    assert recorder.last_candidate_stage == "generation"
    assert recorder.events[-1]["query_index"] == 1


def test_recording_router_records_only_deterministic_effective_shape() -> None:
    recorder = EvaluationRecorder()

    selected = RecordingFewShotRouter(_Router(), recorder).route(
        "查询getTickets的上游调用链",
        GraphSchema(),
    )

    assert selected == ()
    assert recorder.events == [
        {
            "component": "router",
            "stage": "selection",
            "outcome": "succeeded",
            "question": "查询getTickets的上游调用链",
            "effective_query_shape": "full_entry_chain",
            "selected_ids": [],
            "query_index": 1,
        }
    ]


def test_primary_agent_log_event_is_recorded_without_sensitive_content() -> None:
    import logging

    recorder = EvaluationRecorder()
    handler = RecoveryEventHandler(recorder)
    logger = logging.getLogger("text2cypher.application.primary_agent")
    logger.addHandler(handler)
    try:
        logger.warning(
            "primary_agent_planning",
            extra={
                "primary_agent_event": {
                    "component": "primary_agent",
                    "stage": "planning",
                    "outcome": "fallback",
                    "query_count": 1,
                    "decomposed": False,
                    "reason": "invalid_response",
                }
            },
        )
    finally:
        logger.removeHandler(handler)

    assert recorder.events == [
        {
            "component": "primary_agent",
            "stage": "planning",
            "outcome": "fallback",
            "query_count": 1,
            "decomposed": False,
            "reason": "invalid_response",
        }
    ]


def test_legacy_reviewer_log_event_is_still_readable() -> None:
    import logging

    recorder = EvaluationRecorder()
    handler = RecoveryEventHandler(recorder)
    logger = logging.getLogger("text2cypher.application.question_decomposer")
    logger.addHandler(handler)
    try:
        logger.warning(
            "question_decomposition_review",
            extra={
                "decomposition_review_event": {
                    "component": "decomposer",
                    "stage": "review",
                    "outcome": "rejected",
                    "reason": "CORRELATION_LOSS",
                }
            },
        )
    finally:
        logger.removeHandler(handler)

    assert recorder.events == [
        {
            "component": "decomposer",
            "stage": "review",
            "outcome": "rejected",
            "reason": "CORRELATION_LOSS",
        }
    ]


def test_reviewer_info_event_is_visible_when_evaluation_enables_logger() -> None:
    import logging

    recorder = EvaluationRecorder()
    handler = RecoveryEventHandler(recorder)
    logger = logging.getLogger("text2cypher.application.question_decomposer")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info(
            "question_decomposition_review",
            extra={
                "decomposition_review_event": {
                    "component": "decomposer",
                    "stage": "review",
                    "outcome": "accepted",
                    "reason": "VALID",
                }
            },
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert recorder.events == [
        {
            "component": "decomposer",
            "stage": "review",
            "outcome": "accepted",
            "reason": "VALID",
        }
    ]


def test_summary_log_event_is_recorded_without_answer_content() -> None:
    import logging

    recorder = EvaluationRecorder()
    handler = RecoveryEventHandler(recorder)
    logger = logging.getLogger("text2cypher.application.result_summarizer")
    logger.addHandler(handler)
    try:
        logger.warning(
            "result_summary",
            extra={
                "result_summary_event": {
                    "component": "result_summarizer",
                    "stage": "summary",
                    "outcome": "fallback",
                    "mode": "template",
                    "reason": "invalid_response",
                }
            },
        )
    finally:
        logger.removeHandler(handler)

    serialized = json.dumps(recorder.events)
    assert "answer" not in serialized
    assert recorder.events == [
        {
            "component": "result_summarizer",
            "stage": "summary",
            "outcome": "fallback",
            "mode": "template",
            "reason": "invalid_response",
        }
    ]


def test_summary_info_event_is_visible_when_evaluation_enables_logger() -> None:
    import logging

    recorder = EvaluationRecorder()
    handler = RecoveryEventHandler(recorder)
    logger = logging.getLogger("text2cypher.application.result_summarizer")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info(
            "result_summary",
            extra={
                "result_summary_event": {
                    "component": "result_summarizer",
                    "stage": "summary",
                    "outcome": "generated",
                    "mode": "llm",
                    "reason": None,
                }
            },
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert recorder.events == [
        {
            "component": "result_summarizer",
            "stage": "summary",
            "outcome": "generated",
            "mode": "llm",
            "reason": None,
        }
    ]


def test_log_observer_scopes_event_capture_and_restores_logger_level() -> None:
    import logging

    recorder = EvaluationRecorder()
    logger = logging.getLogger("text2cypher.query_engine.engine")
    previous_level = logger.level

    with EvaluationLogObserver(recorder):
        logger.info(
            "cypher_correction_succeeded",
            extra={
                "recovery_event": {
                    "component": "pipeline",
                    "stage": "cypher_correction",
                    "event": "cypher_correction_succeeded",
                    "reason": "validation",
                    "outcome": "corrected",
                }
            },
        )

    assert logger.level == previous_level
    assert recorder.recovery_events == [
        {
            "component": "pipeline",
            "stage": "cypher_correction",
            "event": "cypher_correction_succeeded",
            "reason": "validation",
            "outcome": "corrected",
        }
    ]
