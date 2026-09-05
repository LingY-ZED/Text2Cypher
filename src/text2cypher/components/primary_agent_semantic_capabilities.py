"""兼容导出：Planner 能力视图已迁移至 :mod:`text2cypher.skills`。"""

from text2cypher.skills.registry import load_primary_agent_semantic_capabilities

__all__ = ["load_primary_agent_semantic_capabilities"]
