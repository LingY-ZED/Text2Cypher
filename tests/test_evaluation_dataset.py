"""Tests for the frozen Week 4 evaluation dataset."""

from __future__ import annotations

from collections import Counter

from evaluation.dataset import load_cases
from evaluation.models import ComparisonMode, Difficulty


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
