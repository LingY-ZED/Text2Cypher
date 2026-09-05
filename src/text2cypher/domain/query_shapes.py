"""Deterministic retrieval-shape policy used only by Few-shot routing."""

from __future__ import annotations

from enum import StrEnum


class QueryShape(StrEnum):
    """Stable business-level query shapes; these are not physical graph schema."""

    GENERAL = "general"
    UPSTREAM_REACHABILITY = "upstream_reachability"
    DIRECT_UPSTREAM = "direct_upstream"
    DIRECT_DOWNSTREAM_METHOD = "direct_downstream_method"
    ORDERED_METHOD_PATH = "ordered_method_path"
    REACHABLE_ENTRY_API = "reachable_entry_api"
    FULL_ENTRY_CHAIN = "full_entry_chain"
    FULL_DOWNSTREAM_CHAIN = "full_downstream_chain"
    DIRECT_REST_EGRESS = "direct_rest_egress"


def resolve_query_shape(question: str) -> QueryShape:
    """Derive the retrieval shape solely from explicit question wording."""

    compact = "".join(question.lower().split())
    direction_text = compact.replace("直接", "")
    has_upstream_chain = any(
        cue in compact
        for cue in (
            "上游调用链",
            "上游链",
        )
    )
    has_upstream = any(
        cue in compact
        for cue in (
            "上游方法",
            "上游调用方",
            "上游调用者",
            "上游影响范围",
            "直接上游",
            "谁调用",
        )
    )
    has_upstream = has_upstream or "哪些方法调用" in direction_text
    has_downstream = "下游" in compact
    has_entry_context = "入口" in compact
    has_from_entry = any(cue in compact for cue in ("从入口api", "从入口方法"))
    has_entry_chain = any(cue in compact for cue in ("入口链", "入口调用链"))
    has_full_call_chain = any(
        cue in compact for cue in ("完整调用链", "端到端调用链")
    )
    has_full_upstream_chain = any(
        cue in compact
        for cue in ("完整上游调用链", "完整的上游调用链", "完整上游链")
    )
    has_full_downstream_chain = any(
        cue in compact
        for cue in (
            "完整下游调用链",
            "完整的下游调用链",
            "完整下游跨服务调用链",
            "完整的下游跨服务调用链",
            "完整下游链",
        )
    )
    has_full_entry_chain = any(
        cue in compact for cue in ("完整入口调用链", "完整入口链", "端到端入口链")
    )
    has_ordered = any(
        cue in compact
        for cue in ("有序", "方法路径", "调用路径", "调用顺序", "按顺序")
    )
    has_direct = "直接" in compact
    has_api = "api" in compact or "接口" in compact
    has_method = "方法" in compact
    has_reachable_entry = any(
        cue in compact
        for cue in (
            "可达入口api",
            "可达的入口api",
            "哪些入口api可以到达",
            "哪些入口api能到达",
            "入口api可到达",
            "影响哪些入口api",
            "影响哪些入口",
        )
    )

    if has_full_downstream_chain or (has_full_call_chain and has_downstream):
        return QueryShape.FULL_DOWNSTREAM_CHAIN
    if has_direct and (has_upstream or has_upstream_chain):
        return QueryShape.DIRECT_UPSTREAM
    if (
        has_upstream_chain
        or has_full_upstream_chain
        or has_full_entry_chain
        or (has_full_call_chain and (has_upstream or has_entry_context))
        or has_from_entry
        or has_entry_chain
    ):
        return QueryShape.FULL_ENTRY_CHAIN
    if has_ordered:
        return QueryShape.ORDERED_METHOD_PATH
    if has_reachable_entry:
        return QueryShape.REACHABLE_ENTRY_API
    if has_upstream:
        return QueryShape.UPSTREAM_REACHABILITY
    if has_direct and has_downstream and has_api:
        return QueryShape.DIRECT_REST_EGRESS
    if (
        has_direct
        and has_method
        and not has_api
        and any(
            cue in compact
            for cue in (
                "直接调用哪些方法",
                "直接调用了哪些方法",
                "直接下游方法",
                "直接调用的下游方法",
            )
        )
    ):
        return QueryShape.DIRECT_DOWNSTREAM_METHOD
    return QueryShape.GENERAL
