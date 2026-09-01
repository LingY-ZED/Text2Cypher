"""从包资源或外部 JSON 文件加载 Few-shot 示例。"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from text2cypher.domain.errors import FewShotLibraryError
from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    RelationshipPattern,
)
from text2cypher.domain.query_shapes import QueryShape

_REQUIRED_EXAMPLE_KEYS = {
    "id",
    "category",
    "question",
    "cypher",
    "aliases",
    "tags",
    "schema_requirements",
}
_OPTIONAL_EXAMPLE_KEYS = {"query_shape"}
_REQUIREMENT_KEYS = {
    "node_labels",
    "relationship_types",
    "node_properties",
    "relationship_properties",
    "patterns",
}
_PATTERN_KEYS = {"start_labels", "relationship_type", "end_labels"}
_ALLOWED_START = re.compile(
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


class JsonFewShotExampleLoader:
    """严格解析可信的、版本控制内的 Few-shot JSON 示例库。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    def load(self) -> tuple[FewShotExample, ...]:
        raw_text = self._read_text()
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            raise FewShotLibraryError("Few-shot 示例库不是有效 JSON") from None
        if not isinstance(payload, list) or not payload:
            raise FewShotLibraryError("Few-shot 示例库必须是非空数组")

        examples = tuple(
            self._parse_example(value, index)
            for index, value in enumerate(payload)
        )
        identifiers = [example.id for example in examples]
        if len(set(identifiers)) != len(identifiers):
            raise FewShotLibraryError("Few-shot 示例 ID 不能重复")
        return examples

    def _read_text(self) -> str:
        try:
            if self._path is not None:
                return self._path.read_text(encoding="utf-8")
            resource = files("text2cypher.resources").joinpath(
                "few_shot_examples.json"
            )
            return resource.read_text(encoding="utf-8")
        except (OSError, TypeError):
            raise FewShotLibraryError("无法读取 Few-shot 示例库") from None

    @classmethod
    def _parse_example(cls, value: Any, index: int) -> FewShotExample:
        data = cls._object(value, f"示例 {index}")
        if (
            not _REQUIRED_EXAMPLE_KEYS.issubset(data)
            or not set(data).issubset(
                _REQUIRED_EXAMPLE_KEYS | _OPTIONAL_EXAMPLE_KEYS
            )
        ):
            raise FewShotLibraryError(f"示例 {index} 字段集合不合法")
        cypher = cls._text(data, "cypher", f"示例 {index}")
        cls._validate_cypher(cypher)
        requirements = cls._parse_requirements(
            data["schema_requirements"],
            index,
        )
        raw_query_shape = data.get("query_shape", QueryShape.GENERAL.value)
        if not isinstance(raw_query_shape, str) or not raw_query_shape.strip():
            raise FewShotLibraryError(f"示例 {index}.query_shape 必须是非空文本")
        try:
            query_shape = QueryShape(raw_query_shape.strip())
        except ValueError:
            raise FewShotLibraryError(
                f"示例 {index}.query_shape 不在允许范围内"
            ) from None
        try:
            return FewShotExample(
                id=cls._text(data, "id", f"示例 {index}"),
                category=cls._text(data, "category", f"示例 {index}"),
                question=cls._text(data, "question", f"示例 {index}"),
                cypher=cypher,
                aliases=cls._text_tuple(
                    data.get("aliases"),
                    f"示例 {index}.aliases",
                    require_values=True,
                ),
                tags=cls._text_tuple(
                    data.get("tags"),
                    f"示例 {index}.tags",
                    require_values=True,
                ),
                schema_requirements=requirements,
                query_shape=query_shape,
            )
        except (TypeError, ValueError):
            raise FewShotLibraryError("Few-shot 示例字段不合法") from None

    @classmethod
    def _parse_requirements(
        cls,
        value: Any,
        index: int,
    ) -> FewShotSchemaRequirements:
        field_name = f"示例 {index}.schema_requirements"
        data = cls._object(value, field_name)
        cls._exact_keys(data, _REQUIREMENT_KEYS, field_name)
        patterns_value = data.get("patterns")
        if not isinstance(patterns_value, list):
            raise FewShotLibraryError(f"{field_name}.patterns 必须是数组")
        patterns = tuple(
            cls._parse_pattern(pattern, f"{field_name}.patterns[{pattern_index}]")
            for pattern_index, pattern in enumerate(patterns_value)
        )
        node_labels = cls._text_tuple(
            data.get("node_labels"),
            f"{field_name}.node_labels",
        )
        relationship_types = cls._text_tuple(
            data.get("relationship_types"),
            f"{field_name}.relationship_types",
        )
        node_properties = cls._text_mapping(
            data.get("node_properties"),
            f"{field_name}.node_properties",
        )
        relationship_properties = cls._text_mapping(
            data.get("relationship_properties"),
            f"{field_name}.relationship_properties",
        )
        cls._validate_requirements(
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
            raise FewShotLibraryError("Few-shot Schema requirements 不合法") from None

    @staticmethod
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
            raise FewShotLibraryError(
                f"{field_name}.node_properties 包含未声明的节点标签"
            )
        if not set(relationship_properties).issubset(relationship_type_set):
            raise FewShotLibraryError(
                f"{field_name}.relationship_properties 包含未声明的关系类型"
            )
        if len(set(patterns)) != len(patterns):
            raise FewShotLibraryError(f"{field_name}.patterns 不能包含重复值")
        for pattern in patterns:
            if (
                not set(pattern.start_labels).issubset(label_set)
                or not set(pattern.end_labels).issubset(label_set)
                or pattern.relationship_type not in relationship_type_set
            ):
                raise FewShotLibraryError(
                    f"{field_name}.patterns 包含未声明的 Schema 元素"
                )

    @classmethod
    def _parse_pattern(
        cls,
        value: Any,
        field_name: str,
    ) -> RelationshipPattern:
        data = cls._object(value, field_name)
        cls._exact_keys(data, _PATTERN_KEYS, field_name)
        try:
            return RelationshipPattern(
                start_labels=cls._text_tuple(
                    data.get("start_labels"),
                    f"{field_name}.start_labels",
                    require_values=True,
                ),
                relationship_type=cls._text(
                    data,
                    "relationship_type",
                    field_name,
                ),
                end_labels=cls._text_tuple(
                    data.get("end_labels"),
                    f"{field_name}.end_labels",
                    require_values=True,
                ),
            )
        except ValueError:
            raise FewShotLibraryError("Few-shot relationship pattern 不合法") from None

    @staticmethod
    def _validate_cypher(cypher: str) -> None:
        if not _ALLOWED_START.match(cypher):
            raise FewShotLibraryError("Few-shot Cypher 必须以只读子句开始")
        if (
            _FORBIDDEN_CYPHER.search(cypher)
            or _CALL_CLAUSE.search(cypher)
            or ";" in cypher
            or "//" in cypher
            or "/*" in cypher
            or "*/" in cypher
        ):
            raise FewShotLibraryError("Few-shot Cypher 包含禁止内容")

    @staticmethod
    def _object(value: Any, field_name: str) -> dict[str, Any]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise FewShotLibraryError(f"{field_name} 必须是对象")
        return value

    @staticmethod
    def _exact_keys(
        data: dict[str, Any],
        expected: set[str],
        field_name: str,
    ) -> None:
        if set(data) != expected:
            raise FewShotLibraryError(f"{field_name} 字段集合不合法")

    @staticmethod
    def _text(data: dict[str, Any], key: str, field_name: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FewShotLibraryError(f"{field_name}.{key} 必须是非空文本")
        return value.strip()

    @classmethod
    def _text_tuple(
        cls,
        value: Any,
        field_name: str,
        *,
        require_values: bool = False,
    ) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise FewShotLibraryError(f"{field_name} 必须是数组")
        if require_values and not value:
            raise FewShotLibraryError(f"{field_name} 不能为空")
        values = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise FewShotLibraryError(f"{field_name} 必须只包含非空文本")
            values.append(item.strip())
        if len(set(values)) != len(values):
            raise FewShotLibraryError(f"{field_name} 不能包含重复值")
        return tuple(values)

    @classmethod
    def _text_mapping(
        cls,
        value: Any,
        field_name: str,
    ) -> dict[str, tuple[str, ...]]:
        data = cls._object(value, field_name)
        normalized: dict[str, tuple[str, ...]] = {}
        for key, properties in data.items():
            normalized_key = key.strip()
            if not normalized_key:
                raise FewShotLibraryError(f"{field_name} 的键必须是非空文本")
            if normalized_key in normalized:
                raise FewShotLibraryError(f"{field_name} 不能包含重复键")
            normalized[normalized_key] = cls._text_tuple(
                properties,
                f"{field_name}.{key}",
            )
        return normalized
