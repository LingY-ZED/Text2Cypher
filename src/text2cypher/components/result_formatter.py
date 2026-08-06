"""组件层中查询结果的稳定 JSON 格式化器。"""

from __future__ import annotations

import json
from typing import Any

from text2cypher.domain.models import SubQueryResponse


class JsonResultFormatter:
    """将成功响应渲染为易读的 UTF-8 JSON。"""

    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> str:
        payload: dict[str, Any] = {
            "question": question,
            "decomposed": len(sub_queries) > 1,
            "sub_query_count": len(sub_queries),
            "sub_queries": [
                {
                    "question": sub_query.question,
                    "cypher": sub_query.cypher,
                    "columns": list(sub_query.result.columns),
                    "rows": list(sub_query.result.rows),
                    "row_count": len(sub_query.result.rows),
                    "truncated": sub_query.result.truncated,
                    "duration_ms": sub_query.result.duration_ms,
                }
                for sub_query in sub_queries
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
