"""JSON output formatter used by the initial CLI."""

from __future__ import annotations

import json
from typing import Any

from text2cypher.models import QueryResult


class JsonResultFormatter:
    """Render a successful response as readable UTF-8 JSON."""

    def format(self, question: str, cypher: str, result: QueryResult) -> str:
        payload: dict[str, Any] = {
            "question": question,
            "cypher": cypher,
            "columns": list(result.columns),
            "rows": list(result.rows),
            "row_count": len(result.rows),
            "truncated": result.truncated,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

