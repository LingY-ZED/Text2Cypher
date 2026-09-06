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


def test_json_formatter_preserves_complete_call_chain_segment_table() -> None:
    columns = (
        "根方法",
        "图谱版本",
        "层级",
        "链路类型",
        "源服务",
        "源API路径",
        "源HTTP方法",
        "源方法",
        "方法路径",
        "下游API路径",
        "目标服务",
        "目标API路径",
        "目标HTTP方法",
        "目标方法",
        "消息交换机",
        "消息队列",
        "路由键",
    )
    row = {
        "根方法": "travel.service.TravelServiceImpl.getTickets",
        "图谱版本": "v1",
        "层级": 1,
        "链路类型": "rest",
        "源服务": "ts-travel-service",
        "源API路径": "/api/v1/travelservice/trip_detail",
        "源HTTP方法": "GET",
        "源方法": "travel.service.TravelServiceImpl.getTickets",
        "方法路径": ["travel.service.TravelServiceImpl.getTickets"],
        "下游API路径": "/api/v1/trainservice/trains",
        "目标服务": "ts-train-service",
        "目标API路径": "/api/v1/trainservice/trains",
        "目标HTTP方法": "GET",
        "目标方法": "train.controller.TrainController.query",
        "消息交换机": None,
        "消息队列": None,
        "路由键": None,
    }
    formatted = JsonResultFormatter().format(
        question="查询 getTickets 的完整调用链",
        sub_queries=(
            SubQueryResponse(
                "查询 getTickets 的完整调用链",
                "MATCH ... RETURN ...",
                QueryResult(columns, (row,)),
            ),
        ),
    )

    payload = json.loads(formatted)

    assert payload["decomposed"] is False
    assert payload["sub_queries"][0]["columns"] == list(columns)
    assert payload["sub_queries"][0]["rows"] == [row]
