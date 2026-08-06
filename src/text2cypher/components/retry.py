"""可由外部适配器复用的确定性瞬态故障重试工具。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

_Result = TypeVar("_Result")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """限制一次外部操作的尝试次数和指数退避等待。"""

    enabled: bool = True
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts 必须在 1 到 3 之间")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds 必须为正数")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds 不能小于 base_delay_seconds")

    @property
    def effective_max_attempts(self) -> int:
        """返回当前开关生效后的实际尝试次数。"""

        return self.max_attempts if self.enabled else 1

    def delay_for_retry(
        self,
        failed_attempt: int,
        retry_after_seconds: float | None = None,
    ) -> float:
        """返回一次失败后应等待的秒数，并将服务端建议限制在上限内。"""

        if not 1 <= failed_attempt < self.effective_max_attempts:
            raise ValueError("failed_attempt 必须对应一个可重试的失败尝试")
        exponential_delay = float(
            min(
                self.max_delay_seconds,
                self.base_delay_seconds * (2 ** (failed_attempt - 1)),
            )
        )
        if retry_after_seconds is None or retry_after_seconds < 0:
            return exponential_delay
        return float(min(self.max_delay_seconds, retry_after_seconds))


@dataclass(frozen=True, slots=True)
class RetryAttempt:
    """一次已经确定会发生的重试，供日志或测试观察。"""

    attempt: int
    max_attempts: int
    reason: str
    delay_seconds: float


class RetryableOperationError(Exception):
    """由外部适配器显式标记为可安全重放的故障。"""

    def __init__(
        self,
        cause: Exception,
        *,
        reason: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


class RetryExecutor:
    """只重放显式标记为瞬态的操作，且不依赖具体外部服务。"""

    def __init__(
        self,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], None],
        on_retry: Callable[[RetryAttempt], None] | None = None,
    ) -> None:
        self._policy = policy
        self._sleep = sleep
        self._on_retry = on_retry

    def run(self, operation: Callable[[], _Result]) -> _Result:
        """运行操作；重试耗尽时重新抛出原始安全异常。"""

        max_attempts = self._policy.effective_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                return operation()
            except RetryableOperationError as error:
                if attempt == max_attempts:
                    raise error.cause from None
                delay_seconds = self._policy.delay_for_retry(
                    attempt,
                    error.retry_after_seconds,
                )
                if self._on_retry is not None:
                    self._on_retry(
                        RetryAttempt(
                            attempt=attempt,
                            max_attempts=max_attempts,
                            reason=error.reason,
                            delay_seconds=delay_seconds,
                        )
                    )
                self._sleep(delay_seconds)
        raise AssertionError("重试循环必须在返回或抛出时结束")
