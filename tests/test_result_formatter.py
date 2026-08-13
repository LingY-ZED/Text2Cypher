from __future__ import annotations

import json

from text2cypher.components.result_formatter import JsonResultFormatter
from text2cypher.domain.models import (
    QueryResult,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
    SubQueryResponse,
)


def test_json_formatter_preserves_chinese_values() -> None:
    formatted = JsonResultFormatter().format(
        question="列出服务",
        sub_queries=(
            SubQueryResponse(
                question="列出服务",
                cypher="MATCH (s:微服务) RETURN s.服务名称",
                result=QueryResult(
                    columns=("服务名称",),
                    rows=({"服务名称": "ts-food-service"},),
                    truncated=True,
                ),
            ),
        ),
    )

    payload = json.loads(formatted)

    assert payload["question"] == "列出服务"
    assert payload["decomposed"] is False
    assert payload["sub_query_count"] == 1
    assert payload["sub_queries"][0]["columns"] == ["服务名称"]
    assert (
        payload["sub_queries"][0]["rows"][0]["服务名称"]
        == "ts-food-service"
    )
    assert payload["sub_queries"][0]["truncated"] is True


def test_json_formatter_groups_multiple_sub_query_results() -> None:
    formatted = JsonResultFormatter().format(
        question="分析上下游",
        sub_queries=(
            SubQueryResponse(
                "查询上游",
                "RETURN 'upstream'",
                QueryResult(("上游",), ({"上游": "caller"},)),
            ),
            SubQueryResponse(
                "查询下游",
                "RETURN 'downstream'",
                QueryResult(("下游",), ({"下游": "service"},)),
            ),
        ),
    )

    payload = json.loads(formatted)

    assert payload["decomposed"] is True
    assert payload["sub_query_count"] == 2
    assert [item["question"] for item in payload["sub_queries"]] == [
        "查询上游",
        "查询下游",
    ]
    assert payload["sub_queries"][1]["rows"] == [{"下游": "service"}]


def test_json_formatter_includes_summary_without_changing_raw_results() -> None:
    formatted = JsonResultFormatter().format(
        question="列出服务",
        sub_queries=(
            SubQueryResponse(
                "列出服务",
                "RETURN 'food-service' AS 服务",
                QueryResult(("服务",), ({"服务": "food-service"},)),
            ),
        ),
        summary=ResultSummary(
            "查询到 food-service。",
            ResultSummaryMode.TEMPLATE,
            ResultSummaryFallbackReason.DISABLED,
        ),
    )

    payload = json.loads(formatted)

    assert payload["sub_queries"][0]["rows"] == [{"服务": "food-service"}]
    assert payload["summary"] == {
        "answer": "查询到 food-service。",
        "mode": "template",
        "fallback_reason": "disabled",
    }
