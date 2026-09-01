from __future__ import annotations

import json
import logging

import pytest

from text2cypher.components.recovery_logging import log_retry_event
from text2cypher.components.retry import RetryEvent
from text2cypher.interfaces.logging import JsonLogFormatter


def test_json_log_formatter_keeps_recovery_fields_structured() -> None:
    logger = logging.getLogger("text2cypher.test")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        "test.py",
        1,
        "recovery_event",
        (),
        None,
        extra={
            "recovery_event": {
                "event": "retry_scheduled",
                "component": "llm",
                "stage": "chat_completion",
                "attempt": 1,
                "max_attempts": 3,
                "reason": "http_503",
                "delay_ms": 500,
                "outcome": "retrying",
            }
        },
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["event"] == "retry_scheduled"
    assert payload["component"] == "llm"
    assert payload["delay_ms"] == 500
    assert payload["message"] == "recovery_event"


def test_retry_event_has_only_safe_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("text2cypher.test.recovery")
    caplog.set_level(logging.WARNING)

    log_retry_event(
        logger,
        component="neo4j",
        stage="execute",
        retry_event=RetryEvent(
            event="retry_scheduled",
            attempt=1,
            max_attempts=3,
            reason="neo4j_transient",
            delay_seconds=0.5,
        ),
    )

    event = caplog.records[-1].recovery_event
    assert event == {
        "event": "retry_scheduled",
        "component": "neo4j",
        "stage": "execute",
        "attempt": 1,
        "max_attempts": 3,
        "reason": "neo4j_transient",
        "delay_ms": 500,
        "outcome": "retrying",
    }


def test_json_log_formatter_keeps_decomposition_review_fields_structured() -> None:
    logger = logging.getLogger("text2cypher.test.decomposition")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        "test.py",
        1,
        "question_decomposition_review",
        (),
        None,
        extra={
            "decomposition_review_event": {
                "component": "decomposer",
                "stage": "review",
                "outcome": "rejected",
                "reason": "RESULT_DEPENDENCY",
            }
        },
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["component"] == "decomposer"
    assert payload["stage"] == "review"
    assert payload["outcome"] == "rejected"
    assert payload["reason"] == "RESULT_DEPENDENCY"


def test_json_log_formatter_keeps_primary_agent_fields_structured() -> None:
    logger = logging.getLogger("text2cypher.test.primary_agent")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        "test.py",
        1,
        "primary_agent_planning",
        (),
        None,
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

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["component"] == "primary_agent"
    assert payload["stage"] == "planning"
    assert payload["query_count"] == 1
    assert payload["reason"] == "invalid_response"


def test_json_log_formatter_keeps_result_summary_fields_structured() -> None:
    logger = logging.getLogger("text2cypher.test.summary")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        "test.py",
        1,
        "result_summary",
        (),
        None,
        extra={
            "result_summary_event": {
                "component": "result_summarizer",
                "stage": "summary",
                "outcome": "fallback",
                "mode": "template",
                "reason": "llm_failure",
            }
        },
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["component"] == "result_summarizer"
    assert payload["stage"] == "summary"
    assert payload["mode"] == "template"
    assert payload["reason"] == "llm_failure"
