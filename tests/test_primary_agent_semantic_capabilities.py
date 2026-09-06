from __future__ import annotations

from collections.abc import Generator

import pytest

from text2cypher.components.primary_agent_semantic_capabilities import (
    load_primary_agent_semantic_capabilities,
)
from text2cypher.domain.errors import PrimaryAgentSemanticCapabilitiesError
from text2cypher.skills import registry


@pytest.fixture(autouse=True)
def _clear_capabilities_cache() -> Generator[None]:
    load_primary_agent_semantic_capabilities.cache_clear()
    yield
    load_primary_agent_semantic_capabilities.cache_clear()


def test_loads_abstract_semantic_capabilities_from_package_resource() -> None:
    capabilities = load_primary_agent_semantic_capabilities()

    assert capabilities.startswith("# 代码知识图谱可检索业务能力")
    assert "API 入口" in capabilities
    assert "消息发布方" in capabilities
    assert "有序调用路径" in capabilities
    assert "不要按返回列、属性或关系端点机械拆分" in capabilities
    assert "分组维度与其聚合值" in capabilities
    assert "哪些方法调用 X" in capabilities
    assert "不得扩大或缩小原问题的范围" in capabilities
    assert "按每个对象给出度量" in capabilities
    assert "逐项列出原问题要求的结果集合" in capabilities
    assert "InsidePaymentServiceImpl.pay" in capabilities
    assert "ts-security-service 的公开 API 路径和 HTTP 方法" in capabilities
    assert "完整入口调用链、完整下游调用链、完整方法调用链、有序方法路径和完整消息路径" in (
        capabilities
    )
    assert "方法-[:服务于]->上游API" not in capabilities
    assert "MATCH" not in capabilities
    assert "Few-shot" not in capabilities


@pytest.mark.parametrize(
    ("content", "message"),
    (("   ", "不能为空"), (None, "无法读取")),
)
def test_rejects_empty_or_unreadable_semantic_capabilities(
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
            assert name == "primary_agent_semantic_capabilities.md"
            return _Resource()

    monkeypatch.setattr(
        registry,
        "files",
        lambda _: _Package(),
    )

    with pytest.raises(PrimaryAgentSemanticCapabilitiesError, match=message):
        load_primary_agent_semantic_capabilities()
