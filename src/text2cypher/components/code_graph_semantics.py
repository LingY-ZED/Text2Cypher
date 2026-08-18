"""代码知识图谱问题特征识别与精简业务语义选择。"""

from __future__ import annotations

import re
from enum import StrEnum

from text2cypher.domain.models import GraphSchema, RelationshipPattern


class CodeGraphFeature(StrEnum):
    """会影响查询方向、深度或返回形状的问题特征。"""

    DIRECT = "direct"
    CHAIN = "chain"
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    ENTRY_API = "entry_api"
    REST = "rest"
    CROSS_SERVICE = "cross_service"
    MQ = "mq"
    AGGREGATE = "aggregate"


CALL_CHAIN_CORRELATION_RULE = (
    "完整调用链是一个逐行对应的意图：方法路径、入口或出口 API、服务必须留在"
    "同一子问题中；只有明确分别询问互不关联的对象时才可拆分。"
)

CALL_CHAIN_ROUTING_RULE = (
    "先区分直接方法、入口 API、完整上游链、完整下游链和服务依赖；完整链示例"
    "必须同时匹配方向和返回形状，不能仅凭‘调用、上游、下游’等共有词选择。"
)


class CodeGraphSemanticSelector:
    """结合问题特征和实时 Schema，选择至多五条可执行语义。"""

    max_rules = 5
    max_chars = 1000

    _class_method = re.compile(
        r"(?<![\w.])(?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*(?![\w.])"
    )
    _bare_camel_method = re.compile(
        r"(?<![\w.])[a-z][A-Za-z0-9_$]*[A-Z][\w$]*"
    )
    _service_name = re.compile(
        r"(?<![\w-])ts-[a-z0-9-]+-service(?![\w-])"
    )

    def detect(self, question: str) -> frozenset[CodeGraphFeature]:
        normalized = question.casefold()
        features: set[CodeGraphFeature] = set()

        if self._contains(normalized, "直接", "下一跳", "一步"):
            features.add(CodeGraphFeature.DIRECT)
        if self._contains(normalized, "调用链", "调用路径", "链路") or (
            "完整" in normalized
            and self._contains(normalized, "上游", "下游", "调用")
        ):
            features.add(CodeGraphFeature.CHAIN)
        if self._contains(
            normalized,
            "上游",
            "调用方",
            "调用者",
            "调用服务",
            "谁调用",
            "到达",
            "可达",
            "哪些微服务把",
        ):
            features.add(CodeGraphFeature.UPSTREAM)
        if self._contains(
            normalized,
            "下游",
            "被调用",
            "调用了",
            "访问了",
            "目标服务",
            "依赖哪些",
        ):
            features.add(CodeGraphFeature.DOWNSTREAM)
        if self._contains(
            normalized,
            "入口api",
            "入口 api",
            "入口接口",
            "上游api",
            "上游 api",
            "可到达",
            "到达该方法",
        ):
            features.add(CodeGraphFeature.ENTRY_API)
        if self._contains(
            normalized,
            "rest",
            "远程",
            "下游api",
            "下游 api",
            "上游api",
            "上游 api",
        ):
            features.add(CodeGraphFeature.REST)
        if "跨服务" in normalized:
            features.add(CodeGraphFeature.CROSS_SERVICE)
            features.add(CodeGraphFeature.REST)
        if self._contains(
            normalized,
            "mq",
            "消息",
            "队列",
            "交换机",
            "发布",
            "消费",
        ):
            features.add(CodeGraphFeature.MQ)
        if self._contains(
            normalized,
            "统计",
            "计数",
            "数量",
            "分布",
            "分桶",
            "各桶",
            "汇总",
        ):
            features.add(CodeGraphFeature.AGGREGATE)

        if CodeGraphFeature.CHAIN in features:
            if CodeGraphFeature.UPSTREAM in features:
                features.add(CodeGraphFeature.ENTRY_API)
                features.add(CodeGraphFeature.REST)
                features.add(CodeGraphFeature.CROSS_SERVICE)
            if CodeGraphFeature.DOWNSTREAM in features:
                features.add(CodeGraphFeature.REST)
                features.add(CodeGraphFeature.CROSS_SERVICE)
        return frozenset(features)

    def select(self, schema: GraphSchema, question: str) -> tuple[str, ...]:
        """返回与问题和 Schema 同时兼容的精简生成规则。"""

        features = self.detect(question)
        normalized = question.casefold()
        service_upstream_chain = self._is_service_upstream_chain(
            normalized,
            features,
        )
        service_outgoing_pair = self._is_service_outgoing_pair(normalized)
        rules: list[str] = []

        if self._method_anchor_relevant(question) and self._has_method_anchor(schema):
            rules.append(
                "方法锚点：`Class.method` 用 `方法名=method` 并经所属类的"
                "`简名=Class` 定位；只有方法名时匹配全部同名节点，并返回"
                "`目标方法`全限定名，不猜测唯一实现。"
            )

        if self._is_sender_rest_matrix(normalized) and self._has_sender_rest(schema):
            rules.append(
                "先由微服务间 `服务间消息依赖` 找到发送服务，再从每个发送服务"
                "反向沿类和方法归属路径，经方法的 `远程调用` 到 `下游API`；"
                "下游服务取 `目标微服务`，并在同一查询保留发送服务与下游服务"
                "逐行配对，最终完整投影使用 DISTINCT。"
            )
        elif CodeGraphFeature.MQ in features and self._has_mq_path(schema):
            rules.append(
                "MQ 只按发布方法-[:消息流 {消息流类型:'发布'}]->交换机"
                "-[:消息流 {消息流类型:'路由'}]->队列-[:消息流 "
                "{消息流类型:'消费'}]->消费方法；两端服务分别从对应方法的"
                "方法→类→微服务归属链取得，不与 REST 调用链混用。"
            )
        elif (
            self._has_method_calls(schema)
            and self._method_call_direction_relevant(question, features)
            and not service_upstream_chain
            and not service_outgoing_pair
        ):
            direction = (
                "`方法-[:调用]->方法` 表示调用者到被调用者；上游反向、下游正向。"
            )
            if (
                CodeGraphFeature.DIRECT in features
                and CodeGraphFeature.CHAIN not in features
            ):
                direction += "‘直接/下一跳’只匹配一跳。"
            else:
                direction += "‘调用链/链路/调用路径’使用有界路径，最多 5 跳。"
            rules.append(direction)

        if self._is_impact_question(normalized) and self._has_method_calls(schema):
            rules.append(
                "方法变更影响分三种方向：全部上游方法使用反向"
                "`[:调用*1..5]->(changed)`；入口 API 使用反向"
                "`[:调用*0..5]->(changed)` 后由入口方法连接上游 API；直接远程"
                "下游始终以变更方法为调用方。"
            )

        if service_upstream_chain and self._has_rest_path(schema):
            rules.append(
                "目标为服务的 REST 上游链：调用服务经自身类和调用方法归属链，"
                "由调用方法的 `远程调用` 指向 `下游API`，且"
                "`目标微服务` 等于目标服务；需扩展边界时再可选匹配该下游 API"
                "经 `跨服务调用` 到目标 `上游API` 和入口方法，所有字段保持逐行配对。"
            )
        elif (
            CodeGraphFeature.UPSTREAM in features
            and CodeGraphFeature.CHAIN in features
            and not service_outgoing_pair
            and self._has_entry_api(schema)
        ):
            rules.append(
                "完整上游链：入口方法沿 `[:调用*0..5]` 到目标方法，再由入口方法"
                "经 `调用类型='接口入口'` 到 `API类型='上游API'`；返回目标方法、"
                "入口 API 和 `nodes(path)` 的有序方法全限定名列表。API 间存在"
                "跨服务映射时可补充一个直接调用方服务边界，缺失时保留本地链。"
            )
        elif (
            CodeGraphFeature.ENTRY_API in features
            and not (
                CodeGraphFeature.DOWNSTREAM in features
                and CodeGraphFeature.CHAIN in features
            )
            and not service_outgoing_pair
            and self._has_entry_api(schema)
        ):
            rules.append(
                "入口 API：入口方法沿 `[:调用*0..5]` 反向可达目标方法，并经"
                "`调用类型='接口入口'` 指向 `API类型='上游API'`；关系类型和"
                "API 类型必须同时过滤。"
            )

        if service_outgoing_pair and self._has_rest_path(schema):
            rules.append(
                "固定服务的 REST 出口配对：从服务反向沿微服务←类←方法归属链"
                "取得调用方法，再经 `远程调用` 到 `下游API`；目标服务取"
                "`目标微服务`，并可选经一层 `跨服务调用` 取得目标 `上游API`"
                "和入口方法。下游与目标 API 必须逐行配对。"
            )
        elif (
            CodeGraphFeature.DOWNSTREAM in features
            and CodeGraphFeature.CHAIN in features
            and not service_upstream_chain
            and self._has_rest_path(schema)
        ):
            rules.append(
                "完整下游链：从目标方法沿 `[:调用*0..5]` 正向展开有序方法路径，"
                "由路径末端方法经 `调用类型='远程调用'` 到 `API类型='下游API'`；"
                "目标服务取下游 API.`目标微服务`。可选再经一个"
                "`跨服务调用`边界到目标上游 API 及入口方法；映射缺失时仍保留"
                "已确认的方法链和下游 API。"
            )
        elif (
            CodeGraphFeature.DOWNSTREAM in features
            and (
                CodeGraphFeature.REST in features
                or "服务" in normalized
                or "api" in normalized
            )
            and not service_upstream_chain
            and self._has_rest_path(schema)
        ):
            rules.append(
                "REST 出口：调用方法经 `调用类型='远程调用'` 指向"
                "`API类型='下游API'`，目标服务取该 API.`目标微服务`；调用方服务"
                "只能从调用方法→类→微服务归属链取得。关系类型和 API 类型必须"
                "同时过滤。"
            )

        if (
            CodeGraphFeature.CROSS_SERVICE in features
            and self._has_cross_service(schema)
        ):
            rules.append(
                "REST 服务边界只展开一层：下游 API 经 `调用类型='跨服务调用'`"
                "指向目标 `上游API`，该 API 再归属于目标入口方法；跨服务映射"
                "必须作为可选扩展，不能因映射不存在而过滤已确认结果。"
            )

        if self._is_service_resource_question(
            normalized
        ) and self._has_ownership(schema):
            rules.append(
                "查询已绑定服务的公开 API 等服务级资源时，从资源自身沿完整"
                "资源→方法→类→微服务归属路径回到该服务，不得复用题中其他"
                "实现类或方法变量限制资源。"
            )

        if self._is_interface_implementation_question(
            normalized
        ) and self._has_interface(schema):
            rules.append(
                "实现类作为 `接口实现` 关系起点并直接归属于已绑定服务；"
                "返回实现类实体，不得返回被实现的接口，也不得把实体列表改成计数。"
            )

        if self._is_boundary_application_question(
            normalized
        ) and self._has_rest_path(schema):
            rules.append(
                "系统边界外的应用取下游 API.`目标微服务` 并投影为 `外部应用`；"
                "不得把本地归属位置当作远程目标。"
            )

        if self._needs_distinct(features, normalized):
            rules.append(
                "非聚合实体或路径结果在最终完整投影使用 `RETURN DISTINCT`；"
                "上游 `WITH DISTINCT` 不能替代最终去重。"
            )

        return self._within_budget(rules)

    @staticmethod
    def _contains(text: str, *terms: str) -> bool:
        return any(term in text for term in terms)

    def _method_anchor_relevant(self, question: str) -> bool:
        normalized = question.casefold()
        return (
            bool(self._class_method.search(question))
            or bool(self._bare_camel_method.search(question))
            or self._contains(normalized, "目标方法名", "指定方法")
        )

    def _method_call_direction_relevant(
        self,
        question: str,
        features: frozenset[CodeGraphFeature],
    ) -> bool:
        return self._method_anchor_relevant(question) or bool(
            features
            & {
                CodeGraphFeature.DIRECT,
                CodeGraphFeature.CHAIN,
                CodeGraphFeature.ENTRY_API,
            }
        )

    @staticmethod
    def _node_properties(schema: GraphSchema, label: str) -> set[str]:
        return {
            prop.name
            for node in schema.nodes
            if node.name == label
            for prop in node.properties
        }

    @staticmethod
    def _relationship_properties(
        schema: GraphSchema, relationship_type: str
    ) -> set[str]:
        return {
            prop.name
            for relationship in schema.relationships
            if relationship.name == relationship_type
            for prop in relationship.properties
        }

    @staticmethod
    def _patterns(schema: GraphSchema) -> set[RelationshipPattern]:
        return set(schema.patterns)

    @classmethod
    def _has_patterns(
        cls,
        schema: GraphSchema,
        *patterns: RelationshipPattern,
    ) -> bool:
        return set(patterns) <= cls._patterns(schema)

    @classmethod
    def _has_method_anchor(cls, schema: GraphSchema) -> bool:
        method = cls._node_properties(schema, "方法")
        class_node = cls._node_properties(schema, "类")
        return {"方法名", "全限定名"} <= method and "简名" in class_node

    @classmethod
    def _has_method_calls(cls, schema: GraphSchema) -> bool:
        return cls._has_patterns(
            schema,
            RelationshipPattern(("方法",), "调用", ("方法",)),
        )

    @classmethod
    def _has_entry_api(cls, schema: GraphSchema) -> bool:
        return (
            {"API类型", "接口路径"}
            <= cls._node_properties(schema, "API端点")
            and "调用类型" in cls._relationship_properties(schema, "调用")
            and cls._has_patterns(
                schema,
                RelationshipPattern(("方法",), "调用", ("方法",)),
                RelationshipPattern(("方法",), "调用", ("API端点",)),
            )
        )

    @classmethod
    def _has_rest_path(cls, schema: GraphSchema) -> bool:
        return (
            {"API类型", "接口路径", "目标微服务"}
            <= cls._node_properties(schema, "API端点")
            and "调用类型" in cls._relationship_properties(schema, "调用")
            and cls._has_patterns(
                schema,
                RelationshipPattern(("方法",), "调用", ("API端点",)),
            )
        )

    @classmethod
    def _has_cross_service(cls, schema: GraphSchema) -> bool:
        return (
            "调用类型" in cls._relationship_properties(schema, "调用")
            and cls._has_patterns(
                schema,
                RelationshipPattern(("API端点",), "调用", ("API端点",)),
                RelationshipPattern(("API端点",), "归属于", ("方法",)),
            )
        )

    @classmethod
    def _has_mq_path(cls, schema: GraphSchema) -> bool:
        return (
            "消息流类型" in cls._relationship_properties(schema, "消息流")
            and cls._has_patterns(
                schema,
                RelationshipPattern(("方法",), "消息流", ("消息交换机",)),
                RelationshipPattern(("消息交换机",), "消息流", ("消息队列",)),
                RelationshipPattern(("消息队列",), "消息流", ("方法",)),
            )
        )

    @classmethod
    def _has_sender_rest(cls, schema: GraphSchema) -> bool:
        return (
            "消息流类型" in cls._relationship_properties(schema, "消息流")
            and cls._has_rest_path(schema)
            and cls._has_patterns(
                schema,
                RelationshipPattern(("微服务",), "消息流", ("微服务",)),
                RelationshipPattern(("方法",), "归属于", ("类",)),
                RelationshipPattern(("类",), "归属于", ("微服务",)),
            )
        )

    @classmethod
    def _has_ownership(cls, schema: GraphSchema) -> bool:
        return cls._has_patterns(
            schema,
            RelationshipPattern(("API端点",), "归属于", ("方法",)),
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        )

    @classmethod
    def _has_interface(cls, schema: GraphSchema) -> bool:
        return cls._has_patterns(
            schema,
            RelationshipPattern(("类",), "接口实现", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        )

    @staticmethod
    def _is_service_resource_question(question: str) -> bool:
        return "服务" in question and any(
            term in question
            for term in ("公开api", "公开 api", "对外api", "对外 api", "暴露")
        )

    @staticmethod
    def _is_interface_implementation_question(question: str) -> bool:
        return (
            "接口实现类" in question or "实现类实体" in question
        ) and not any(term in question for term in ("统计", "计数", "数量"))

    @staticmethod
    def _is_sender_rest_matrix(question: str) -> bool:
        return (
            "消息" in question
            and "发送" in question
            and "rest" in question
            and ("每个" in question or "下游" in question)
        )

    @staticmethod
    def _is_impact_question(question: str) -> bool:
        return any(
            term in question
            for term in ("修改", "变更", "影响", "回归", "波及")
        )

    @staticmethod
    def _is_boundary_application_question(question: str) -> bool:
        return "系统边界外" in question or "边界外的应用" in question

    def _is_service_upstream_chain(
        self,
        question: str,
        features: frozenset[CodeGraphFeature],
    ) -> bool:
        asks_correlated_chain = (
            CodeGraphFeature.CHAIN in features
            or "完整对应" in question
            or ("调用服务" in question and "调用方法" in question)
        )
        return (
            bool(self._service_name.search(question))
            and CodeGraphFeature.UPSTREAM in features
            and asks_correlated_chain
            and any(term in question for term in ("到达", "调用目标", "依赖它"))
        )

    def _is_service_outgoing_pair(self, question: str) -> bool:
        return (
            bool(self._service_name.search(question))
            and "远程调用" in question
            and "下游 api" in question
            and ("目标上游 api" in question or "完整配对" in question)
        )

    @staticmethod
    def _needs_distinct(
        features: frozenset[CodeGraphFeature], question: str
    ) -> bool:
        return CodeGraphFeature.AGGREGATE not in features and any(
            term in question
            for term in ("列出", "查询", "哪些", "谁", "是什么", "返回", "依赖")
        )

    def _within_budget(self, rules: list[str]) -> tuple[str, ...]:
        selected: list[str] = []
        size = 0
        for rule in rules:
            rendered_size = len(rule) + 3
            if len(selected) >= self.max_rules or size + rendered_size > self.max_chars:
                break
            selected.append(rule)
            size += rendered_size
        return tuple(selected)
