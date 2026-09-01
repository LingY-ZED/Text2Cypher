from __future__ import annotations

from collections.abc import Generator

import pytest

from text2cypher.components import primary_agent_semantic_capabilities
from text2cypher.components.primary_agent_semantic_capabilities import (
    load_primary_agent_semantic_capabilities,
)
from text2cypher.domain.errors import PrimaryAgentSemanticCapabilitiesError


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
    assert "方法-[:服务于]->上游API" not in capabilities
    assert "MATCH" not in capabilities


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
        primary_agent_semantic_capabilities,
        "files",
        lambda _: _Package(),
    )

    with pytest.raises(PrimaryAgentSemanticCapabilitiesError, match=message):
        load_primary_agent_semantic_capabilities()
