"""Graph Query Skills 的稳定元数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from text2cypher.domain.query_shapes import QueryShape
from text2cypher.skills.graph_profile import BusinessRuleModule


class SkillId(StrEnum):
    """当前代码图谱能力包的稳定标识。"""

    CODE_STRUCTURE = "code_structure"
    CALL_ANALYSIS = "call_analysis"
    API_ANALYSIS = "api_analysis"
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    CHANGE_IMPACT = "change_impact"


@dataclass(frozen=True, slots=True)
class GraphSkillDefinition:
    """一个只读、可审计的领域 Skill 定义。"""

    id: SkillId
    version: str
    description: str
    query_shapes: tuple[QueryShape, ...]
    graph_rule_modules: tuple[BusinessRuleModule, ...]
