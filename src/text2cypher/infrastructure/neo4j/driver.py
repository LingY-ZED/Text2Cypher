"""基础设施层中 Neo4j 官方驱动的生命周期管理。"""

from __future__ import annotations

from neo4j import Driver, GraphDatabase, NotificationMinimumSeverity
from neo4j.exceptions import DriverError, Neo4jError

from text2cypher.config import Settings
from text2cypher.domain.errors import Neo4jConnectionError


class Neo4jDriverProvider:
    """延迟创建、验证并关闭共享的官方 Neo4j Driver。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
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
        )
        try:
            driver.verify_connectivity()
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
