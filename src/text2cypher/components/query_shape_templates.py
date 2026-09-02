"""完整调用链专用的 Schema 感知结构模板加载与选择。"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.domain.errors import FewShotLibraryError, QueryShapeTemplateError
from text2cypher.domain.models import (
    GraphSchema,
    QueryShapeTemplate,
    SchemaGraph,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader

_TEMPLATE_KEYS = {"id", "query_shape", "template", "schema_requirements"}
_PLACEHOLDER = "<TARGET_METHOD_FILTER>"
_PLACEHOLDER_PATTERN = re.compile(r"<[A-Z][A-Z_]*>")


class JsonQueryShapeTemplateLoader:
    """从包资源加载经过严格校验的完整链结构模板。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    def load(self) -> tuple[QueryShapeTemplate, ...]:
        try:
            payload = json.loads(self._read_text())
        except json.JSONDecodeError:
            raise QueryShapeTemplateError("查询结构模板不是有效 JSON") from None
        if not isinstance(payload, list) or not payload:
            raise QueryShapeTemplateError("查询结构模板必须是非空数组")

        templates = tuple(
            self._parse_template(value, index)
            for index, value in enumerate(payload)
        )
        ids = [template.id for template in templates]
        shapes = [template.query_shape for template in templates]
        if len(set(ids)) != len(ids) or len(set(shapes)) != len(shapes):
            raise QueryShapeTemplateError("查询结构模板 ID 和形状必须唯一")
        return templates

    def _read_text(self) -> str:
        try:
            if self._path is not None:
                return self._path.read_text(encoding="utf-8")
            return (
                files("text2cypher.resources")
                .joinpath("query_shape_templates.json")
                .read_text(encoding="utf-8")
            )
        except (OSError, TypeError):
            raise QueryShapeTemplateError("无法读取查询结构模板") from None

    @staticmethod
    def _parse_template(value: Any, index: int) -> QueryShapeTemplate:
        if not isinstance(value, dict) or set(value) != _TEMPLATE_KEYS:
            raise QueryShapeTemplateError(f"结构模板 {index} 字段集合不合法")
        if not all(isinstance(key, str) for key in value):
            raise QueryShapeTemplateError(f"结构模板 {index} 必须是对象")
        identifier = _required_text(value.get("id"), f"结构模板 {index}.id")
        raw_shape = _required_text(
            value.get("query_shape"),
            f"结构模板 {index}.query_shape",
        )
        try:
            query_shape = QueryShape(raw_shape)
        except ValueError:
            raise QueryShapeTemplateError(
                f"结构模板 {index}.query_shape 不在允许范围内"
            ) from None
        template = _required_text(
            value.get("template"),
            f"结构模板 {index}.template",
        )
        placeholders = set(_PLACEHOLDER_PATTERN.findall(template))
        if placeholders != {_PLACEHOLDER}:
            raise QueryShapeTemplateError("结构模板必须且只能使用目标方法占位符")
        try:
            JsonFewShotExampleLoader.validate_readonly_cypher(template)
            requirements = JsonFewShotExampleLoader.parse_schema_requirements(
                value.get("schema_requirements"),
                index,
            )
            return QueryShapeTemplate(
                id=identifier,
                query_shape=query_shape,
                template=template,
                schema_requirements=requirements,
            )
        except (FewShotLibraryError, TypeError, ValueError):
            raise QueryShapeTemplateError(
                f"结构模板 {index} 内容不合法"
            ) from None


class QueryShapeTemplateSelector:
    """只为 Schema 兼容的完整链形状提供确定性结构模板。"""

    def __init__(
        self,
        templates: tuple[QueryShapeTemplate, ...] | None = None,
        *,
        compatibility_filter: FewShotSchemaCompatibilityFilter | None = None,
    ) -> None:
        loaded = templates or JsonQueryShapeTemplateLoader().load()
        self._templates = {template.query_shape: template for template in loaded}
        if len(self._templates) != len(loaded):
            raise ValueError("查询结构模板形状不能重复")
        self._compatibility_filter = (
            compatibility_filter or FewShotSchemaCompatibilityFilter()
        )

    def select(
        self,
        query_shape: QueryShape,
        schema: GraphSchema,
        schema_graph: SchemaGraph,
    ) -> QueryShapeTemplate | None:
        template = self._templates.get(query_shape)
        if template is None:
            return None
        if not self._compatibility_filter.is_requirements_compatible(
            template.schema_requirements,
            schema,
            schema_graph,
        ):
            return None
        return template


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryShapeTemplateError(f"{field_name} 必须是非空文本")
    return value.strip()
