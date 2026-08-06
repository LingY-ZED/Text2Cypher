"""基础设施层中 Neo4j 官方驱动的生命周期管理。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import sleep

from neo4j import Driver, GraphDatabase, NotificationMinimumSeverity
from neo4j.exceptions import DriverError, Neo4jError

from text2cypher.components.recovery_logging import log_retry_event
from text2cypher.components.retry import RetryExecutor, RetryPolicy
from text2cypher.config import Settings
from text2cypher.domain.errors import Neo4jConnectionError
from text2cypher.infrastructure.neo4j.retry import run_with_neo4j_retry

_LOGGER = logging.getLogger(__name__)


class Neo4jDriverProvider:
    """延迟创建、验证并关闭共享的官方 Neo4j Driver。"""

    def __init__(
        self,
        settings: Settings,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep_func: Callable[[float], None] = sleep,
    ) -> None:
        self._settings = settings
        self._retry_executor = RetryExecutor(
            retry_policy or RetryPolicy(),
            sleep=sleep_func,
            on_event=lambda event: log_retry_event(
                _LOGGER,
                component="neo4j",
                stage="connectivity",
                retry_event=event,
            ),
        )
        self._driver: Driver | None = None

    @property
    def driver(self) -> Driver:
        """返回已经通过连通性验证的共享 Driver。"""

        if self._driver is not None:
            return self._driver

        driver = GraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(
                self._settings.neo4j_username,
                self._settings.neo4j_password.get_secret_value(),
            ),
            warn_notification_severity=NotificationMinimumSeverity.OFF,
            max_transaction_retry_time=0,
        )
        try:
            run_with_neo4j_retry(
                self._retry_executor,
                driver.verify_connectivity,
            )
        except (DriverError, Neo4jError):
            driver.close()
            raise Neo4jConnectionError("无法连接或认证 Neo4j") from None

        self._driver = driver
        return driver

    def close(self) -> None:
        """关闭已创建的 Driver。"""

        if self._driver is not None:
            self._driver.close()
            self._driver = None
