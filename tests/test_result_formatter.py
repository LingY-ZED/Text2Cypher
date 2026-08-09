from __future__ import annotations

import json

from text2cypher.components.result_formatter import JsonResultFormatter
from text2cypher.domain.models import (
    DependencyParameter,
    QueryResult,
    SubQueryError,
    SubQueryResponse,
    SubQueryStatus,
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
    assert payload["status"] == "success"
    assert payload["decomposed"] is False
    assert payload["sub_query_count"] == 1
    assert payload["sub_queries"][0]["columns"] == ["服务名称"]
    assert (
        payload["sub_queries"][0]["rows"][0]["服务名称"]
        == "ts-food-service"
    )
    assert payload["sub_queries"][0]["truncated"] is True
    assert payload["sub_queries"][0]["id"] == "q1"


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


def test_json_formatter_renders_partial_status_without_failed_cypher() -> None:
    formatted = JsonResultFormatter().format(
        question="分析依赖",
        sub_queries=(
            SubQueryResponse(
                "查询上游",
                "RETURN 'upstream'",
                QueryResult(("上游",), ({"上游": "caller"},)),
                id="q1",
            ),
            SubQueryResponse(
                "查询下游",
                None,
                None,
                id="q2",
                depends_on=("q1",),
                parameter_sources={
                    "dep_q1_rows": DependencyParameter(
                        "dep_q1_rows",
                        "q1",
                        ("上游",),
                    )
                },
                status=SubQueryStatus.FAILED,
                error=SubQueryError("validation_failed", "子查询未能安全完成执行"),
            ),
        ),
    )

    payload = json.loads(formatted)

    assert payload["status"] == "partial"
    assert payload["successful_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["sub_queries"][1]["cypher"] is None
    assert payload["sub_queries"][1]["rows"] is None
    assert payload["sub_queries"][1]["error"]["kind"] == "validation_failed"
