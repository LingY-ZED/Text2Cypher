"""兼容导出：代码图谱 Profile 已迁移至 :mod:`text2cypher.skills`。"""

from text2cypher.skills.graph_profile import (
    BusinessRuleModule,
    load_code_graph_business_rule_module,
    load_code_graph_business_rule_modules,
    load_code_graph_business_rules,
)

__all__ = [
    "BusinessRuleModule",
    "load_code_graph_business_rule_module",
    "load_code_graph_business_rule_modules",
    "load_code_graph_business_rules",
]
