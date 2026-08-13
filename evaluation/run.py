"""Run the frozen evaluation suite against a real Text2Cypher Pipeline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from evaluation.comparator import CaseVerdict, compare_case
from evaluation.dataset import DEFAULT_CASES_PATH, load_cases
from evaluation.instrumentation import (
    EvaluationRecorder,
    RecordingCypherExecutor,
    RecordingCypherParser,
    RecordingCypherValidator,
    RecordingFewShotRouter,
    RecordingQuestionDecomposer,
    RecoveryEventHandler,
    StageLLMClient,
)
from evaluation.metrics import calculate_metrics
from evaluation.models import EvaluationCase, EvaluationIntent
from evaluation.probes import run_recovery_probes
from evaluation.report import render_report
from text2cypher.application.cypher_corrector import LLMCypherCorrector
from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.application.question_decomposer import LLMQuestionDecomposer
from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.components.result_formatter import JsonResultFormatter
from text2cypher.components.retry import RetryPolicy
from text2cypher.config import Settings
from text2cypher.domain.models import GraphSchema, QueryResult, Text2CypherResponse
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader
from text2cypher.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider
from text2cypher.infrastructure.neo4j.executor import Neo4jCypherExecutor
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher
from text2cypher.infrastructure.neo4j.validator import Neo4jCypherValidator


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--revision", default="error-recovery")
    parser.add_argument("--revision-sha", required=True)
    args = parser.parse_args(argv)
    if args.runs <= 0:
        parser.error("--runs must be positive")

    cases = load_cases(args.cases)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = _evaluation_settings()
    metadata = _metadata(settings, args.revision, args.revision_sha, args.runs)
    _write_json(output / "metadata.json", metadata)

    retry_policy = _retry_policy(settings)
    provider = Neo4jDriverProvider(settings, retry_policy=retry_policy)
    try:
        driver = provider.driver
        schema_fetcher = Neo4jSchemaFetcher(
            driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
            retry_policy=retry_policy,
        )
        schema = schema_fetcher.fetch()
        metadata["schema_fingerprint"] = _schema_fingerprint(schema)
        _write_json(output / "metadata.json", metadata)
        validator = Neo4jCypherValidator(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            retry_policy=retry_policy,
        )
        executor = Neo4jCypherExecutor(
            driver,
            settings.neo4j_database,
            settings.query_timeout_seconds,
            settings.max_result_rows,
            retry_policy=retry_policy,
        )
        oracle_payload, drift = _run_oracles(cases, validator, executor)
        _write_json(output / "oracle-results.json", oracle_payload)
        probes = run_recovery_probes()
        if drift:
            render_report(
                output,
                metadata,
                (),
                None,
                probes,
                oracle_drift=drift,
            )
            return 2

        llm_client = OpenAICompatibleLLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
            disable_thinking=settings.llm_disable_thinking,
            retry_policy=retry_policy,
        )
        recorder = EvaluationRecorder()
        handler = RecoveryEventHandler(recorder)
        decomposer_logger = logging.getLogger(
            "text2cypher.application.question_decomposer"
        )
        previous_decomposer_level = decomposer_logger.level
        decomposer_logger.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        try:
            pipeline = _build_instrumented_pipeline(
                settings,
                provider,
                llm_client,
                retry_policy,
                recorder,
            )
            records = _run_cases(
                cases,
                args.runs,
                pipeline,
                recorder,
                output / "case-results.jsonl",
            )
        finally:
            logging.getLogger().removeHandler(handler)
            decomposer_logger.setLevel(previous_decomposer_level)
            llm_client.close()
    finally:
        provider.close()

    _write_jsonl(output / "case-results.jsonl", records)
    _write_csv(output / "case-results.csv", records)
    metrics = calculate_metrics(records, probes)
    _write_json(output / "metrics.json", metrics)
    render_report(output, metadata, records, metrics, probes)
    return 0 if metrics["quality_gate_passed"] else 1


def _evaluation_settings() -> Settings:
    return Settings.from_environment().model_copy(
        update={
            "few_shot_enabled": True,
            "question_decomposition_enabled": True,
            "cypher_correction_enabled": True,
            "empty_result_correction_enabled": True,
            "retry_enabled": True,
            "retry_max_attempts": 3,
            "llm_disable_thinking": True,
        }
    )


def _retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        enabled=settings.retry_enabled,
        max_attempts=settings.retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
    )


def _build_instrumented_pipeline(
    settings: Settings,
    provider: Neo4jDriverProvider,
    llm_client: OpenAICompatibleLLMClient,
    retry_policy: RetryPolicy,
    recorder: EvaluationRecorder,
) -> Text2CypherPipeline:
    driver = provider.driver
    examples = JsonFewShotExampleLoader(settings.few_shot_library_path).load()
    question_decomposer = RecordingQuestionDecomposer(
        LLMQuestionDecomposer(
            StageLLMClient(llm_client, recorder, "decomposer"),
            review_llm_client=StageLLMClient(llm_client, recorder, "reviewer"),
            max_subquestions=settings.question_decomposition_max_subquestions,
        ),
        recorder,
    )
    few_shot_router = RecordingFewShotRouter(
        LLMFewShotRouter(
            examples,
            StageLLMClient(llm_client, recorder, "router"),
            top_k=settings.few_shot_top_k,
            max_chars=settings.few_shot_max_chars,
        ),
        recorder,
    )
    return Text2CypherPipeline(
        schema_fetcher=Neo4jSchemaFetcher(
            driver,
            settings.neo4j_database,
            settings.schema_timeout_seconds,
            retry_policy=retry_policy,
        ),
        prompt_builder=DefaultPromptBuilder(),
        llm_client=StageLLMClient(llm_client, recorder, "generation"),
        cypher_parser=RecordingCypherParser(DefaultCypherParser(), recorder),
        cypher_validator=RecordingCypherValidator(
            Neo4jCypherValidator(
                driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
                retry_policy=retry_policy,
            ),
            recorder,
        ),
        cypher_executor=RecordingCypherExecutor(
            Neo4jCypherExecutor(
                driver,
                settings.neo4j_database,
                settings.query_timeout_seconds,
                settings.max_result_rows,
                retry_policy=retry_policy,
            ),
            recorder,
        ),
        result_formatter=JsonResultFormatter(),
        question_decomposer=question_decomposer,
        few_shot_router=few_shot_router,
        cypher_corrector=LLMCypherCorrector(
            StageLLMClient(llm_client, recorder, "corrector")
        ),
        recover_empty_results=True,
    )


def _run_oracles(
    cases: Sequence[EvaluationCase],
    validator: Neo4jCypherValidator,
    executor: Neo4jCypherExecutor,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for case in cases:
        intents: list[dict[str, Any]] = []
        for intent in case.intents:
            report = validator.validate(intent.oracle_cypher)
            result = executor.execute(intent.oracle_cypher)
            actual = _jsonable_rows(result)
            expected = [dict(row) for row in intent.expected_snapshot]
            matches = _row_fingerprint(actual) == _row_fingerprint(expected)
            intents.append(
                {
                    "id": intent.id,
                    "label": intent.label,
                    "query_type": report.query_type,
                    "columns": list(result.columns),
                    "rows": actual,
                    "snapshot_matches": matches,
                }
            )
            if not matches:
                drift.append(
                    {
                        "case_id": case.id,
                        "intent_id": intent.id,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        output.append({"id": case.id, "question": case.question, "intents": intents})
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": output,
        "drift_detected": bool(drift),
    }, drift


def _run_cases(
    cases: Sequence[EvaluationCase],
    runs: int,
    pipeline: Text2CypherPipeline,
    recorder: EvaluationRecorder,
    checkpoint_path: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = len(cases) * runs
    completed = 0
    for run_number in range(1, runs + 1):
        for case in cases:
            completed += 1
            print(
                f"[{completed}/{total}] run={run_number} case={case.id}",
                flush=True,
            )
            recorder.reset()
            started = perf_counter()
            response: Text2CypherResponse | None = None
            error: Exception | None = None
            try:
                response = pipeline.run(case.question)
            except Exception as caught:
                error = caught
            duration = round(perf_counter() - started, 6)
            record = _case_record(
                case,
                run_number,
                duration,
                response,
                error,
                recorder,
            )
            records.append(record)
            _write_jsonl(checkpoint_path, records)
    return records


def _case_record(
    case: EvaluationCase,
    run_number: int,
    duration: float,
    response: Text2CypherResponse | None,
    error: Exception | None,
    recorder: EvaluationRecorder,
) -> dict[str, Any]:
    expected_queries = _expected_query_count(recorder.events)
    query_events = {
        query_index: [
            event
            for event in recorder.events
            if event.get("query_index") == query_index
        ]
        for query_index in range(1, expected_queries + 1)
    }
    generation_success = all(
        any(
            event.get("component") == "cypher"
            and event.get("stage") == "parse"
            and event.get("outcome") == "succeeded"
            for event in events
        )
        for events in query_events.values()
    )
    syntax_success = all(
        _last_outcome(events, "explain") == "succeeded"
        for events in query_events.values()
    )
    execution_success = all(
        _last_outcome(events, "execute") == "succeeded"
        for events in query_events.values()
    )
    initial_syntax = all(
        any(
            event.get("stage") == "explain"
            and event.get("source") == "generation"
            and event.get("outcome") == "succeeded"
            for event in events
        )
        for events in query_events.values()
    )
    initial_execution = all(
        any(
            event.get("stage") == "execute"
            and event.get("source") == "generation"
            and event.get("outcome") == "succeeded"
            for event in events
        )
        for events in query_events.values()
    )
    verdict = _semantic_verdict(case, response)
    decomposition_contract_passed = _decomposition_contract_passed(
        case,
        recorder.events,
    )
    decomposition = _decomposition_observation(recorder.events)
    sub_queries = _response_payload(response)
    record: dict[str, Any] = {
        "case_id": case.id,
        "difficulty": case.difficulty.value,
        "category": case.category,
        "question": case.question,
        "run": run_number,
        "duration_seconds": duration,
        "generation_success": generation_success,
        "syntax_success": syntax_success,
        "execution_success": execution_success,
        "nonempty_success": bool(
            response is not None
            and all(sub_query.result.rows for sub_query in response.sub_queries)
        ),
        "semantic_success": verdict.matched,
        "decomposition_contract_success": decomposition_contract_passed,
        "semantic_outcome": verdict.semantic_outcome.value,
        "intent_verdicts": [asdict(item) for item in verdict.intents],
        "failure_stage": _failure_stage(
            recorder.events,
            response,
            verdict,
            error,
        ),
        "error": (
            {
                "type": type(error).__name__,
                "message": "详细错误正文未保存；请根据脱敏阶段事件定位",
            }
            if error is not None
            else None
        ),
        "decomposed": decomposition[0] if decomposition is not None else None,
        "sub_queries": sub_queries,
        "selected_example_ids": [
            event.get("selected_ids", [])
            for event in recorder.events
            if event.get("component") == "router" and event.get("stage") == "selection"
        ],
        "initial_syntax_success": initial_syntax,
        "initial_execution_success": initial_execution,
        "events": [dict(event) for event in recorder.events],
        "recovery_events": [dict(event) for event in recorder.recovery_events],
    }
    return record


def _expected_query_count(events: Sequence[Mapping[str, Any]]) -> int:
    decomposition = next(
        (
            event
            for event in events
            if event.get("component") == "decomposer"
            and event.get("stage") == "decomposition"
        ),
        None,
    )
    if decomposition is None:
        return 1
    count = decomposition.get("sub_question_count")
    return int(count) if isinstance(count, int) and count > 0 else 1


def _last_outcome(events: Sequence[Mapping[str, Any]], stage: str) -> str | None:
    matching = [event for event in events if event.get("stage") == stage]
    return str(matching[-1].get("outcome")) if matching else None


def _semantic_verdict(
    case: EvaluationCase,
    response: Text2CypherResponse | None,
) -> CaseVerdict:
    if response is None:
        return CaseVerdict(
            matched=False,
            intents=tuple(
                _failed_intent(intent, "Pipeline 未返回结果") for intent in case.intents
            ),
        )
    return compare_case(
        case,
        tuple(sub_query.result.rows for sub_query in response.sub_queries),
    )


def _decomposition_contract_passed(
    case: EvaluationCase,
    events: Sequence[Mapping[str, Any]],
) -> bool:
    if case.decomposition_contract.value == "any":
        return True
    observation = _decomposition_observation(events)
    if observation is None:
        return False
    decomposed, sub_question_count = observation
    if case.decomposition_contract.value == "must_preserve":
        return not decomposed and sub_question_count == 1
    if case.decomposition_contract.value == "must_split":
        return decomposed and sub_question_count >= 2
    return False


def _decomposition_observation(
    events: Sequence[Mapping[str, Any]],
) -> tuple[bool, int] | None:
    event = next(
        (
            item
            for item in events
            if item.get("component") == "decomposer"
            and item.get("stage") == "decomposition"
            and item.get("outcome") == "succeeded"
        ),
        None,
    )
    if event is None:
        return None
    decomposed = event.get("decomposed")
    count = event.get("sub_question_count")
    if type(decomposed) is not bool or not isinstance(count, int) or count < 1:
        return None
    return decomposed, count


def _failed_intent(intent: EvaluationIntent, reason: str) -> Any:
    from evaluation.comparator import IntentVerdict

    return IntentVerdict(intent.id, False, reason)


def _failure_stage(
    events: Sequence[Mapping[str, Any]],
    response: Text2CypherResponse | None,
    verdict: CaseVerdict,
    error: Exception | None,
) -> str | None:
    if response is not None and not verdict.matched:
        return "semantic"
    if response is not None:
        return None
    failed = [event for event in events if event.get("outcome") == "failed"]
    if failed:
        event = failed[-1]
        stage = str(event.get("stage", "unknown"))
        if event.get("component") == "llm":
            return f"{stage}_llm"
        return stage
    return type(error).__name__ if error is not None else "unknown"


def _response_payload(response: Text2CypherResponse | None) -> list[dict[str, Any]]:
    if response is None:
        return []
    return [
        {
            "question": sub_query.question,
            "cypher": sub_query.cypher,
            "columns": list(sub_query.result.columns),
            "rows": _jsonable_rows(sub_query.result),
            "truncated": sub_query.result.truncated,
            "duration_ms": sub_query.result.duration_ms,
        }
        for sub_query in response.sub_queries
    ]


def _jsonable_rows(result: QueryResult) -> list[dict[str, Any]]:
    return [
        {str(key): _jsonable(value) for key, value in row.items()}
        for row in result.rows
    ]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _row_fingerprint(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            for row in rows
        )
    )


def _schema_fingerprint(schema: GraphSchema) -> str:
    document = json.dumps(asdict(schema), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _metadata(
    settings: Settings,
    revision: str,
    revision_sha: str,
    runs: int,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "revision_sha": revision_sha,
        "python": sys.version,
        "runs_per_case": runs,
        "case_count": 30,
        "model": settings.llm_model,
        "database": settings.neo4j_database,
        "settings": {
            "few_shot_enabled": settings.few_shot_enabled,
            "question_decomposition_enabled": settings.question_decomposition_enabled,
            "cypher_correction_enabled": settings.cypher_correction_enabled,
            "empty_result_correction_enabled": settings.empty_result_correction_enabled,
            "retry_enabled": settings.retry_enabled,
            "retry_max_attempts": settings.retry_max_attempts,
            "llm_disable_thinking": settings.llm_disable_thinking,
        },
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, default=str) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _write_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "case_id",
        "difficulty",
        "category",
        "run",
        "duration_seconds",
        "generation_success",
        "syntax_success",
        "execution_success",
        "nonempty_success",
        "semantic_success",
        "decomposition_contract_success",
        "semantic_outcome",
        "failure_stage",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


if __name__ == "__main__":
    raise SystemExit(main())
