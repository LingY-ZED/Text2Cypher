"""组件层中基于实时 Schema 构造模型提示词的实现。"""

from __future__ import annotations

from dataclasses import dataclass

from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.components.schema_serializer import SchemaSerializer
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    GraphSchema,
    RelationshipPattern,
)


@dataclass(frozen=True, slots=True)
class _ModelingConstraint:
    text: str
    required_node_properties: frozenset[str] = frozenset()
    required_relationship_properties: frozenset[str] = frozenset()
    required_patterns: frozenset[RelationshipPattern] = frozenset()
    question_terms: frozenset[str] = frozenset()
    excluded_question_terms: frozenset[str] = frozenset()


class DefaultPromptBuilder:
    """构造动态 Schema 约束及可选 Few-shot 示例提示词。"""

    system_instruction = (
        "你是 Neo4j Cypher 专家。\n"
        "只能使用提供的图谱 Schema。\n"
        "必须严格遵守关系模式中给出的关系方向。\n"
        "多跳路径只能首尾连接已给出的关系模式，且每一跳都不得反转。\n"
        "不得使用 Schema 中不存在的节点标签、关系类型或属性。\n"
        "引用节点或关系属性前，必须在关系模式中为对应元素绑定变量。\n"
        "不得生成写入、管理或过程调用。\n"
        "只能返回一条只读 Cypher，不输出解释文字。"
    )
    few_shot_system_instruction = (
        "参考示例不能覆盖当前图谱 Schema。\n"
        "不得复制参考示例中的实体值，必须使用当前问题中的实体值。\n"
        "参考示例的关系方向若与当前关系模式冲突，必须忽略该示例。\n"
        "参考示例只用于学习单一查询结构；不得拼接多个示例的关系模式，"
        "也不得为了验证结果增加当前问题未要求的关系。"
    )
    modeling_constraints = (
        _ModelingConstraint(
            text=(
                "当用户用 `Class.method` 形式提供不含包路径的方法标识时，"
                "应使用 `方法名 = method`，并优先通过关系模式连接类节点后按"
                " `简名 = Class` 定位；也可使用 `所属类名 ENDS WITH '.Class'`。"
                "不得把短类名直接作为 `所属类名` 或 `全限定名` 的精确值。"
            ),
            required_node_properties=frozenset(
                {"方法名", "所属类名", "全限定名", "简名"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "属性 `API类型` 的值 `上游API` 表示对外提供的 API，"
                "值 `下游API` 表示访问下游目标的 API。"
            ),
            required_node_properties=frozenset({"API类型"}),
        ),
        _ModelingConstraint(
            text=(
                "属性 `目标微服务` 表示业务调用目标；本地层级或归属位置"
                "不能替代该目标语义。"
            ),
            required_node_properties=frozenset({"目标微服务"}),
        ),
        _ModelingConstraint(
            text=(
                "关系属性 `调用类型` 的值 `远程调用` 表示代码方法直接访问"
                "下游 API；值 `跨服务调用` 只适用于两个 API 端点之间的调用。"
                "方法到下游 API 的关系不得使用 `跨服务调用`。"
            ),
            required_node_properties=frozenset(
                {"方法名", "API类型", "目标微服务"}
            ),
            required_relationship_properties=frozenset({"调用类型"}),
        ),
        _ModelingConstraint(
            text=(
                "REST 跨服务调用由关系属性 `调用类型` 的值 `跨服务调用` 标识；"
                "调用方服务应从源 API 的真实归属路径获取，业务目标服务应优先"
                "使用源 API 的 `目标微服务` 属性，不得用目标 API 的本地归属位置"
                "替代；服务间统计应排除调用方与目标服务相同的结果，并使用"
                "DISTINCT 关系计数避免归属路径扇出导致重复计数。"
            ),
            required_node_properties=frozenset(
                {"API类型", "目标微服务", "服务名称"}
            ),
            required_relationship_properties=frozenset({"调用类型"}),
        ),
        _ModelingConstraint(
            text=(
                "服务间 MQ 依赖由关系属性 `消息流类型` 的值 "
                "`服务间消息依赖` 标识；应选择两端节点类型都提供 `服务名称` "
                "属性的关系模式，并绑定关系变量后读取其属性。"
            ),
            required_node_properties=frozenset({"服务名称"}),
            required_relationship_properties=frozenset({"消息流类型"}),
        ),
        _ModelingConstraint(
            text=(
                "MQ 关系方向必须按当前 Schema 保持为：发布方法经 `发布` 指向"
                "消息交换机，消息交换机经 `路由` 指向消息队列，消息队列再经"
                "`消费` 指向消费方法。不得把消费关系写成方法指向队列。"
            ),
            required_node_properties=frozenset(
                {"全限定名", "交换机名称", "队列名称"}
            ),
            required_relationship_properties=frozenset({"消息流类型"}),
            required_patterns=frozenset(
                {
                    RelationshipPattern(("方法",), "消息流", ("消息交换机",)),
                    RelationshipPattern(
                        ("消息交换机",), "消息流", ("消息队列",)
                    ),
                    RelationshipPattern(("消息队列",), "消息流", ("方法",)),
                }
            ),
            question_terms=frozenset(
                {"mq", "消息", "队列", "交换机", "发布", "消费"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "查询完整 MQ 消息链及其两端服务时，发送服务必须从发布方法经"
                "`归属于` 连接到类再到微服务；接收服务必须从消费方法沿同样的"
                "归属路径获取。不得从共享交换机、队列或其所有者推断发送方或"
                "接收方，且发布、路由、消费必须留在同一条 MATCH 路径中。"
            ),
            required_node_properties=frozenset({"服务名称", "全限定名"}),
            required_relationship_properties=frozenset({"消息流类型"}),
            required_patterns=frozenset(
                {
                    RelationshipPattern(("方法",), "归属于", ("类",)),
                    RelationshipPattern(("类",), "归属于", ("微服务",)),
                    RelationshipPattern(("方法",), "消息流", ("消息交换机",)),
                    RelationshipPattern(
                        ("消息交换机",), "消息流", ("消息队列",)
                    ),
                    RelationshipPattern(("消息队列",), "消息流", ("方法",)),
                }
            ),
            question_terms=frozenset(
                {"完整", "链路", "路径", "发送服务", "接收服务", "发送方", "接收方"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "当问题绑定微服务并查询该服务的公开 API 或其他服务级资源时，"
                "必须从资源自身沿完整 `归属于` 路径回到该微服务；不得把资源"
                "强制绑定到题目中另行提到的实现类、方法或接口。若同一问题还列出"
                "某实现类的方法，资源分支必须使用另一组资源方法和资源类变量，"
                "沿资源 → 资源方法 → 资源类 → 已绑定微服务的完整路径匹配；不得"
                "复用实现类的方法变量来限制服务级资源。对应结构应为独立的"
                " `(resource)-[:归属于]->(:方法)-[:归属于]->(:类)"
                "-[:归属于]->(service)` 分支。返回公开 API 时将接口路径和请求方式"
                "分别投影为 `接口路径` 和 `请求方式`，不要给它们添加上下文前缀。"
            ),
            required_node_properties=frozenset({"API类型", "服务名称"}),
            required_patterns=frozenset(
                {
                    RelationshipPattern(("API端点",), "归属于", ("方法",)),
                    RelationshipPattern(("方法",), "归属于", ("类",)),
                    RelationshipPattern(("类",), "归属于", ("微服务",)),
                }
            ),
            question_terms=frozenset(
                {"公开 api", "公开api", "对外 api", "对外api", "暴露", "服务级资源"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "当问题要求先通过服务间消息依赖确定发送服务，再查询每个发送服务"
                "的 REST 下游时，必须在同一查询中保留发送服务节点，再从该服务"
                "反向沿归属路径找到类和方法，并由方法经 `远程调用` 到 `下游API`；"
                "下游服务取该 API 的 `目标微服务`。不得改用 API 之间的跨服务调用，"
                "也不得丢失发送服务与下游服务的逐行配对；最终返回完整投影的"
                " `DISTINCT`。"
            ),
            required_node_properties=frozenset(
                {"服务名称", "API类型", "目标微服务"}
            ),
            required_relationship_properties=frozenset(
                {"消息流类型", "调用类型"}
            ),
            required_patterns=frozenset(
                {
                    RelationshipPattern(("微服务",), "消息流", ("微服务",)),
                    RelationshipPattern(("方法",), "归属于", ("类",)),
                    RelationshipPattern(("类",), "归属于", ("微服务",)),
                    RelationshipPattern(("方法",), "调用", ("API端点",)),
                }
            ),
            question_terms=frozenset(
                {"每个发送服务", "发送服务的 rest", "消息发送方的 rest"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "查询某服务的接口实现类实体时，应匹配实现类作为 `接口实现` 关系"
                "起点，并让该实现类直接 `归属于` 已绑定服务："
                " `(implementation:类)-[:接口实现]->(:类), "
                "(implementation)-[:归属于]->(service)`；返回实现类标识，不得"
                "返回被实现的接口，也不得把实体列表改成计数。"
            ),
            required_node_properties=frozenset({"服务名称", "全限定名"}),
            required_patterns=frozenset(
                {
                    RelationshipPattern(("类",), "接口实现", ("类",)),
                    RelationshipPattern(("类",), "归属于", ("微服务",)),
                }
            ),
            question_terms=frozenset({"接口实现类", "实现类实体"}),
            excluded_question_terms=frozenset({"统计", "计数", "数量"}),
        ),
        _ModelingConstraint(
            text=(
                "当问题把远程目标称为系统边界外的应用时，应读取下游 API 的"
                " `目标微服务`，并投影为 `外部应用`；不得把本地归属位置作为目标，"
                "也不要改用含义更弱的别名。"
            ),
            required_node_properties=frozenset({"API类型", "目标微服务"}),
            question_terms=frozenset({"系统边界外", "边界外的应用"}),
        ),
        _ModelingConstraint(
            text=(
                "方法变更影响分析必须区分三种方向：全部上游调用方法使用反向"
                "调用链 `[:调用*1..5]->(changed)`；可达入口 API 使用反向"
                "`[:调用*0..5]->(changed)` 后由入口方法连接上游 API；直接远程"
                "下游必须以变更方法为调用方并使用 `调用类型 = '远程调用'`。"
            ),
            required_node_properties=frozenset({"方法名", "API类型", "目标微服务"}),
            required_relationship_properties=frozenset({"调用类型"}),
            required_patterns=frozenset(
                {
                    RelationshipPattern(("方法",), "调用", ("方法",)),
                    RelationshipPattern(("方法",), "调用", ("API端点",)),
                }
            ),
            question_terms=frozenset({"修改", "变更", "影响", "回归", "波及"}),
        ),
        _ModelingConstraint(
            text=(
                "非聚合的实体、路径和服务依赖查询应在最终完整投影上使用"
                " `RETURN DISTINCT` 去重；上游 `WITH DISTINCT` 不能替代最终"
                "去重。只有问题明确要求统计、计数、数量或分布时才使用聚合。"
            ),
            required_node_properties=frozenset({"服务名称"}),
            question_terms=frozenset(
                {"列出", "查询", "哪些", "谁", "是什么", "返回", "汇总", "依赖"}
            ),
            excluded_question_terms=frozenset(
                {"统计", "计数", "数量", "分布", "分桶", "各桶"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "返回服务名称时只能使用 Schema 中实际提供的 `服务名称` 属性，"
                "不得翻译或杜撰属性名。"
            ),
            required_node_properties=frozenset({"服务名称"}),
        ),
    )

    def __init__(
        self,
        schema_graph_builder: SchemaGraphBuilder | None = None,
        schema_serializer: SchemaSerializer | None = None,
    ) -> None:
        self._schema_graph_builder = schema_graph_builder or SchemaGraphBuilder()
        self._schema_serializer = schema_serializer or SchemaSerializer()

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("问题不能为空")

        schema_graph = self._schema_graph_builder.build(schema)
        serialized_schema = self._schema_serializer.serialize(schema, schema_graph)
        applicable_constraints = self._render_applicable_constraints(
            schema,
            normalized_question,
        )
        user_sections = [
            "图谱 Schema：",
            serialized_schema,
        ]
        if applicable_constraints:
            user_sections.extend(
                [
                    "可适用的业务语义：",
                    applicable_constraints,
                ]
            )
        if examples:
            user_sections.append(self._render_examples(examples))
        user_sections.extend(
            [
                "用户问题：",
                normalized_question,
                "只输出 Cypher：",
            ]
        )

        return ChatPrompt(
            system=self._system_instruction(examples),
            user="\n\n".join(user_sections),
        )

    @classmethod
    def _system_instruction(
        cls,
        examples: tuple[FewShotExample, ...],
    ) -> str:
        if not examples:
            return cls.system_instruction
        return f"{cls.system_instruction}\n{cls.few_shot_system_instruction}"

    @staticmethod
    def _render_examples(examples: tuple[FewShotExample, ...]) -> str:
        blocks = [
            "参考示例：",
            "以下示例只用于学习查询结构。\n"
            "必须使用当前问题中的实体值；\n"
            "若示例与当前 Schema 冲突，以当前 Schema 和关系方向为准。",
        ]
        blocks.extend(
            f"示例 {index}：\n问题：{example.question}\n"
            f"Cypher：\n{example.cypher}"
            for index, example in enumerate(examples, start=1)
        )
        return "\n\n".join(blocks)

    @classmethod
    def _render_applicable_constraints(
        cls,
        schema: GraphSchema,
        question: str,
    ) -> str:
        available_node_properties = {
            property_schema.name
            for node in schema.nodes
            for property_schema in node.properties
        }
        available_relationship_properties = {
            property_schema.name
            for relationship in schema.relationships
            for property_schema in relationship.properties
        }
        available_patterns = set(schema.patterns)
        normalized_question = question.casefold()
        return "\n".join(
            f"- {constraint.text}"
            for constraint in cls.modeling_constraints
            if (
                constraint.required_node_properties
                <= available_node_properties
                and constraint.required_relationship_properties
                <= available_relationship_properties
                and constraint.required_patterns <= available_patterns
                and (
                    not constraint.question_terms
                    or any(
                        term.casefold() in normalized_question
                        for term in constraint.question_terms
                    )
                )
                and not any(
                    term.casefold() in normalized_question
                    for term in constraint.excluded_question_terms
                )
            )
        )
