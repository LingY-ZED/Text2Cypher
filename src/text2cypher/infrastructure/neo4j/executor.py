"""基础设施层的只读 Cypher 执行和 Neo4j 值转换。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from time import perf_counter
from typing import Any

from neo4j import READ_ACCESS, Driver
from neo4j.exceptions import DriverError, Neo4jError
from neo4j.graph import Node, Path, Relationship
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time

from text2cypher.domain.errors import CypherExecutionError
from text2cypher.domain.models import QueryResult


class Neo4jCypherExecutor:
    """在受限只读事务中执行校验通过的 Cypher。"""

    def __init__(
        self,
        driver: Driver,
        database: str,
        timeout_seconds: int,
        max_result_rows: int,
    ) -> None:
        self._driver = driver
        self._database = database
        self._timeout_seconds = float(timeout_seconds)
        self._max_result_rows = max_result_rows

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult:
        """读取最多配置条数加一条记录，并返回是否截断的结果。"""

        started_at = perf_counter()
        try:
            with self._driver.session(
                database=self._database,
                default_access_mode=READ_ACCESS,
            ) as session:
                with session.begin_transaction(
                    timeout=self._timeout_seconds
                ) as transaction:
                    records, columns = self._read_records(
                        transaction,
                        cypher,
                        dict(parameters or {}),
                    )
        except (DriverError, Neo4jError):
            raise CypherExecutionError("Neo4j 无法执行 Cypher 查询") from None

        truncated = len(records) > self._max_result_rows
        bounded_records = records[: self._max_result_rows]
        rows = tuple(
            {
                column: self._to_json(record[column])
                for column in columns
            }
            for record in bounded_records
        )
        duration_ms = round((perf_counter() - started_at) * 1000)
        return QueryResult(
            columns=tuple(columns),
            rows=rows,
            truncated=truncated,
            duration_ms=duration_ms,
        )

    def _read_records(
        self,
        transaction: Any,
        cypher: str,
        parameters: dict[str, Any],
    ) -> tuple[list[Any], list[str]]:
        result = transaction.run(cypher, parameters)
        records = result.fetch(self._max_result_rows + 1)
        return records, list(result.keys())

    @classmethod
    def _to_json(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Node):
            return {
                "element_id": value.element_id,
                "labels": sorted(value.labels),
                "properties": {
                    key: cls._to_json(item)
                    for key, item in value.items()
                },
            }
        if isinstance(value, Relationship):
            start_node = value.start_node
            end_node = value.end_node
            return {
                "element_id": value.element_id,
                "type": value.type,
                "start_element_id": (
                    start_node.element_id if start_node is not None else None
                ),
                "end_element_id": (
                    end_node.element_id if end_node is not None else None
                ),
                "properties": {
                    key: cls._to_json(item)
                    for key, item in value.items()
                },
            }
        if isinstance(value, Path):
            return {
                "nodes": [cls._to_json(node) for node in value.nodes],
                "relationships": [
                    cls._to_json(relationship)
                    for relationship in value.relationships
                ],
            }
        if isinstance(value, Point):
            coordinates = list(value)
            return {
                "srid": value.srid,
                "coordinates": coordinates,
            }
        if isinstance(value, (date, datetime, time, Date, DateTime, Time, Duration)):
            return str(value)
        if isinstance(value, Mapping):
            return {
                str(key): cls._to_json(item)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return [cls._to_json(item) for item in value]
        if isinstance(value, set):
            return [cls._to_json(item) for item in sorted(value, key=str)]
        if isinstance(value, (bytes, bytearray)):
            return value.hex()
        return str(value)
