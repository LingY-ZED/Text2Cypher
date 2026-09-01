"""加载按场景拆分、可人工维护的代码知识图谱业务语义。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from functools import cache
from importlib.resources import files

from text2cypher.domain.errors import CodeGraphBusinessRulesError

_RESOURCE_PACKAGE = "text2cypher.resources"
_RESOURCE_DIRECTORY = "code_graph_business_rules"


class BusinessRuleModule(StrEnum):
    """稳定的代码知识图谱业务规则模块标识。"""

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
    """按全局稳定顺序去重并校验调用方提供的模块。"""

    requested: set[BusinessRuleModule] = set()
    for module in modules:
        if not isinstance(module, BusinessRuleModule):
            raise TypeError("业务规则模块必须是 BusinessRuleModule")
        requested.add(module)
    return tuple(module for module in BusinessRuleModule if module in requested)


@cache
def load_code_graph_business_rule_module(module: BusinessRuleModule) -> str:
    """读取并缓存一个业务规则模块；资源异常属于启动配置错误。"""

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
    """按固定顺序渲染去重后的业务规则模块集合。"""

    normalized_modules = _normalize_modules(modules)
    return "\n\n".join(
        load_code_graph_business_rule_module(module)
        for module in normalized_modules
    )


@cache
def load_code_graph_business_rules() -> str:
    """兼容旧调用方：读取并缓存全部业务规则模块。"""

    return load_code_graph_business_rule_modules(BusinessRuleModule)
