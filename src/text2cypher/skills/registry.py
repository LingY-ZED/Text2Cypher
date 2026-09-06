"""Graph Query Skill 注册表及规划知识视图。"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from importlib.resources import files

from text2cypher.domain.errors import PrimaryAgentSemanticCapabilitiesError
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.skills.graph_profile import BusinessRuleModule
from text2cypher.skills.models import GraphSkillDefinition, SkillId

_RESOURCE_PACKAGE = "text2cypher.resources"
_RESOURCE_NAME = "primary_agent_semantic_capabilities.md"

_GRAPH_QUERY_SKILLS = (
    GraphSkillDefinition(
        id=SkillId.CODE_STRUCTURE,
        version="1",
        description="类、方法、接口实现、归属与属性查询。",
        query_shapes=(QueryShape.GENERAL,),
        graph_rule_modules=(
            BusinessRuleModule.CORE,
            BusinessRuleModule.ANCHOR_OWNERSHIP,
        ),
    ),
    GraphSkillDefinition(
        id=SkillId.CALL_ANALYSIS,
        version="2",
        description="直接调用、可达调用、有序路径和完整调用链。",
        query_shapes=(
            QueryShape.UPSTREAM_REACHABILITY,
            QueryShape.DIRECT_UPSTREAM,
            QueryShape.DIRECT_DOWNSTREAM_METHOD,
            QueryShape.ORDERED_METHOD_PATH,
            QueryShape.FULL_ENTRY_CHAIN,
            QueryShape.FULL_DOWNSTREAM_CHAIN,
            QueryShape.FULL_METHOD_CALL_CHAIN,
        ),
        graph_rule_modules=(
            BusinessRuleModule.METHOD_CALL,
            BusinessRuleModule.ORDERED_PATH,
        ),
    ),
    GraphSkillDefinition(
        id=SkillId.API_ANALYSIS,
        version="2",
        description="服务 API、方法入口、API 契约和入口路径。",
        query_shapes=(
            QueryShape.REACHABLE_ENTRY_API,
            QueryShape.FULL_ENTRY_CHAIN,
            QueryShape.FULL_DOWNSTREAM_CHAIN,
            QueryShape.FULL_METHOD_CALL_CHAIN,
        ),
        graph_rule_modules=(BusinessRuleModule.ENTRY_API,),
    ),
    GraphSkillDefinition(
        id=SkillId.DEPENDENCY_ANALYSIS,
        version="2",
        description="REST、MQ、服务依赖和关联聚合。",
        query_shapes=(
            QueryShape.DIRECT_REST_EGRESS,
            QueryShape.FULL_DOWNSTREAM_CHAIN,
            QueryShape.FULL_METHOD_CALL_CHAIN,
        ),
        graph_rule_modules=(
            BusinessRuleModule.REST,
            BusinessRuleModule.MQ,
            BusinessRuleModule.AGGREGATION,
        ),
    ),
    GraphSkillDefinition(
        id=SkillId.CHANGE_IMPACT,
        version="1",
        description="变更分析的上游、入口和依赖拆分视图。",
        query_shapes=(QueryShape.GENERAL,),
        graph_rule_modules=(BusinessRuleModule.DECOMPOSITION,),
    ),
)


def graph_query_skills() -> tuple[GraphSkillDefinition, ...]:
    """按稳定 ID 顺序返回当前受支持的 Skill 清单。"""

    return _GRAPH_QUERY_SKILLS


def skills_for_modules(
    modules: Iterable[BusinessRuleModule],
) -> tuple[GraphSkillDefinition, ...]:
    """将已确定的 Profile 模块映射为稳定的 Skill 元数据。"""

    selected = set(modules)
    if not all(isinstance(module, BusinessRuleModule) for module in selected):
        raise TypeError("业务规则模块必须是 BusinessRuleModule")
    return tuple(
        skill
        for skill in _GRAPH_QUERY_SKILLS
        if selected.intersection(skill.graph_rule_modules)
    )


@cache
def load_primary_agent_semantic_capabilities() -> str:
    """读取 Planner 使用的抽象查询能力视图，不包含物理 Schema。"""

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
