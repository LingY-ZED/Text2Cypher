from __future__ import annotations

from collections.abc import Generator

import pytest

from text2cypher.components import code_graph_business_rules
from text2cypher.components.code_graph_business_rules import (
    load_code_graph_business_rules,
)
from text2cypher.domain.errors import CodeGraphBusinessRulesError


@pytest.fixture(autouse=True)
def _clear_business_rules_cache() -> Generator[None]:
    load_code_graph_business_rules.cache_clear()
    yield
    load_code_graph_business_rules.cache_clear()


def test_loads_complete_business_rules_from_package_resource() -> None:
    rules = load_code_graph_business_rules()

    assert rules.startswith("# 代码知识图谱业务语义")
    assert "方法-[:服务于]->上游API" in rules
    assert "方法-[:下游调用]->下游API" in rules
    assert "调用深度 IN [0,1]" in rules
    assert "禁止把短类名当成 `类.全限定名`" in rules
    assert "禁止拿服务名称与 `方法.全限定名`" in rules
    assert "直接上游调用者的唯一方向是" in rules
    assert "可达入口 API 必须先绑定" in rules
    assert "链中下一节点" in rules
    assert "完整下游链必须用 `UNION` 分开" in rules
    assert "发布至.路由键 = 路由至.路由键" in rules
    assert "count(DISTINCT remote) AS 调用关系数" in rules
    assert "无论是否出现“分别”二字" in rules
    assert "逐行对应" in rules
    assert "不得使用旧标签 `API端点`" in rules
    assert "不得使用旧关系 `消息流`" in rules


@pytest.mark.parametrize(
    ("content", "message"),
    (("   ", "不能为空"), (None, "无法读取")),
)
def test_rejects_empty_or_unreadable_business_rules(
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

    class _Package:
        def joinpath(self, name: str) -> _Resource:
            assert name == "code_graph_business_rules.md"
            return _Resource()

    monkeypatch.setattr(code_graph_business_rules, "files", lambda _: _Package())

    with pytest.raises(CodeGraphBusinessRulesError, match=message):
        load_code_graph_business_rules()
