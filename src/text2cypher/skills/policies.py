"""Graph Query Skills 的确定性场景选择策略。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from text2cypher.domain.models import FewShotExample
from text2cypher.domain.query_shapes import QueryShape, resolve_query_shape
from text2cypher.skills.graph_profile import BusinessRuleModule
from text2cypher.skills.models import GraphSkillDefinition
from text2cypher.skills.registry import skills_for_modules


class BusinessRulePromptStage(StrEnum):
    """可以消费 Profile 语义片段的当前 Prompt 阶段。"""

    FEW_SHOT_ROUTER = "few_shot_router"
    CYPHER_TRANSLATOR = "cypher_translator"


_STAGE_EXCLUDED_MODULES: dict[
    BusinessRulePromptStage,
    frozenset[BusinessRuleModule],
] = {
    BusinessRulePromptStage.FEW_SHOT_ROUTER: frozenset(
        {BusinessRuleModule.DECOMPOSITION}
    ),
    BusinessRulePromptStage.CYPHER_TRANSLATOR: frozenset(
        {BusinessRuleModule.DECOMPOSITION}
    ),
}

_SHAPE_MODULES: dict[QueryShape, frozenset[BusinessRuleModule]] = {
    QueryShape.GENERAL: frozenset(),
    QueryShape.UPSTREAM_REACHABILITY: frozenset(
        {BusinessRuleModule.METHOD_CALL}
    ),
    QueryShape.DIRECT_UPSTREAM: frozenset({BusinessRuleModule.METHOD_CALL}),
    QueryShape.DIRECT_DOWNSTREAM_METHOD: frozenset(
        {BusinessRuleModule.METHOD_CALL}
    ),
    QueryShape.ORDERED_METHOD_PATH: frozenset(
        {
            BusinessRuleModule.METHOD_CALL,
            BusinessRuleModule.ORDERED_PATH,
        }
    ),
    QueryShape.REACHABLE_ENTRY_API: frozenset(
        {
            BusinessRuleModule.METHOD_CALL,
            BusinessRuleModule.ENTRY_API,
        }
    ),
    QueryShape.FULL_ENTRY_CHAIN: frozenset(
        {
            BusinessRuleModule.METHOD_CALL,
            BusinessRuleModule.ENTRY_API,
            BusinessRuleModule.ORDERED_PATH,
        }
    ),
    QueryShape.FULL_DOWNSTREAM_CHAIN: frozenset(
        {
            BusinessRuleModule.METHOD_CALL,
            BusinessRuleModule.ENTRY_API,
            BusinessRuleModule.REST,
            BusinessRuleModule.ORDERED_PATH,
        }
    ),
    QueryShape.DIRECT_REST_EGRESS: frozenset({BusinessRuleModule.REST}),
}

_REST_CUES = (
    "rest",
    "远程请求",
    "远程调用",
    "同步请求",
    "下游api",
    "目标服务",
    "下游服务",
    "跨服务",
    "外部服务",
)
_MQ_CUES = (
    "mq",
    "消息",
    "队列",
    "交换机",
    "发布",
    "消费",
    "路由键",
    "rabbit",
)
_ENTRY_API_CUES = (
    "公开api",
    "入口api",
    "上游api",
    "http",
    "请求方式",
    "请求体",
    "响应类型",
    "接口路径",
    "http动词",
)
_METHOD_CALL_CUES = (
    "上游调用链",
    "上游方法",
    "上游调用方",
    "上游调用者",
    "直接上游",
    "谁调用",
    "哪些方法调用",
    "接口分派",
    "接口调用",
    "调用者",
    "被调用",
)
_ORDERED_PATH_CUES = (
    "上游调用链",
    "有序",
    "方法路径",
    "调用路径",
    "调用顺序",
    "按顺序",
    "完整入口链",
    "完整入口调用链",
    "完整下游链",
    "完整下游调用链",
)
_ANCHOR_OWNERSHIP_CUES = (
    "微服务",
    "服务名称",
    "service",
    "归属",
    "属于",
    "声明",
    "实现接口",
    "全限定名",
)
_AGGREGATION_CUES = ("统计", "计数", "数量", "多少", "分组", "分布", "count")
_CHANGE_CUES = ("修改", "变更", "影响", "回归验证")


class GraphQuerySkillPolicy:
    """为 Router 或 Translator 选择当前问题需要的 Profile 片段。"""

    def select(
        self,
        question: str,
        *,
        stage: BusinessRulePromptStage,
        query_shape: QueryShape | None = None,
        examples: Iterable[FewShotExample] = (),
    ) -> tuple[BusinessRuleModule, ...]:
        """从子问题、形状和示例元数据构造稳定的模块集合。"""

        normalized_question = self._normalize_question(question)
        self._validate_stage(stage)
        effective_shape = query_shape or resolve_query_shape(normalized_question)
        if not isinstance(effective_shape, QueryShape):
            raise TypeError("查询形状必须是 QueryShape")

        modules = set(_SHAPE_MODULES[effective_shape])
        modules.update(
            self._modules_from_text(
                normalized_question,
                allow_method_call=(
                    effective_shape is not QueryShape.DIRECT_REST_EGRESS
                ),
                allow_decomposition=True,
            )
        )
        modules.update(self._modules_from_examples(examples))
        if modules == {BusinessRuleModule.AGGREGATION}:
            modules.clear()
        self._apply_dependencies(modules)
        self._apply_stage_policy(modules, stage)
        modules.add(BusinessRuleModule.CORE)
        return tuple(module for module in BusinessRuleModule if module in modules)

    def select_skills(
        self,
        question: str,
        *,
        stage: BusinessRulePromptStage,
        query_shape: QueryShape | None = None,
        examples: Iterable[FewShotExample] = (),
    ) -> tuple[GraphSkillDefinition, ...]:
        """将与 Prompt 等价的模块选择映射为审计用 Skill 清单。"""

        return skills_for_modules(
            self.select(
                question,
                stage=stage,
                query_shape=query_shape,
                examples=examples,
            )
        )

    @staticmethod
    def _normalize_question(question: str) -> str:
        if type(question) is not str:
            raise TypeError("问题必须是字符串")
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")
        return normalized_question

    @staticmethod
    def _validate_stage(stage: BusinessRulePromptStage) -> None:
        if not isinstance(stage, BusinessRulePromptStage):
            raise TypeError("规则 Prompt 阶段必须是 BusinessRulePromptStage")

    @classmethod
    def _modules_from_examples(
        cls,
        examples: Iterable[FewShotExample],
    ) -> set[BusinessRuleModule]:
        modules: set[BusinessRuleModule] = set()
        for example in examples:
            if not isinstance(example, FewShotExample):
                raise TypeError("示例必须是 FewShotExample")
            modules.update(_SHAPE_MODULES[example.query_shape])
            if example.category == "mq_query":
                modules.add(BusinessRuleModule.MQ)
            if example.category == "aggregate_statistics":
                modules.add(BusinessRuleModule.AGGREGATION)
            metadata = " ".join(
                (
                    example.category,
                    example.question,
                    *example.aliases,
                    *example.tags,
                )
            )
            modules.update(
                cls._modules_from_text(
                    metadata,
                    allow_method_call=True,
                    allow_decomposition=False,
                )
            )
        return modules

    @staticmethod
    def _modules_from_text(
        text: str,
        *,
        allow_method_call: bool,
        allow_decomposition: bool,
    ) -> set[BusinessRuleModule]:
        compact = "".join(text.lower().split())
        modules: set[BusinessRuleModule] = set()
        has_rest = _contains_any(compact, _REST_CUES)
        has_mq = _contains_any(compact, _MQ_CUES)
        has_entry_api = _contains_any(compact, _ENTRY_API_CUES)
        has_ordered_path = _contains_any(compact, _ORDERED_PATH_CUES)
        has_anchor_ownership = _contains_any(compact, _ANCHOR_OWNERSHIP_CUES)
        has_aggregation = _contains_any(compact, _AGGREGATION_CUES)

        if has_rest:
            modules.add(BusinessRuleModule.REST)
        if has_mq:
            modules.add(BusinessRuleModule.MQ)
        if has_entry_api:
            modules.add(BusinessRuleModule.ENTRY_API)
        if has_ordered_path:
            modules.add(BusinessRuleModule.ORDERED_PATH)
        if has_anchor_ownership:
            modules.add(BusinessRuleModule.ANCHOR_OWNERSHIP)
        if allow_method_call and (
            _contains_any(compact, _METHOD_CALL_CUES)
            or (
                "直接调用" in compact
                and "方法" in compact
                and not has_rest
            )
        ):
            modules.add(BusinessRuleModule.METHOD_CALL)
        if has_aggregation and modules:
            modules.add(BusinessRuleModule.AGGREGATION)
        if allow_decomposition and _contains_any(compact, _CHANGE_CUES):
            required = {
                BusinessRuleModule.METHOD_CALL,
                BusinessRuleModule.ENTRY_API,
                BusinessRuleModule.REST,
            }
            if len(modules & required) >= 2:
                modules.add(BusinessRuleModule.DECOMPOSITION)
        return modules

    @staticmethod
    def _apply_dependencies(modules: set[BusinessRuleModule]) -> None:
        anchor_dependents = {
            BusinessRuleModule.METHOD_CALL,
            BusinessRuleModule.ENTRY_API,
            BusinessRuleModule.REST,
            BusinessRuleModule.MQ,
            BusinessRuleModule.ORDERED_PATH,
        }
        if modules & anchor_dependents:
            modules.add(BusinessRuleModule.ANCHOR_OWNERSHIP)
        if BusinessRuleModule.ORDERED_PATH in modules:
            modules.add(BusinessRuleModule.METHOD_CALL)

    @staticmethod
    def _apply_stage_policy(
        modules: set[BusinessRuleModule],
        stage: BusinessRulePromptStage,
    ) -> None:
        modules.difference_update(_STAGE_EXCLUDED_MODULES[stage])


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)
