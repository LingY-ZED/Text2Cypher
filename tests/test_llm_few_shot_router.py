from __future__ import annotations

import pytest

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    LLMResponse,
    NodeSchema,
    RelationshipPattern,
    RelationshipSchema,
)


class StubLLMClient:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _schema() -> GraphSchema:
    return GraphSchema(
        nodes=(NodeSchema("A"), NodeSchema("B")),
        relationships=(RelationshipSchema("R"),),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )


def _example(
    identifier: str,
    *,
    requirements: FewShotSchemaRequirements | None = None,
    cypher: str = "MATCH (n) RETURN n",
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category="test",
        question=f"{identifier} 示例问题",
        cypher=cypher,
        aliases=(f"{identifier} 别名",),
        tags=("测试",),
        schema_requirements=requirements or FewShotSchemaRequirements(),
    )


def _compatible_requirements() -> FewShotSchemaRequirements:
    return FewShotSchemaRequirements(
        node_labels=("A", "B"),
        relationship_types=("R",),
        patterns=(RelationshipPattern(("A",), "R", ("B",)),),
    )


def test_router_sends_only_stable_compatible_metadata() -> None:
    compatible = _example(
        "a-compatible",
        requirements=_compatible_requirements(),
        cypher="MATCH (secret:Internal) RETURN secret",
    )
    second_compatible = _example(
        "b-compatible",
        requirements=_compatible_requirements(),
    )
    incompatible = _example(
        "z-incompatible",
        requirements=FewShotSchemaRequirements(
            node_labels=("A", "B"),
            relationship_types=("R",),
            patterns=(RelationshipPattern(("B",), "R", ("A",)),),
        ),
        cypher="MATCH (should_not:Appear) RETURN should_not",
    )
    client = StubLLMClient(
        LLMResponse(content='{"selected_ids":["a-compatible"]}')
    )
    router = LLMFewShotRouter(
        (incompatible, second_compatible, compatible),
        client,
    )

    selected = router.route("查询目标", _schema())

    assert tuple(example.id for example in selected) == ("a-compatible",)
    assert len(client.prompts) == 1
    prompt = client.prompts[0]
    assert prompt.user.index("a-compatible") < prompt.user.index("b-compatible")
    assert "z-incompatible" not in prompt.user
    assert "MATCH" not in prompt.user
    assert "schema_requirements" not in prompt.user
    assert "Internal" not in prompt.user
    assert "查询锚点" in prompt.system
    assert "关系方向" in prompt.system
    assert "返回字段、分组维度和聚合形状" in prompt.system
    assert "不得仅因共享关键词而优先选择" in prompt.system
    assert "完整上游链、完整下游链" in prompt.system
    assert "变更方法直接远程下游服务选方法直接出口" in prompt.system
    assert "选择完整链时不得用只返回其中一列的示例替代" in prompt.system
    assert "同时覆盖起始锚点和最终返回形状" in prompt.system


@pytest.mark.parametrize(
    "content",
    [
        '{"selected_ids":["b","a"]}',
        '```json\n{"selected_ids":["b","a"]}\n```',
    ],
)
def test_router_accepts_json_and_standard_json_fence_in_response(
    content: str,
) -> None:
    client = StubLLMClient(LLMResponse(content=content))
    router = LLMFewShotRouter(
        (_example("a"), _example("b")),
        client,
    )

    selected = router.route("查询", GraphSchema())

    assert tuple(example.id for example in selected) == ("b", "a")


def test_router_preserves_valid_ids_and_filters_invalid_values() -> None:
    client = StubLLMClient(
        LLMResponse(
            content=(
                '{"selected_ids":["missing","b",3,"b","a"],'
                '"ignored":"metadata"}'
            )
        )
    )
    router = LLMFewShotRouter(
        (_example("a"), _example("b")),
        client,
    )

    selected = router.route("查询", GraphSchema())

    assert tuple(example.id for example in selected) == ("b", "a")


@pytest.mark.parametrize(
    "content",
    [
        '解释文字\n{"selected_ids":["a"]}',
        '{"ids":["a"]}',
        '["a"]',
        '```python\n{"selected_ids":["a"]}\n```',
    ],
)
def test_router_falls_back_for_invalid_response(
    content: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(LLMResponse(content=content))
    router = LLMFewShotRouter((_example("a"),), client)

    assert router.route("查询", GraphSchema()) == ()
    assert "ValueError" in caplog.text
    assert content not in caplog.text


def test_router_returns_empty_without_compatible_candidates() -> None:
    client = StubLLMClient(LLMResponse(content='{"selected_ids":["a"]}'))
    router = LLMFewShotRouter(
        (_example("a", requirements=_compatible_requirements()),),
        client,
    )

    assert router.route("查询", GraphSchema(nodes=(NodeSchema("City"),))) == ()
    assert client.prompts == []


def test_router_falls_back_after_llm_error_without_logging_sensitive_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubLLMClient(LLMGenerationError("router-secret-response"))
    router = LLMFewShotRouter((_example("a"),), client)

    assert router.route("查询", GraphSchema()) == ()
    assert "LLMGenerationError" in caplog.text
    assert "router-secret-response" not in caplog.text


def test_router_applies_prompt_character_budget_after_id_validation() -> None:
    client = StubLLMClient(LLMResponse(content='{"selected_ids":["a","b"]}'))
    router = LLMFewShotRouter(
        (
            _example("a", cypher="MATCH (n) RETURN '超过预算'"),
            _example("b"),
        ),
        client,
        max_chars=1,
    )

    assert router.route("查询", GraphSchema()) == ()
