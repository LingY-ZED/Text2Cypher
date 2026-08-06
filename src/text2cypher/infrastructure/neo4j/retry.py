"""Neo4j 异常分类及只读操作的受限重试。"""

from __future__ import annotations

from collections.abc import Callable

from neo4j.exceptions import (
    AuthError,
    DriverError,
    Forbidden,
    Neo4jError,
    ServiceUnavailable,
    SessionExpired,
)

from text2cypher.components.retry import RetryableOperationError, RetryExecutor


def is_transient_neo4j_error(error: DriverError | Neo4jError) -> bool:
    """判断官方驱动异常是否允许安全重放同一只读操作。"""

    return isinstance(error, (ServiceUnavailable, SessionExpired)) or (
        isinstance(error, Neo4jError) and error.is_retryable()
    )


def is_neo4j_access_error(error: DriverError | Neo4jError) -> bool:
    """判断错误是否表示认证或授权失败。"""

    if isinstance(error, (AuthError, Forbidden)):
        return True
    code = getattr(error, "code", "")
    return isinstance(code, str) and code.startswith("Neo.ClientError.Security.")


def run_with_neo4j_retry[Result](
    retry_executor: RetryExecutor,
    operation: Callable[[], Result],
) -> Result:
    """仅重试可重放的官方驱动错误，耗尽后保留原始异常类别。"""

    def retryable_operation() -> Result:
        try:
            return operation()
        except (DriverError, Neo4jError) as error:
            if is_transient_neo4j_error(error):
                raise RetryableOperationError(
                    error,
                    reason="neo4j_transient",
                ) from None
            raise

    return retry_executor.run(retryable_operation)
