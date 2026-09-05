"""从包资源或外部 JSON 文件加载 Few-shot 示例。"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from text2cypher.domain.errors import FewShotLibraryError
from text2cypher.domain.models import FewShotExample, FewShotSchemaRequirements
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.domain.resource_contracts import (
    ResourceContractError,
    parse_schema_requirements,
    validate_readonly_cypher,
)

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
        cls.validate_readonly_cypher(cypher)
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

    @staticmethod
    def _parse_requirements(
        value: Any,
        index: int,
    ) -> FewShotSchemaRequirements:
        return JsonFewShotExampleLoader.parse_schema_requirements(value, index)

    @staticmethod
    def parse_schema_requirements(
        value: Any,
        index: int = 0,
    ) -> FewShotSchemaRequirements:
        """兼容供其他可信资源复用的严格 Schema requirements 契约。"""

        try:
            return parse_schema_requirements(
                value,
                field_name=f"示例 {index}.schema_requirements",
            )
        except ResourceContractError as error:
            raise FewShotLibraryError(str(error)) from None

    @staticmethod
    def validate_readonly_cypher(cypher: str) -> None:
        """兼容 Few-shot 资源的只读 Cypher 校验入口。"""

        try:
            validate_readonly_cypher(cypher)
        except ResourceContractError as error:
            raise FewShotLibraryError(str(error)) from None

    @staticmethod
    def _object(value: Any, field_name: str) -> dict[str, Any]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise FewShotLibraryError(f"{field_name} 必须是对象")
        return value

    @staticmethod
    def _text(data: dict[str, Any], key: str, field_name: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FewShotLibraryError(f"{field_name}.{key} 必须是非空文本")
        return value.strip()

    @staticmethod
    def _text_tuple(
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
