"""恢复路径使用的脱敏结构化日志事件。"""

from __future__ import annotations

import logging

from text2cypher.components.retry import RetryEvent


def log_retry_event(
    logger: logging.Logger,
    *,
    component: str,
    stage: str,
    retry_event: RetryEvent,
) -> None:
    """记录不含请求内容的统一重试生命周期事件。"""

    outcomes = {
        "retry_scheduled": "retrying",
        "retry_succeeded": "recovered",
        "retry_exhausted": "failed",
    }
    _emit(
        logger,
        event=retry_event.event,
        component=component,
        stage=stage,
        attempt=retry_event.attempt,
        max_attempts=retry_event.max_attempts,
        reason=retry_event.reason,
        delay_ms=(
            round(retry_event.delay_seconds * 1000)
            if retry_event.delay_seconds is not None
            else None
        ),
        outcome=outcomes[retry_event.event],
    )


def log_correction_event(
    logger: logging.Logger,
    *,
    event: str,
    reason: str,
    outcome: str,
) -> None:
    """记录一次性 Cypher 纠错的生命周期事件。"""

    _emit(
        logger,
        event=event,
        component="pipeline",
        stage="cypher_correction",
        attempt=1,
        max_attempts=1,
        reason=reason,
        delay_ms=None,
        outcome=outcome,
    )


def log_subquery_event(
    logger: logging.Logger,
    *,
    event: str,
    subquery_id: str,
    dependency_count: int,
    outcome: str,
    duration_ms: int | None = None,
) -> None:
    """记录不含查询内容的子问题调度事件。"""

    logger.info(
        "subquery_event",
        extra={
            "subquery_event": {
                "event": event,
                "component": "pipeline",
                "stage": "subquery_scheduler",
                "subquery_id": subquery_id,
                "dependency_count": dependency_count,
                "duration_ms": duration_ms,
                "outcome": outcome,
            }
        },
    )


def _emit(
    logger: logging.Logger,
    *,
    event: str,
    component: str,
    stage: str,
    attempt: int,
    max_attempts: int,
    reason: str,
    delay_ms: int | None,
    outcome: str,
) -> None:
    logger.warning(
        "recovery_event",
        extra={
            "recovery_event": {
                "event": event,
                "component": component,
                "stage": stage,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "reason": reason,
                "delay_ms": delay_ms,
                "outcome": outcome,
            }
        },
    )
