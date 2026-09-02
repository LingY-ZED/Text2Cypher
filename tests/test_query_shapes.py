from __future__ import annotations

import pytest

from text2cypher.domain.query_shapes import QueryShape, resolve_query_shape


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("查询getTickets的上游调用链", QueryShape.UPSTREAM_REACHABILITY),
        ("哪些方法直接调用 getTickets", QueryShape.DIRECT_UPSTREAM),
        (
            "RebookServiceImpl.rebook 直接调用了哪些方法？",
            QueryShape.DIRECT_DOWNSTREAM_METHOD,
        ),
        ("查询 getTickets 的有序方法路径", QueryShape.ORDERED_METHOD_PATH),
        (
            "哪些入口 API 可以到达 ConsignServiceImpl.updateConsignRecord？",
            QueryShape.REACHABLE_ENTRY_API,
        ),
        (
            "查询 getTickets 从入口 API 开始的完整上游调用链",
            QueryShape.FULL_ENTRY_CHAIN,
        ),
        (
            "查询 getTickets 的完整下游调用链",
            QueryShape.FULL_DOWNSTREAM_CHAIN,
        ),
        (
            "查询 InsidePaymentServiceImpl.pay 的完整下游跨服务调用链，"
            "返回有序方法路径、下游 API、目标服务、目标上游 API 和入口方法。",
            QueryShape.FULL_DOWNSTREAM_CHAIN,
        ),
        (
            "InsidePaymentServiceImpl.pay 直接调用哪些下游 API 和目标服务？"
            "同时返回目标方法。",
            QueryShape.DIRECT_REST_EGRESS,
        ),
        (
            "列出服务远程调用的下游 API，并保持完整配对。",
            QueryShape.GENERAL,
        ),
        (
            "列出 ts-admin-basic-info-service 远程调用的下游 API，并保持调用方法、"
            "目标服务、目标上游 API 和目标入口方法的完整配对。",
            QueryShape.GENERAL,
        ),
        (
            "查询到达 ts-order-other-service 的 REST 上游跨服务链，保持完整对应。",
            QueryShape.GENERAL,
        ),
        (
            "查询到达 ts-security-service 的调用服务、调用方法、下游 API、"
            "目标上游 API 和目标入口方法，保持完整对应关系。",
            QueryShape.GENERAL,
        ),
    ],
)
def test_resolve_query_shape_uses_only_explicit_question_language(
    question: str,
    expected: QueryShape,
) -> None:
    assert resolve_query_shape(question) is expected


def test_direct_rest_shape_does_not_require_extra_return_field_cues() -> None:
    assert (
        resolve_query_shape("InsidePaymentServiceImpl.pay 直接调用哪些下游 API？")
        is QueryShape.DIRECT_REST_EGRESS
    )


def test_plain_upstream_call_chain_is_reachability_not_full_entry_chain() -> None:
    assert (
        resolve_query_shape("FoodServiceImpl.getAllFood 的上游调用链是什么？")
        is QueryShape.UPSTREAM_REACHABILITY
    )
