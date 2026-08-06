"""基础设施层的 Neo4j Cypher 安全规则和 EXPLAIN 校验。"""

from __future__ import annotations

import re
from typing import Any

from neo4j import Driver, Query, RoutingControl
from neo4j.exceptions import DriverError, Neo4jError

from text2cypher.domain.errors import CypherValidationError
from text2cypher.domain.models import ValidationReport


class Neo4jCypherValidator:
    """以本地规则和 EXPLAIN 双重确认 Cypher 只读。"""

    _forbidden_patterns = (
        re.compile(r"\bCREATE\b", re.IGNORECASE),
        re.compile(r"\bMERGE\b", re.IGNORECASE),
        re.compile(r"\bDELETE\b", re.IGNORECASE),
        re.compile(r"\bDETACH\b", re.IGNORECASE),
        re.compile(r"\bSET\b", re.IGNORECASE),
        re.compile(r"\bREMOVE\b", re.IGNORECASE),
        re.compile(r"\bDROP\b", re.IGNORECASE),
        re.compile(r"\bALTER\b", re.IGNORECASE),
        re.compile(r"\bGRANT\b", re.IGNORECASE),
        re.compile(r"\bDENY\b", re.IGNORECASE),
        re.compile(r"\bREVOKE\b", re.IGNORECASE),
        re.compile(r"\bFOREACH\b", re.IGNORECASE),
        re.compile(r"\bCALL\s+(?=[A-Za-z_`{])", re.IGNORECASE),
        re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE),
        re.compile(r"\bUSE\b", re.IGNORECASE),
    )
    _allowed_start = re.compile(
        r"^\s*(?:OPTIONAL\s+MATCH|MATCH|WITH|UNWIND|RETURN)\b",
        re.IGNORECASE,
    )

    def __init__(self, driver: Driver, database: str, timeout_seconds: int) -> None:
        self._driver = driver
        self._database = database
        self._timeout_seconds = float(timeout_seconds)

    def validate(self, cypher: str) -> ValidationReport:
        """拒绝危险查询，并通过 EXPLAIN 确认服务端只读类型。"""

        sanitized = self._sanitize(cypher)
        self._ensure_single_statement(sanitized)
        sanitized = sanitized.split(";", maxsplit=1)[0]
        if not self._allowed_start.match(sanitized):
            raise CypherValidationError("Cypher 必须以允许的只读子句开始")
        for pattern in self._forbidden_patterns:
            if pattern.search(sanitized):
                raise CypherValidationError("Cypher 包含禁止的写入或管理操作")

        try:
            result = self._driver.execute_query(
                Query(f"EXPLAIN {cypher}", timeout=self._timeout_seconds),
                routing_=RoutingControl.READ,
                database_=self._database,
            )
        except (DriverError, Neo4jError):
            raise CypherValidationError("Cypher 未通过 Neo4j EXPLAIN 校验") from None

        summary = self._summary(result)
        query_type = getattr(summary, "query_type", None)
        if query_type != "r":
            raise CypherValidationError("Neo4j 未将 Cypher 判定为只读查询")
        status_codes = self._status_codes(summary)
        if "01N52" in status_codes:
            raise CypherValidationError("Cypher 使用了当前 Schema 中不存在的属性")
        return ValidationReport(
            query_type=query_type,
            notifications=status_codes,
        )

    @staticmethod
    def _summary(result: Any) -> Any:
        if hasattr(result, "summary"):
            return result.summary
        return result[1]

    @staticmethod
    def _status_codes(summary: Any) -> tuple[str, ...]:
        statuses = getattr(summary, "gql_status_objects", ())
        return tuple(
            status_code
            for status in statuses
            if isinstance(
                status_code := getattr(status, "gql_status", None),
                str,
            )
        )

    @staticmethod
    def _sanitize(cypher: str) -> str:
        if not isinstance(cypher, str) or not cypher.strip():
            raise CypherValidationError("Cypher 不能为空")

        output: list[str] = []
        quote: str | None = None
        in_identifier = False
        index = 0
        while index < len(cypher):
            current = cypher[index]
            next_character = cypher[index + 1] if index + 1 < len(cypher) else ""
            if quote is not None:
                output.append(" ")
                if current == "\\" and next_character:
                    output.append(" ")
                    index += 2
                    continue
                if current == quote:
                    quote = None
                index += 1
                continue
            if in_identifier:
                output.append(" ")
                if current == chr(96):
                    in_identifier = False
                index += 1
                continue
            if current in ("'", '"'):
                quote = current
                output.append(" ")
                index += 1
                continue
            if current == chr(96):
                in_identifier = True
                output.append(" ")
                index += 1
                continue
            if current == "/" and next_character in ("/", "*"):
                raise CypherValidationError("Cypher 不允许包含注释")
            output.append(current)
            index += 1
        return "".join(output)

    @staticmethod
    def _ensure_single_statement(sanitized: str) -> None:
        semicolons = [
            index
            for index, character in enumerate(sanitized)
            if character == ";"
        ]
        if not semicolons:
            return
        if len(semicolons) != 1 or sanitized[semicolons[0] + 1 :].strip():
            raise CypherValidationError("Cypher 不允许包含多个语句")
