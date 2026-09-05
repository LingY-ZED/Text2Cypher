"""当前代码知识图谱 Profile 的物理映射知识。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from functools import cache
from importlib.resources import files

from text2cypher.domain.errors import CodeGraphBusinessRulesError

_RESOURCE_PACKAGE = "text2cypher.resources"
_RESOURCE_DIRECTORY = "code_graph_business_rules"


class BusinessRuleModule(StrEnum):
    """`code-graph-v1` Profile 中稳定的物理语义片段。"""

    CORE = "core"
    ANCHOR_OWNERSHIP = "anchor_ownership"
    METHOD_CALL = "method_call"
    ENTRY_API = "entry_api"
    REST = "rest"
    ORDERED_PATH = "ordered_path"
    MQ = "mq"
    AGGREGATION = "aggregation"
    DECOMPOSITION = "decomposition"


def _normalize_modules(
    modules: Iterable[BusinessRuleModule],
) -> tuple[BusinessRuleModule, ...]:
    requested: set[BusinessRuleModule] = set()
    for module in modules:
        if not isinstance(module, BusinessRuleModule):
            raise TypeError("业务规则模块必须是 BusinessRuleModule")
        requested.add(module)
    return tuple(module for module in BusinessRuleModule if module in requested)


@cache
def load_code_graph_business_rule_module(module: BusinessRuleModule) -> str:
    """读取一个 Profile 物理映射片段。"""

    if not isinstance(module, BusinessRuleModule):
        raise TypeError("业务规则模块必须是 BusinessRuleModule")
    try:
        content = (
            files(_RESOURCE_PACKAGE)
            .joinpath(_RESOURCE_DIRECTORY)
            .joinpath(f"{module.value}.md")
            .read_text(encoding="utf-8")
        )
    except (OSError, TypeError) as error:
        raise CodeGraphBusinessRulesError(
            f"无法读取代码图谱业务规则模块：{module.value}"
        ) from error
    normalized = content.strip()
    if not normalized:
        raise CodeGraphBusinessRulesError(
            f"代码图谱业务规则模块不能为空：{module.value}"
        )
    return normalized


def load_code_graph_business_rule_modules(
    modules: Iterable[BusinessRuleModule],
) -> str:
    """按 Profile 的固定顺序渲染去重后的片段。"""

    return "\n\n".join(
        load_code_graph_business_rule_module(module)
        for module in _normalize_modules(modules)
    )


@cache
def load_code_graph_business_rules() -> str:
    """兼容读取完整 `code-graph-v1` Profile。"""

    return load_code_graph_business_rule_modules(BusinessRuleModule)
