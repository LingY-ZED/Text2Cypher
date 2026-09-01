from __future__ import annotations

import importlib.util
import inspect

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.components.code_graph_business_rules import (
    load_code_graph_business_rules,
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


def test_all_llm_prompt_stages_share_the_complete_rules_once() -> None:
    schema = GraphSchema()
    example = _example()
    rules = load_code_graph_business_rules()
    prompts = (
        DefaultPromptBuilder().build(schema, "list nodes"),
        QuestionDecompositionPromptBuilder().build(schema, "list nodes", 3),
        LLMFewShotRouter((example,), _NoopLLMClient())._build_router_prompt(
            "question", (example,)
        ),
    )

    assert all(prompt.system.count(rules) == 1 for prompt in prompts)
    assert all("代码知识图谱业务语义：" in prompt.system for prompt in prompts)
    assert all(rules not in prompt.user for prompt in prompts)


def test_generator_rules_do_not_depend_on_question_wording() -> None:
    builder = DefaultPromptBuilder()
    schema = GraphSchema()

    call_chain_prompt = builder.build(schema, "trace callers of getAllFood")
    paraphrase_prompt = builder.build(schema, "which code can eventually reach it")

    assert call_chain_prompt.system == paraphrase_prompt.system
    assert load_code_graph_business_rules() in call_chain_prompt.system


def test_legacy_selector_has_no_module_or_prompt_builder_parameter() -> None:
    parameters = inspect.signature(DefaultPromptBuilder).parameters

    assert "semantic_selector" not in parameters
    assert (
        importlib.util.find_spec("text2cypher.components.code_graph_semantics")
        is None
    )
