from __future__ import annotations

import json
import logging

import pytest

from text2cypher.components.recovery_logging import (
    log_retry_event,
    log_subquery_event,
)
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


def test_subquery_event_is_structured_and_omits_sensitive_query_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("text2cypher.test.subquery")
    caplog.set_level(logging.INFO, logger=logger.name)

    log_subquery_event(
        logger,
        event="subquery_succeeded",
        subquery_id="q2",
        dependency_count=1,
        outcome="success",
        duration_ms=17,
    )

    event = caplog.records[-1].subquery_event
    assert event == {
        "event": "subquery_succeeded",
        "component": "pipeline",
        "stage": "subquery_scheduler",
        "subquery_id": "q2",
        "dependency_count": 1,
        "duration_ms": 17,
        "outcome": "success",
    }
    payload = json.loads(JsonLogFormatter().format(caplog.records[-1]))
    assert payload["subquery_id"] == "q2"
    assert "Cypher" not in payload
