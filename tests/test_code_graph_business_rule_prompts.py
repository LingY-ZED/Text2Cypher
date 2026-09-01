from __future__ import annotations

import inspect

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.components.code_graph_business_rules import (
    BusinessRuleModule,
    load_code_graph_business_rule_module,
    load_code_graph_business_rules,
)
from text2cypher.components.primary_agent_semantic_capabilities import (
    load_primary_agent_semantic_capabilities,
)
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.components.question_decomposition import (
    QuestionDecompositionPromptBuilder,
)
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    LLMResponse,
)


class _NoopLLMClient:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        return LLMResponse(content='{"selected_ids":[]}')


def _example() -> FewShotExample:
    return FewShotExample(
        id="example",
        category="test",
        question="example question",
        cypher="MATCH (node) RETURN node",
        aliases=("example",),
        tags=("test",),
        schema_requirements=FewShotSchemaRequirements(),
    )


def test_prompt_stages_receive_role_appropriate_business_context() -> None:
    schema = GraphSchema()
    example = _example()
    translator = DefaultPromptBuilder().build(
        schema,
        "ts-rebook-service 通过 REST 调用了哪些下游服务？",
    )
    router = LLMFewShotRouter(
        (example,),
        _NoopLLMClient(),
    )._build_router_prompt(
        "ts-rebook-service 通过 REST 调用了哪些下游服务？",
        (example,),
    )
    decomposer = QuestionDecompositionPromptBuilder().build(
        schema,
        "list nodes",
        3,
    )

    core = load_code_graph_business_rule_module(BusinessRuleModule.CORE)
    rest = load_code_graph_business_rule_module(BusinessRuleModule.REST)

    assert translator.system.count(core) == 1
    assert translator.system.count(rest) == 1
    assert "方法-[:下游调用]->下游API" in translator.system
    assert "详细消息路径固定为" not in translator.system
    assert "# 代码知识图谱业务语义：路由核心" in router.system
    assert router.system.count("# 代码知识图谱业务语义：路由核心") == 1
    assert "# 代码知识图谱业务语义：路由 REST" in router.system
    assert "方法-[:下游调用]->下游API" not in router.system
    assert load_primary_agent_semantic_capabilities() in decomposer.system
    assert "方法-[:下游调用]->下游API" not in decomposer.system
    assert "MATCH" not in decomposer.system
    assert all(
        load_code_graph_business_rules() not in prompt.system
        for prompt in (translator, router, decomposer)
    )
    assert all(
        "代码知识图谱业务语义：" not in prompt.user
        for prompt in (translator, router, decomposer)
    )


def test_translator_rules_change_only_with_the_subquestion_scene() -> None:
    builder = DefaultPromptBuilder()
    schema = GraphSchema()

    rest_prompt = builder.build(
        schema,
        "ts-rebook-service 通过 REST 调用了哪些下游服务？",
    )
    mq_prompt = builder.build(schema, "谁消费 email 队列中的消息？")
    chain_prompt = builder.build(
        schema,
        "查询 InsidePaymentServiceImpl.pay 的完整下游调用链。",
    )

    assert "方法-[:下游调用]->下游API" in rest_prompt.system
    assert "详细消息路径固定为" not in rest_prompt.system
    assert "详细消息路径固定为" in mq_prompt.system
    assert "方法-[:下游调用]->下游API" not in mq_prompt.system
    assert "完整下游链必须用 `UNION` 分开" in chain_prompt.system
    assert "方法-[:下游调用]->下游API" in chain_prompt.system
    assert "详细消息路径固定为" not in chain_prompt.system
    assert rest_prompt.system.count(
        load_code_graph_business_rule_module(BusinessRuleModule.CORE)
    ) == 1
    assert mq_prompt.system.count(
        load_code_graph_business_rule_module(BusinessRuleModule.CORE)
    ) == 1


def test_translator_uses_selected_few_shot_metadata_to_complete_selection() -> None:
    example = FewShotExample(
        id="rest-example",
        category="path_query",
        question="查看服务远程调用的目标。",
        cypher="MATCH (node) RETURN node",
        aliases=("查看 REST 下游",),
        tags=("REST", "下游API", "跨服务"),
        schema_requirements=FewShotSchemaRequirements(),
    )

    prompt = DefaultPromptBuilder().build(
        GraphSchema(),
        "查询某服务的依赖",
        (example,),
    )

    assert "方法-[:下游调用]->下游API" in prompt.system
    assert "详细消息路径固定为" not in prompt.system


def test_builders_expose_optional_rule_selector_without_port_changes() -> None:
    prompt_builder_parameters = inspect.signature(DefaultPromptBuilder).parameters
    router_parameters = inspect.signature(LLMFewShotRouter).parameters

    assert "rule_selector" in prompt_builder_parameters
    assert "rule_selector" in router_parameters
    assert "semantic_selector" not in prompt_builder_parameters
