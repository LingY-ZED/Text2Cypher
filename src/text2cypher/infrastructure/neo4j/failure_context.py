"""从 Neo4j 诊断对象提取 Corrector 可用的白名单字段。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from text2cypher.domain.models import (
    CypherFailureContext,
    CypherFailureKind,
    CypherFailureSource,
)


def neo4j_error_context(
    kind: CypherFailureKind,
    error: Exception,
    *,
    fallback_message: str,
) -> CypherFailureContext:
    """从服务端异常提取有限诊断，不保留任意 metadata。"""

    metadata = _mapping(getattr(error, "metadata", None))
    return _context(
        kind,
        message=_text(getattr(error, "message", None)) or fallback_message,
        code=_text(getattr(error, "code", None)),
        gql_status=_text(getattr(error, "gql_status", None)),
        classification=_text(getattr(error, "classification", None)),
        position=_mapping(metadata.get("position")),
    )


def neo4j_status_context(
    kind: CypherFailureKind,
    status: Any,
    *,
    fallback_message: str,
) -> CypherFailureContext:
    """从 EXPLAIN 状态对象提取标准诊断字段。"""

    diagnostic_record = _mapping(getattr(status, "diagnostic_record", None))
    position = _mapping(
        diagnostic_record.get("position") or diagnostic_record.get("_position")
    )
    return _context(
        kind,
        message=(
            _text(getattr(status, "status_description", None))
            or _text(getattr(status, "message", None))
            or fallback_message
        ),
        code=_text(getattr(status, "code", None)),
        gql_status=_text(getattr(status, "gql_status", None)),
        classification=_text(
            getattr(status, "classification", None)
            or getattr(status, "gql_classification", None)
        ),
        position=position,
    )


def _context(
    kind: CypherFailureKind,
    *,
    message: str,
    code: str | None,
    gql_status: str | None,
    classification: str | None,
    position: Mapping[str, Any],
) -> CypherFailureContext:
    return CypherFailureContext(
        kind=kind,
        source=CypherFailureSource.NEO4J,
        message=message,
        code=code,
        gql_status=gql_status,
        classification=classification,
        line=_nonnegative_int(position.get("line")),
        column=_nonnegative_int(position.get("column")),
        offset=_nonnegative_int(position.get("offset")),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str)
    }


def _text(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
