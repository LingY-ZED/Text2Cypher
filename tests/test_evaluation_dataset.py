"""Tests for the frozen Week 4 evaluation dataset."""

from __future__ import annotations

from collections import Counter

from evaluation.dataset import load_cases
from evaluation.models import (
    ComparisonMode,
    DecompositionContract,
    Difficulty,
    ValueNormalizer,
)


def test_dataset_has_fixed_size_distribution_and_unique_questions() -> None:
    cases = load_cases()

    assert len(cases) == 30
    assert Counter(case.difficulty for case in cases) == {
        Difficulty.SIMPLE: 10,
        Difficulty.MEDIUM: 12,
        Difficulty.HARD: 8,
    }
    assert len({case.id for case in cases}) == 30
    assert len({case.question for case in cases}) == 30


def test_every_intent_has_a_readonly_nonempty_frozen_contract() -> None:
    cases = load_cases()
    modes: set[ComparisonMode] = set()

    for case in cases:
        for intent in case.intents:
            modes.add(intent.comparison_mode)
            assert intent.oracle_cypher.startswith(("MATCH", "OPTIONAL MATCH"))
            assert ";" not in intent.oracle_cypher
            assert intent.expected_columns
            assert intent.expected_snapshot
            assert set(intent.accepted_aliases) == set(intent.expected_columns)
            assert all(
                column in intent.accepted_aliases[column]
                for column in intent.expected_columns
            )

    assert modes == set(ComparisonMode)


def test_corrected_evaluation_contracts_are_explicit() -> None:
    cases = {case.id: case for case in load_cases()}

    config_owner = cases["config-api-owner"].intents[0]
    assert "所属微服务" in config_owner.accepted_aliases["服务名称"]

    method_counts = cases["admin-route-api-method-counts"].intents[0]
    assert method_counts.expected_columns == ("请求方式", "API数量")
    assert "接口数量" in method_counts.accepted_aliases["API数量"]

    email_consumers = cases["email-consumer-methods"].intents[0]
    assert "消费方法全限定名" in email_consumers.accepted_aliases["消费方法"]

    implementation_methods = next(
        intent
        for intent in cases["admin-route-implementation-slice"].intents
        if intent.id == "methods"
    )
    assert implementation_methods.value_normalizers["方法全限定名"] is (
        ValueNormalizer.QUALIFIED_NAME_TAIL
    )

    implementations = next(
        intent
        for intent in cases["admin-basic-architecture-aggregates"].intents
        if intent.id == "implementations"
    )
    assert "接口实现类全限定名" in implementations.accepted_aliases["实现类"]


def test_decomposition_contracts_and_local_aliases_are_explicit() -> None:
    cases = {case.id: case for case in load_cases()}

    for case_id in (
        "admin-route-post-contract",
        "preserve-rabbit-message-path",
        "notification-senders-rest-matrix",
        "food-delivery-full-message-chain",
    ):
        assert cases[case_id].decomposition_contract is (
            DecompositionContract.MUST_PRESERVE
        )
    for case_id in (
        "inside-payment-pay-impact",
        "consign-insert-impact",
        "notification-api-mq-consumers",
        "security-service-three-way",
        "admin-basic-architecture-aggregates",
    ):
        assert cases[case_id].decomposition_contract is (
            DecompositionContract.MUST_SPLIT
        )
    assert cases["admin-route-implementation-slice"].decomposition_contract is (
        DecompositionContract.ANY
    )

    matrix = cases["notification-senders-rest-matrix"].intents[0]
    assert "REST下游服务" in matrix.accepted_aliases["下游服务"]
    target_counts = cases["admin-basic-target-call-counts"].intents[0]
    assert "REST下游服务" not in target_counts.accepted_aliases["下游服务"]

    entry = next(
        intent
        for intent in cases["inside-payment-pay-impact"].intents
        if intent.id == "entry_apis"
    )
    assert "入口API路径" in entry.accepted_aliases["入口接口"]
    other_entry = cases["consign-update-entry-apis"].intents[0]
    assert "入口API路径" not in other_entry.accepted_aliases["入口接口"]

    targets = cases["rebook-service-rest-targets"].intents[0]
    assert "依赖微服务" in targets.accepted_aliases["下游服务"]
    other_targets = cases["inside-payment-pay-targets"].intents[0]
    assert "依赖微服务" not in other_targets.accepted_aliases["下游服务"]


def test_rest_mq_and_impact_oracles_follow_current_semantics() -> None:
    cases = {case.id: case for case in load_cases()}

    callers = cases["order-other-rest-callers"].intents[0]
    assert "远程调用" in callers.oracle_cypher
    assert "跨服务调用" not in callers.oracle_cypher
    assert {row["上游服务"] for row in callers.expected_snapshot} == {
        "ts-admin-order-service",
        "ts-cancel-service",
        "ts-execute-service",
        "ts-inside-payment-service",
        "ts-rebook-service",
        "ts-security-service",
    }

    impact = next(
        intent
        for intent in cases["consign-insert-impact"].intents
        if intent.id == "upstream_methods"
    )
    assert "[:调用*1..5]" in impact.oracle_cypher
    assert any(
        row["上游方法"] == "consign.controller.ConsignController.updateConsign"
        for row in impact.expected_snapshot
    )

    senders = next(
        intent
        for intent in cases["notification-api-mq-consumers"].intents
        if intent.id == "senders"
    )
    assert "sender.服务名称 <> receiver.服务名称" not in senders.oracle_cypher
    assert any(
        row["发送服务"] == "ts-notification-service"
        for row in senders.expected_snapshot
    )

    security_upstreams = next(
        intent
        for intent in cases["security-service-three-way"].intents
        if intent.id == "upstreams"
    )
    assert "远程调用" in security_upstreams.oracle_cypher
    assert "跨服务调用" not in security_upstreams.oracle_cypher


def test_questions_do_not_reuse_the_previous_comparison_set() -> None:
    previous_questions = {
        "系统中共有多少个微服务？",
        "查询 CancelServiceImpl 的 cancelOrder 方法的全限定名和方法签名。",
        "ts-auth-service 对外暴露哪些 API 端点及请求方式？",
        "/api/v1/auth 这个上游接口归属哪个微服务？",
        "CancelServiceImpl 的 cancelOrder 会直接调用哪些内部方法？",
        "RoutePlanServiceImpl 的 searchMinStopStations 会访问哪些下游微服务？",
        "哪些服务通过 REST 调用了 ts-route-service？",
        "修改 TravelPlanServiceImpl.getRestTicketNumber 会影响哪些入口 API？",
        "ts-preserve-service 与 ts-notification-service 之间通过哪些 MQ 通道通信？",
        "哪些方法会向 email 队列发布消息？",
        "按被调用服务汇总跨服务 REST 调用关系数量。",
        "统计 RoutePlanServiceImpl.searchMinStopStations 调用各下游服务的次数。",
        "修改 RoutePlanServiceImpl.searchMinStopStations 后，"
        "哪些上游方法与下游服务会受影响？",
        "综合查询 ts-preserve-service 的 REST 和 MQ 下游依赖。",
        "ts-auth-service 的 /api/v1/auth 上游接口请求体类型、"
        "响应类型和响应包装类型分别是什么？",
        "ts-auth-service 的 /api/v1/users/{userId} 上游接口有哪些请求参数？",
        "修改 FoodServiceImpl.getAllFood 后，"
        "哪些上游方法、入口 API 与下游服务会受影响？",
        "修改 RoutePlanServiceImpl.searchMinStopStations 后，"
        "哪些上游方法、入口 API 与下游服务会受影响？",
        "ts-food-service 对外暴露哪些 API、通过 REST 访问哪些下游服务，"
        "以及通过 MQ 依赖哪些下游服务？",
        "ts-preserve-other-service 的 API 端点、"
        "REST 下游服务和 MQ 下游服务分别是什么？",
        "RoutePlanServiceImpl.searchMinStopStations 将请求发往系统边界之外的哪些应用？",
        "FoodServiceImpl.getAllFood 会跨进程触达哪些应用？",
        "查看 ts-preserve-service 的同步远程依赖，忽略 MQ 消息链。",
        "日志里同时出现“MQ、队列、交换机”，"
        "但本次只查 CancelServiceImpl.cancelOrder 的直接代码调用。",
        "故障发生在 ts-train-food-service，沿依赖边回溯到源代码层，"
        "返回最接近的调用函数。",
        "从 ts-auth-service 的公开 HTTP 面向看，它有哪些可访问资源？",
        "对 RoutePlanServiceImpl.searchMinStopStations 的外部请求按目的应用做分桶，"
        "各桶有几条边？",
        "按被访问方整理系统内同步远程依赖边的数量分布。",
    }

    assert not ({case.question for case in load_cases()} & previous_questions)
