"""组件层中查询结果的稳定 JSON 格式化器。"""

from __future__ import annotations

import json
from typing import Any

from text2cypher.domain.models import SubQueryResponse, SubQueryStatus


class JsonResultFormatter:
    """将成功或部分成功响应渲染为易读的 UTF-8 JSON。"""

    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> str:
        successful_count = sum(
            item.status is SubQueryStatus.SUCCESS for item in sub_queries
        )
        payload: dict[str, Any] = {
            "question": question,
            "status": (
                "success" if successful_count == len(sub_queries) else "partial"
            ),
            "decomposed": len(sub_queries) > 1,
            "sub_query_count": len(sub_queries),
            "successful_count": successful_count,
            "failed_count": sum(
                item.status is SubQueryStatus.FAILED for item in sub_queries
            ),
            "blocked_count": sum(
                item.status is SubQueryStatus.BLOCKED for item in sub_queries
            ),
            "sub_queries": [self._render_sub_query(item) for item in sub_queries],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _render_sub_query(sub_query: SubQueryResponse) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": sub_query.id,
            "depends_on": list(sub_query.depends_on),
            "parameter_sources": {
                name: {
                    "source_id": specification.source_id,
                    "columns": list(specification.columns),
                }
                for name, specification in sub_query.parameter_sources.items()
            },
            "status": sub_query.status.value,
            "question": sub_query.question,
            "cypher": sub_query.cypher,
        }
        if sub_query.status is not SubQueryStatus.SUCCESS:
            error = sub_query.error
            if error is None:
                raise AssertionError("未成功子查询必须包含安全错误")
            payload.update(
                {
                    "columns": None,
                    "rows": None,
                    "row_count": None,
                    "truncated": None,
                    "duration_ms": None,
                    "error": {
                        "kind": error.kind,
                        "message": error.message,
                    },
                }
            )
            return payload

        result = sub_query.result
        if result is None:
            raise AssertionError("成功子查询必须包含结果")
        payload.update(
            {
                "columns": list(result.columns),
                "rows": list(result.rows),
                "row_count": len(result.rows),
                "truncated": result.truncated,
                "duration_ms": result.duration_ms,
                "error": None,
            }
        )
        return payload
