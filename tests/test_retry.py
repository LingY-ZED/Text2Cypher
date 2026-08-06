from __future__ import annotations

import pytest

from text2cypher.components.retry import (
    RetryableOperationError,
    RetryEvent,
    RetryExecutor,
    RetryPolicy,
)


def test_retry_executor_retries_marked_failure_and_records_backoff() -> None:
    attempts = 0
    waits: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableOperationError(
                RuntimeError("temporary"),
                reason="temporary",
            )
        return "ok"

    result = RetryExecutor(
        RetryPolicy(),
        sleep=waits.append,
    ).run(operation)

    assert result == "ok"
    assert attempts == 3
    assert waits == [0.5, 1.0]


def test_retry_executor_honors_clamped_retry_after() -> None:
    waits: list[float] = []

    def operation() -> None:
        raise RetryableOperationError(
            RuntimeError("temporary"),
            reason="busy",
            retry_after_seconds=9,
        )

    with pytest.raises(RuntimeError, match="temporary"):
        RetryExecutor(
            RetryPolicy(max_attempts=2, max_delay_seconds=2),
            sleep=waits.append,
        ).run(operation)

    assert waits == [2]


def test_retry_executor_can_be_disabled() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise RetryableOperationError(RuntimeError("temporary"), reason="temporary")

    with pytest.raises(RuntimeError, match="temporary"):
        RetryExecutor(
            RetryPolicy(enabled=False),
            sleep=lambda _: pytest.fail("不应等待"),
        ).run(operation)

    assert attempts == 1


def test_retry_executor_emits_scheduled_succeeded_and_exhausted_events() -> None:
    events: list[RetryEvent] = []
    attempts = 0

    def eventually_succeeds() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableOperationError(RuntimeError("temporary"), reason="busy")
        return "ok"

    executor = RetryExecutor(
        RetryPolicy(max_attempts=2),
        sleep=lambda _: None,
        on_event=events.append,
    )

    assert executor.run(eventually_succeeds) == "ok"
    assert events == [
        RetryEvent("retry_scheduled", 1, 2, "busy", 0.5),
        RetryEvent("retry_succeeded", 2, 2, "busy", None),
    ]

    with pytest.raises(RuntimeError, match="temporary"):
        executor.run(
            lambda: (_ for _ in ()).throw(
                RetryableOperationError(RuntimeError("temporary"), reason="busy")
            )
        )

    assert events[-2:] == [
        RetryEvent("retry_scheduled", 1, 2, "busy", 0.5),
        RetryEvent("retry_exhausted", 2, 2, "busy", None),
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "1 到 3"),
        ({"max_attempts": 4}, "1 到 3"),
        ({"base_delay_seconds": 0}, "正数"),
        ({"max_delay_seconds": 0.1}, "不能小于"),
    ],
)
def test_retry_policy_rejects_invalid_values(
    kwargs: dict[str, float | int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RetryPolicy(**kwargs)
