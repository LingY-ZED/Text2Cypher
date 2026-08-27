"""加载可人工维护的代码知识图谱业务语义。"""

from __future__ import annotations

from functools import cache
from importlib.resources import files

from text2cypher.domain.errors import CodeGraphBusinessRulesError

_RESOURCE_PACKAGE = "text2cypher.resources"
_RESOURCE_NAME = "code_graph_business_rules.md"


@cache
def load_code_graph_business_rules() -> str:
    """读取并缓存完整业务规则；资源异常属于启动配置错误。"""

    try:
        content = files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME).read_text(
            encoding="utf-8"
        )
    except (OSError, TypeError) as error:
        raise CodeGraphBusinessRulesError("无法读取代码图谱业务规则") from error

    normalized = content.strip()
    if not normalized:
        raise CodeGraphBusinessRulesError("代码图谱业务规则不能为空")
    return normalized
