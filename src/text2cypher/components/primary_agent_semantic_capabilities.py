"""加载 Primary Agent 可使用的抽象检索能力。"""

from __future__ import annotations

from functools import cache
from importlib.resources import files

from text2cypher.domain.errors import PrimaryAgentSemanticCapabilitiesError

_RESOURCE_PACKAGE = "text2cypher.resources"
_RESOURCE_NAME = "primary_agent_semantic_capabilities.md"


@cache
def load_primary_agent_semantic_capabilities() -> str:
    """读取并缓存不包含物理 Schema 的能力说明。"""

    try:
        content = files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME).read_text(
            encoding="utf-8"
        )
    except (OSError, TypeError) as error:
        raise PrimaryAgentSemanticCapabilitiesError(
            "无法读取 Primary Agent 语义能力"
        ) from error

    normalized = content.strip()
    if not normalized:
        raise PrimaryAgentSemanticCapabilitiesError(
            "Primary Agent 语义能力不能为空"
        )
    return normalized
