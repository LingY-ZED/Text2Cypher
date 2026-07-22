"""组件层中基于实时 Schema 构造模型提示词的实现。"""

from __future__ import annotations

from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import ChatPrompt, GraphSchema, PropertySchema


class DefaultPromptBuilder:
    """构造不含 Few-shot 选择的最小 Schema 约束提示词。"""

    system_instruction = (
        "你是 Neo4j Cypher 专家。根据提供的图谱 Schema 和用户问题生成一条只读 "
        "Cypher 查询。只能使用 Schema 中出现的节点标签、关系类型和属性名。"
        "不得生成写入、管理、过程调用或解释性文字。响应只能包含 Cypher。"
    )
    modeling_constraints = (
        "代码知识图谱建模约束：归属于关系方向为子节点到父节点。从 API端点、方法或类"
        "定位所属微服务时，必须使用 [:归属于*1..3]，不能使用单跳归属于关系替代。"
        "调用关系方向为调用方到被调用方。方法查询下游服务时，必须找到 API类型为下游API"
        "的 API端点，并直接返回其目标微服务属性；此时 API端点的归属于路径表示本地"
        "调用位置，不能用于推断目标服务。"
        "REST 跨服务调用统计必须遵循此结构：先匹配"
        "(sourceApi:API端点)-[call:调用 {调用类型: '跨服务调用'}]->"
        "(targetApi:API端点)，再让两个 API端点各自通过 [:归属于*1..3] 映射到"
        "源和目标微服务，最后按两个服务名称和 count(call) 聚合。调用类型是关系属性，"
        "不能以方法节点过滤，也不能从方法节点开始构造这类统计。"
        "返回微服务名称时，必须使用服务名称属性，不得翻译或臆造英文 camelCase 属性。"
        "查询某服务自身提供的 API端点时，必须以 API类型为上游API过滤，并通过归属于"
        "多跳路径定位该服务。反向查询哪些服务调用某服务时，必须以 API端点的目标微服务"
        "属性定位被调服务，再反向定位调用方法所属微服务。服务间 MQ 查询必须匹配微服务"
        "之间直接的消息流关系，消息流类型必须为服务间消息依赖；不得引入方法、交换机或"
        "队列等中间节点，可返回该消息流关系的交换机名称和队列名称属性。"
    )

    def build(self, schema: GraphSchema, question: str) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("问题不能为空")

        return ChatPrompt(
            system=f"{self.system_instruction}\n\n{self.modeling_constraints}",
            user=(
                "图谱结构：\n"
                f"{self._render_schema(schema)}\n\n"
                "必须遵守的建模规则：\n"
                f"{self.modeling_constraints}\n\n"
                "用户问题：\n"
                f"{normalized_question}\n\n"
                "只输出 Cypher："
            ),
        )

    @staticmethod
    def _render_properties(properties: tuple[PropertySchema, ...]) -> str:
        if not properties:
            return "（未观察到属性）"
        values = []
        for property_schema in properties:
            type_text = " | ".join(property_schema.types) or "未知类型"
            required = "（必填）" if property_schema.mandatory else ""
            values.append(f"{property_schema.name}: {type_text}{required}")
        return ", ".join(values)

    @classmethod
    def _render_schema(cls, schema: GraphSchema) -> str:
        node_lines = [
            f"- {node.name} {{{cls._render_properties(node.properties)}}}"
            for node in schema.nodes
        ] or ["- （未观察到节点标签）"]
        relationship_lines = [
            (
                f"- {relationship.name} "
                f"{{{cls._render_properties(relationship.properties)}}}"
            )
            for relationship in schema.relationships
        ] or ["- （未观察到关系类型）"]
        return "\n".join(
            [
                "节点属性：",
                *node_lines,
                "关系属性：",
                *relationship_lines,
            ]
        )
