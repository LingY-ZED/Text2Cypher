from __future__ import annotations

from hashlib import sha256

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.components.code_graph_business_rule_routing import (
    render_few_shot_routing_rules as legacy_render_few_shot_routing_rules,
)
from text2cypher.components.code_graph_business_rule_selector import (
    CodeGraphBusinessRuleSelector,
)
from text2cypher.components.code_graph_business_rules import (
    BusinessRuleModule as LegacyBusinessRuleModule,
)
from text2cypher.components.code_graph_business_rules import (
    load_code_graph_business_rule_modules as legacy_load_rules,
)
from text2cypher.components.primary_agent import PrimaryAgentPromptBuilder
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.domain.models import ChatPrompt, GraphSchema, LLMResponse
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.skills.graph_profile import (
    BusinessRuleModule,
    load_code_graph_business_rule_modules,
)
from text2cypher.skills.policies import (
    BusinessRulePromptStage,
    GraphQuerySkillPolicy,
)
from text2cypher.skills.registry import graph_query_skills
from text2cypher.skills.views import (
    render_few_shot_routing_rules,
    render_translation_rules,
)


class _NoopLLMClient:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        return LLMResponse(content='{"selected_ids":[]}')


def test_legacy_profile_exports_and_new_views_render_identically() -> None:
    modules = (
        BusinessRuleModule.REST,
        BusinessRuleModule.CORE,
        BusinessRuleModule.METHOD_CALL,
    )

    assert LegacyBusinessRuleModule is BusinessRuleModule
    assert legacy_load_rules(modules) == load_code_graph_business_rule_modules(
        modules
    )
    assert legacy_render_few_shot_routing_rules(
        modules
    ) == render_few_shot_routing_rules(modules)
    assert render_translation_rules(modules) == load_code_graph_business_rule_modules(
        modules
    )


def test_policy_preserves_module_selection_and_exposes_skills() -> None:
    question = "查询 InsidePaymentServiceImpl.pay 的完整下游调用链。"
    policy = GraphQuerySkillPolicy()

    assert CodeGraphBusinessRuleSelector is GraphQuerySkillPolicy
    assert policy.select(
        question,
        stage=BusinessRulePromptStage.CYPHER_TRANSLATOR,
    ) == (
        BusinessRuleModule.CORE,
        BusinessRuleModule.ANCHOR_OWNERSHIP,
        BusinessRuleModule.METHOD_CALL,
        BusinessRuleModule.ENTRY_API,
        BusinessRuleModule.REST,
        BusinessRuleModule.ORDERED_PATH,
    )
    assert tuple(skill.id.value for skill in policy.select_skills(
        question,
        stage=BusinessRulePromptStage.CYPHER_TRANSLATOR,
    )) == (
        "code_structure",
        "call_analysis",
        "api_analysis",
        "dependency_analysis",
    )
    assert [skill.id.value for skill in graph_query_skills()] == [
        "code_structure",
        "call_analysis",
        "api_analysis",
        "dependency_analysis",
        "change_impact",
    ]
    assert QueryShape.FULL_DOWNSTREAM_CHAIN in graph_query_skills()[1].query_shapes


def test_prompt_knowledge_views_match_the_p3_frozen_rendering() -> None:
    question = "ts-rebook-service 通过 REST 调用了哪些下游服务？"
    translator = DefaultPromptBuilder().build(GraphSchema(), question)
    router = LLMFewShotRouter((), _NoopLLMClient())._build_router_prompt(
        question,
        (),
    )
    primary = PrimaryAgentPromptBuilder().build(question, 3)

    assert sha256(translator.system.encode()).hexdigest() == (
        "639a6187075df4a44811d81a9f9d1b170c291cf9d74f5fb7b1513eca7b498e7c"
    )
    assert sha256(router.system.encode()).hexdigest() == (
        "9c10acd53fb8747fd9cbde884ca5e857610b2b738ff7f4d2cbe525f87535b070"
    )
    assert sha256(primary.system.encode()).hexdigest() == (
        "940d969db0f57ecfd61b35fb8f864718d0120ab0b27d78f4413916271a4436a3"
    )
