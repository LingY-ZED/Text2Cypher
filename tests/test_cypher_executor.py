from __future__ import annotations

from datetime import datetime
from typing import Any

from neo4j import READ_ACCESS

from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor


class FakeTransactionResult:
    """模拟事务查询结果。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def fetch(self, count: int) -> list[dict[str, Any]]:
        return self._records[:count]

    def keys(self) -> list[str]:
        return list(self._records[0]) if self._records else []


class FakeTransaction:
    """模拟只读事务。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def __enter__(self) -> FakeTransaction:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def run(self, query: Any, parameters: dict[str, Any]) -> FakeTransactionResult:
        del query, parameters
        return FakeTransactionResult(self._records)


class FakeSession:
    """模拟官方 Driver 的会话上下文。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self.transaction_timeout: float | None = None

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def begin_transaction(self, *, timeout: float) -> FakeTransaction:
        self.transaction_timeout = timeout
        return FakeTransaction(self._records)


class FakeExecutorDriver:
    """记录会话参数的模拟 Neo4j Driver。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self.session_kwargs: dict[str, Any] | None = None
        self.last_session: FakeSession | None = None

    def session(self, **kwargs: Any) -> FakeSession:
        self.session_kwargs = kwargs
        self.last_session = FakeSession(self._records)
        return self.last_session


def test_executor_limits_rows_uses_read_session_and_converts_nested_values() -> None:
    driver = FakeExecutorDriver(
        [
            {
                "编号": 1,
                "内容": {"时间": datetime(2026, 7, 22, 8, 0), "标签": ["一"]},
            },
            {"编号": 2, "内容": {"标签": ["二"]}},
            {"编号": 3, "内容": {"标签": ["三"]}},
        ]
    )

    result = Neo4jCypherExecutor(driver, "neo4j", 5, 2).execute("RETURN 1")

    assert result.columns == ("编号", "内容")
    assert len(result.rows) == 2
    assert result.rows[0]["内容"]["时间"] == "2026-07-22 08:00:00"
    assert result.truncated is True
    assert result.duration_ms is not None
    assert driver.session_kwargs == {
        "database": "neo4j",
        "default_access_mode": READ_ACCESS,
    }
    assert driver.last_session is not None
    assert driver.last_session.transaction_timeout == 5
