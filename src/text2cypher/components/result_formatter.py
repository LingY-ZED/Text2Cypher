"""组件层中查询结果的稳定 JSON 格式化器。"""

from __future__ import annotations

import json
from typing import Any

from text2cypher.domain.models import QueryResult


class JsonResultFormatter:
    """将成功响应渲染为易读的 UTF-8 JSON。"""

    def format(self, question: str, cypher: str, result: QueryResult) -> str:
        payload: dict[str, Any] = {
            "question": question,
            "cypher": cypher,
            "columns": list(result.columns),
            "rows": list(result.rows),
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "duration_ms": result.duration_ms,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
