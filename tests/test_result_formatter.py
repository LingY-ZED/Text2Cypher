from __future__ import annotations

import json

from text2cypher.models import QueryResult
from text2cypher.result_formatter import JsonResultFormatter


def test_json_formatter_preserves_chinese_values() -> None:
    formatted = JsonResultFormatter().format(
        question="列出服务",
        cypher="MATCH (s:微服务) RETURN s.服务名称",
        result=QueryResult(
            columns=("服务名称",),
            rows=({"服务名称": "ts-food-service"},),
            truncated=True,
        ),
    )

    payload = json.loads(formatted)

    assert payload["question"] == "列出服务"
    assert payload["columns"] == ["服务名称"]
    assert payload["rows"][0]["服务名称"] == "ts-food-service"
    assert payload["truncated"] is True

