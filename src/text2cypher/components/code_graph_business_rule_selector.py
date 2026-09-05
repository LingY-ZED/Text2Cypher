"""兼容导出：图谱规则选择已迁移至 :mod:`text2cypher.skills`。"""

from text2cypher.skills.policies import (
    BusinessRulePromptStage,
    GraphQuerySkillPolicy,
)

CodeGraphBusinessRuleSelector = GraphQuerySkillPolicy

__all__ = [
    "BusinessRulePromptStage",
    "CodeGraphBusinessRuleSelector",
]
