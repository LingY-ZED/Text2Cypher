from __future__ import annotations

from collections.abc import Generator

import pytest

from text2cypher.components import code_graph_business_rules
from text2cypher.components.code_graph_business_rules import (
    BusinessRuleModule,
    load_code_graph_business_rule_module,
    load_code_graph_business_rule_modules,
    load_code_graph_business_rules,
)
from text2cypher.domain.errors import CodeGraphBusinessRulesError


@pytest.fixture(autouse=True)
def _clear_business_rules_cache() -> Generator[None]:
    load_code_graph_business_rules.cache_clear()
    load_code_graph_business_rule_module.cache_clear()
    yield
    load_code_graph_business_rules.cache_clear()
    load_code_graph_business_rule_module.cache_clear()


def test_loads_complete_business_rules_from_ordered_package_modules() -> None:
    rules = load_code_graph_business_rules()

    assert rules.startswith("# 代码知识图谱业务语义：核心约束")
    assert tuple(BusinessRuleModule) == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.METHOD_CALL,
        BusinessRuleModule.ENTRY_API,
        BusinessRuleModule.REST,
        BusinessRuleModule.ORDERED_PATH,
        BusinessRuleModule.MQ,
        BusinessRuleModule.AGGREGATION,
        BusinessRuleModule.DECOMPOSITION,
    )
    assert "方法-[:服务于]->上游API" in rules
    assert "方法-[:下游调用]->下游API" in rules
    assert "不得再用 `调用深度` 判断是否直接" in rules
    assert "length(path) AS 调用距离" in rules
    assert "禁止把短类名当成 `类.全限定名`" in rules
    assert "禁止拿服务名称与 `方法.全限定名`" in rules
    assert "直接上游调用者的唯一方向是" in rules
    assert "可达入口 API 必须先绑定" in rules
    assert "`调用*0..` 最短路径" in rules
    assert "链中下一节点" in rules
    assert "`链中下一节点*0..` 同时覆盖" in rules
    assert "发布至.路由键 = 路由至.路由键" in rules
    assert "count(DISTINCT remote) AS 调用关系数" in rules
    assert "无论是否出现“分别”二字" in rules
    assert "逐行对应" in rules
    assert "不得使用旧标签 `API端点`" in rules
    assert "不得使用旧关系 `消息流`" in rules


def test_loads_single_module_and_normalizes_module_collections() -> None:
    core = load_code_graph_business_rule_module(BusinessRuleModule.CORE)
    selected = load_code_graph_business_rule_modules(
        (
            BusinessRuleModule.REST,
            BusinessRuleModule.CORE,
            BusinessRuleModule.REST,
        )
    )

    assert core.startswith("# 代码知识图谱业务语义：核心约束")
    assert selected.startswith(core)
    assert selected.count("# 代码知识图谱业务语义：REST 出口与映射") == 1
    assert "方法-[:下游调用]->下游API" in selected
    assert "消息交换机" not in selected


@pytest.mark.parametrize("invalid_module", ("core", object()))
def test_rejects_non_module_identifiers(invalid_module: object) -> None:
    with pytest.raises(TypeError, match="BusinessRuleModule"):
        load_code_graph_business_rule_module(invalid_module)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("content", "message"),
    (("   ", "不能为空"), (None, "无法读取")),
)
def test_rejects_empty_or_unreadable_business_rule_module(
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    message: str,
) -> None:
    class _Resource:
        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            if content is None:
                raise OSError("unreadable")
            return content

    class _Directory:
        def joinpath(self, name: str) -> _Resource:
            assert name == "core.md"
            return _Resource()

    class _Package:
        def joinpath(self, name: str) -> _Directory:
            assert name == "code_graph_business_rules"
            return _Directory()

    monkeypatch.setattr(code_graph_business_rules, "files", lambda _: _Package())

    with pytest.raises(CodeGraphBusinessRulesError, match=message):
        load_code_graph_business_rule_module(BusinessRuleModule.CORE)
