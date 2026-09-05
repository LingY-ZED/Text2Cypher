"""可信查询资源共享的纯数据契约。"""

from __future__ import annotations

import re
from typing import Any

from text2cypher.domain.models import FewShotSchemaRequirements, RelationshipPattern

_REQUIREMENT_KEYS = {
    "node_labels",
    "relationship_types",
    "node_properties",
    "relationship_properties",
    "patterns",
}
_PATTERN_KEYS = {"start_labels", "relationship_type", "end_labels"}
_ALLOWED_READONLY_START = re.compile(
    r"^\s*(?:MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN)\b",
    re.IGNORECASE,
)
_FORBIDDEN_CYPHER = re.compile(
    r"\b(?:"
    r"CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|GRANT|DENY|"
    r"REVOKE|FOREACH|LOAD\s+CSV"
    r")\b",
    re.IGNORECASE,
)
_CALL_CLAUSE = re.compile(r"\bCALL\s+(?:\{|[A-Za-z_`])", re.IGNORECASE)


class ResourceContractError(ValueError):
    """可信资源未满足共享数据契约。"""


def parse_schema_requirements(
    value: Any,
    *,
    field_name: str,
) -> FewShotSchemaRequirements:
    """解析 Few-shot 与结构模板共同使用的 Schema requirements。"""

    data = _object(value, field_name)
    _exact_keys(data, _REQUIREMENT_KEYS, field_name)
    patterns_value = data.get("patterns")
    if not isinstance(patterns_value, list):
        raise ResourceContractError(f"{field_name}.patterns 必须是数组")
    patterns = tuple(
        _parse_pattern(pattern, f"{field_name}.patterns[{index}]")
        for index, pattern in enumerate(patterns_value)
    )
    node_labels = _text_tuple(data.get("node_labels"), f"{field_name}.node_labels")
    relationship_types = _text_tuple(
        data.get("relationship_types"),
        f"{field_name}.relationship_types",
    )
    node_properties = _text_mapping(
        data.get("node_properties"),
        f"{field_name}.node_properties",
    )
    relationship_properties = _text_mapping(
        data.get("relationship_properties"),
        f"{field_name}.relationship_properties",
    )
    _validate_requirements(
        node_labels=node_labels,
        relationship_types=relationship_types,
        node_properties=node_properties,
        relationship_properties=relationship_properties,
        patterns=patterns,
        field_name=field_name,
    )
    try:
        return FewShotSchemaRequirements(
            node_labels=node_labels,
            relationship_types=relationship_types,
            node_properties=node_properties,
            relationship_properties=relationship_properties,
            patterns=patterns,
        )
    except ValueError:
        raise ResourceContractError("Few-shot Schema requirements 不合法") from None


def validate_readonly_cypher(cypher: str) -> None:
    """验证可信示例或模板中的 Cypher 未越过只读资源边界。"""

    if not _ALLOWED_READONLY_START.match(cypher):
        raise ResourceContractError("Few-shot Cypher 必须以只读子句开始")
    if (
        _FORBIDDEN_CYPHER.search(cypher)
        or _CALL_CLAUSE.search(cypher)
        or ";" in cypher
        or "//" in cypher
        or "/*" in cypher
        or "*/" in cypher
    ):
        raise ResourceContractError("Few-shot Cypher 包含禁止内容")


def _validate_requirements(
    *,
    node_labels: tuple[str, ...],
    relationship_types: tuple[str, ...],
    node_properties: dict[str, tuple[str, ...]],
    relationship_properties: dict[str, tuple[str, ...]],
    patterns: tuple[RelationshipPattern, ...],
    field_name: str,
) -> None:
    label_set = set(node_labels)
    relationship_type_set = set(relationship_types)
    if not set(node_properties).issubset(label_set):
        raise ResourceContractError(
            f"{field_name}.node_properties 包含未声明的节点标签"
        )
    if not set(relationship_properties).issubset(relationship_type_set):
        raise ResourceContractError(
            f"{field_name}.relationship_properties 包含未声明的关系类型"
        )
    if len(set(patterns)) != len(patterns):
        raise ResourceContractError(f"{field_name}.patterns 不能包含重复值")
    for pattern in patterns:
        if (
            not set(pattern.start_labels).issubset(label_set)
            or not set(pattern.end_labels).issubset(label_set)
            or pattern.relationship_type not in relationship_type_set
        ):
            raise ResourceContractError(
                f"{field_name}.patterns 包含未声明的 Schema 元素"
            )


def _parse_pattern(value: Any, field_name: str) -> RelationshipPattern:
    data = _object(value, field_name)
    _exact_keys(data, _PATTERN_KEYS, field_name)
    try:
        return RelationshipPattern(
            start_labels=_text_tuple(
                data.get("start_labels"),
                f"{field_name}.start_labels",
                require_values=True,
            ),
            relationship_type=_text(data, "relationship_type", field_name),
            end_labels=_text_tuple(
                data.get("end_labels"),
                f"{field_name}.end_labels",
                require_values=True,
            ),
        )
    except ValueError:
        raise ResourceContractError("Few-shot relationship pattern 不合法") from None


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ResourceContractError(f"{field_name} 必须是对象")
    return value


def _exact_keys(
    data: dict[str, Any],
    expected: set[str],
    field_name: str,
) -> None:
    if set(data) != expected:
        raise ResourceContractError(f"{field_name} 字段集合不合法")


def _text(data: dict[str, Any], key: str, field_name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResourceContractError(f"{field_name}.{key} 必须是非空文本")
    return value.strip()


def _text_tuple(
    value: Any,
    field_name: str,
    *,
    require_values: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ResourceContractError(f"{field_name} 必须是数组")
    if require_values and not value:
        raise ResourceContractError(f"{field_name} 不能为空")
    values = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ResourceContractError(f"{field_name} 必须只包含非空文本")
        values.append(item.strip())
    if len(set(values)) != len(values):
        raise ResourceContractError(f"{field_name} 不能包含重复值")
    return tuple(values)


def _text_mapping(value: Any, field_name: str) -> dict[str, tuple[str, ...]]:
    data = _object(value, field_name)
    normalized: dict[str, tuple[str, ...]] = {}
    for key, properties in data.items():
        normalized_key = key.strip()
        if not normalized_key:
            raise ResourceContractError(f"{field_name} 的键必须是非空文本")
        if normalized_key in normalized:
            raise ResourceContractError(f"{field_name} 不能包含重复键")
        normalized[normalized_key] = _text_tuple(properties, f"{field_name}.{key}")
    return normalized
