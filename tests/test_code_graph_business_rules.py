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
    assert "方法-[:调用]->方法" in rules
    assert "调用类型='远程调用'" in rules
    assert "消息流类型=发布" in rules
    assert "逐行对应" in rules


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
