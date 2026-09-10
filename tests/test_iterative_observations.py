from __future__ import annotations

from text2cypher.components.iterative_planner import IterativePlannerPromptBuilder
from text2cypher.domain.iterative import (
    IterativePlanningContext,
    RuntimeToolName,
)
from text2cypher.domain.models import ExecutedCypher, QueryResult
from text2cypher.runtime.observations import ObservationCompactor


def test_observation_retains_raw_result_but_planner_view_is_compact() -> None:
    cypher = "MATCH (secret) RETURN secret.password AS password"
    raw = ExecutedCypher(
        cypher,
        QueryResult(
            ("qualified_name", "password", "cypher"),
            (
                {
                    "qualified_name": "PaymentService.pay",
                    "password": "top-secret",
                    "cypher": cypher,
                },
                {
                    "qualified_name": "HiddenService.run",
                    "password": "other-secret",
                    "cypher": "RETURN hidden",
                },
            ),
        ),
    )
    compactor = ObservationCompactor(max_key_entities=1)

    observation = compactor.result(
        observation_id="o1",
        action_id="r1a1",
        tool=RuntimeToolName.QUERY_CODE_GRAPH,
        raw_result=raw,
        action_fingerprint="query:payment",
    )
    view = compactor.planner_view(observation)
    prompt = IterativePlannerPromptBuilder().build(
        IterativePlanningContext(
            original_question="分析支付服务",
            next_round=2,
            remaining_rounds=2,
            remaining_actions=8,
            observations=(view,),
            obtained_information=("找到方法",),
            missing_information=("调用方",),
        )
    )

    assert observation.raw_result is raw
    assert observation.key_entities[0].attributes["qualified_name"] == (
        "PaymentService.pay"
    )
    assert cypher not in prompt.user
    assert "RETURN hidden" not in prompt.user
    assert "other-secret" not in prompt.user
    assert "top-secret" in prompt.user
    assert "cypher" not in observation.key_entities[0].attributes


def test_empty_and_duplicate_observations_have_distinct_safe_states() -> None:
    compactor = ObservationCompactor()
    empty = compactor.result(
        observation_id="o1",
        action_id="r1a1",
        tool=RuntimeToolName.RESOLVE_SYMBOL,
        raw_result=(),
        action_fingerprint="resolve:A.run",
    )
    duplicate = compactor.duplicate(
        observation_id="o2",
        action_id="r2a1",
        tool=RuntimeToolName.RESOLVE_SYMBOL,
    )

    assert empty.status.value == "empty"
    assert len(empty.evidence_fingerprints) == 1
    assert duplicate.status.value == "duplicate"
    assert duplicate.evidence_fingerprints == ()
