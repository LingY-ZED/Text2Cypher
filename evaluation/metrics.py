"""Metric aggregation and quality gates for Text2Cypher evaluation runs."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from statistics import fmean
from typing import Any

from evaluation.models import SemanticOutcome

RATE_TARGETS = {
    "generation_rate": 0.95,
    "syntax_rate": 0.90,
    "execution_rate": 0.90,
    "query_accuracy": 0.85,
}
P95_TARGET_SECONDS = 30.0
RECOVERY_TARGET = 0.80


def calculate_metrics(
    records: Sequence[Mapping[str, Any]],
    recovery_probes: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Calculate the seven official metrics and deterministic diagnostics."""

    if not records:
        raise ValueError("at least one evaluation record is required")
    total = len(records)
    metrics: dict[str, Any] = {
        "sample_count": total,
        "official": {},
    }
    for metric, field in (
        ("generation_rate", "generation_success"),
        ("syntax_rate", "syntax_success"),
        ("execution_rate", "execution_success"),
        ("query_accuracy", "semantic_success"),
    ):
        count = sum(bool(record.get(field)) for record in records)
        target = RATE_TARGETS[metric]
        metrics["official"][metric] = _rate(count, total, target)

    durations = sorted(float(record["duration_seconds"]) for record in records)
    average = fmean(durations)
    p95 = _nearest_rank(durations, 0.95)
    metrics["official"]["average_response_seconds"] = round(average, 3)
    metrics["official"]["p95_response_seconds"] = {
        "value": round(p95, 3),
        "target": P95_TARGET_SECONDS,
        "passed": p95 <= P95_TARGET_SECONDS,
    }

    recovery_events = [
        event
        for record in records
        for event in record.get("recovery_events", ())
        if isinstance(event, Mapping)
    ]
    correction_started = sum(
        event.get("event") == "cypher_correction_started" for event in recovery_events
    )
    correction_succeeded = sum(
        event.get("event") == "cypher_correction_succeeded" for event in recovery_events
    )
    natural_recovery = _optional_rate(
        correction_succeeded,
        correction_started,
        RECOVERY_TARGET,
    )
    metrics["official"]["error_recovery_rate"] = natural_recovery

    outcome_counts = Counter(_semantic_outcome(record) for record in records)
    intent_matched, intent_total = _intent_totals(records)

    metrics["diagnostics"] = {
        "semantic_outcomes": {
            outcome.value: _plain_rate(outcome_counts[outcome], total)
            for outcome in SemanticOutcome
        },
        "intent_coverage": _plain_rate(intent_matched, intent_total),
        "initial_syntax_rate": _plain_rate(
            sum(bool(record.get("initial_syntax_success")) for record in records),
            total,
        ),
        "initial_execution_rate": _plain_rate(
            sum(bool(record.get("initial_execution_success")) for record in records),
            total,
        ),
        "nonempty_result_rate": _plain_rate(
            sum(bool(record.get("nonempty_success")) for record in records),
            total,
        ),
        "semantic_after_correction": _semantic_after_correction(records),
        "by_difficulty": _group_rates(records, "difficulty"),
        "by_category": _group_rates(records, "category"),
        "stability": _stability(records),
        "failure_stages": dict(
            sorted(
                Counter(
                    str(record.get("failure_stage") or "none")
                    for record in records
                    if not record.get("semantic_success")
                ).items()
            )
        ),
        "correction_reasons": dict(
            sorted(
                Counter(
                    str(event.get("reason"))
                    for event in recovery_events
                    if event.get("event") == "cypher_correction_started"
                ).items()
            )
        ),
        "transport_retries": _transport_retries(recovery_events),
    }

    probes = dict(recovery_probes or {})
    metrics["recovery_probes"] = {
        "results": probes,
        "passed": sum(probes.values()),
        "total": len(probes),
        "gate_passed": len(probes) == 4 and all(probes.values()),
    }
    gate_values = [
        value["passed"]
        for value in metrics["official"].values()
        if isinstance(value, dict) and value.get("passed") is not None
    ]
    metrics["quality_gate_passed"] = bool(
        all(gate_values) and metrics["recovery_probes"]["gate_passed"]
    )
    return metrics


def _rate(count: int, total: int, target: float) -> dict[str, Any]:
    value = count / total
    return {
        "count": count,
        "total": total,
        "value": round(value, 6),
        "target": target,
        "passed": value >= target,
    }


def _optional_rate(count: int, total: int, target: float) -> dict[str, Any]:
    if total == 0:
        return {
            "count": count,
            "total": total,
            "value": None,
            "target": target,
            "passed": None,
        }
    return _rate(count, total, target)


def _plain_rate(count: int, total: int) -> dict[str, Any]:
    return {
        "count": count,
        "total": total,
        "value": round(count / total, 6) if total else None,
    }


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return sorted(values)[index]


def _group_rates(
    records: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record[field])].append(record)
    output: dict[str, dict[str, Any]] = {}
    for key, values in sorted(grouped.items()):
        outcomes = Counter(_semantic_outcome(value) for value in values)
        intent_matched, intent_total = _intent_totals(values)
        output[key] = {
            "samples": len(values),
            "generation_rate": _plain_rate(
                sum(bool(value.get("generation_success")) for value in values),
                len(values),
            )["value"],
            "execution_rate": _plain_rate(
                sum(bool(value.get("execution_success")) for value in values),
                len(values),
            )["value"],
            "query_accuracy": _plain_rate(
                sum(bool(value.get("semantic_success")) for value in values),
                len(values),
            )["value"],
            "full_rate": _plain_rate(
                outcomes[SemanticOutcome.FULL],
                len(values),
            )["value"],
            "partial_rate": _plain_rate(
                outcomes[SemanticOutcome.PARTIAL],
                len(values),
            )["value"],
            "incorrect_rate": _plain_rate(
                outcomes[SemanticOutcome.INCORRECT],
                len(values),
            )["value"],
            "intent_coverage": _plain_rate(intent_matched, intent_total)["value"],
        }
    return output


def _semantic_outcome(record: Mapping[str, Any]) -> SemanticOutcome:
    raw_outcome = record.get("semantic_outcome")
    try:
        return SemanticOutcome(str(raw_outcome))
    except ValueError:
        pass
    verdicts = record.get("intent_verdicts")
    if isinstance(verdicts, Sequence) and not isinstance(verdicts, (str, bytes)):
        matched = [
            bool(verdict.get("matched"))
            for verdict in verdicts
            if isinstance(verdict, Mapping)
        ]
        if matched:
            if all(matched):
                return SemanticOutcome.FULL
            if any(matched):
                return SemanticOutcome.PARTIAL
            return SemanticOutcome.INCORRECT
    return (
        SemanticOutcome.FULL
        if record.get("semantic_success")
        else SemanticOutcome.INCORRECT
    )


def _intent_totals(
    records: Sequence[Mapping[str, Any]],
) -> tuple[int, int]:
    matched = 0
    total = 0
    for record in records:
        verdicts = record.get("intent_verdicts")
        valid_verdicts = (
            [verdict for verdict in verdicts if isinstance(verdict, Mapping)]
            if isinstance(verdicts, Sequence)
            and not isinstance(verdicts, (str, bytes))
            else []
        )
        if valid_verdicts:
            matched += sum(bool(verdict.get("matched")) for verdict in valid_verdicts)
            total += len(valid_verdicts)
        else:
            matched += int(bool(record.get("semantic_success")))
            total += 1
    return matched, total


def _stability(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        by_case[str(record["case_id"])].append(bool(record.get("semantic_success")))
    distribution: Counter[str] = Counter()
    cases: dict[str, dict[str, Any]] = {}
    for case_id, values in sorted(by_case.items()):
        passed = sum(values)
        key = f"{passed}/{len(values)}"
        distribution[key] += 1
        cases[case_id] = {
            "passed": passed,
            "runs": len(values),
            "stable": passed == len(values),
        }
    return {"distribution": dict(sorted(distribution.items())), "cases": cases}


def _semantic_after_correction(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    corrected = [
        record
        for record in records
        if any(
            isinstance(event, Mapping)
            and event.get("event") == "cypher_correction_started"
            for event in record.get("recovery_events", ())
        )
    ]
    return _plain_rate(
        sum(bool(record.get("semantic_success")) for record in corrected),
        len(corrected),
    )


def _transport_retries(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scheduled = sum(event.get("event") == "retry_scheduled" for event in events)
    recovered = sum(event.get("event") == "retry_succeeded" for event in events)
    exhausted = sum(event.get("event") == "retry_exhausted" for event in events)
    return {
        "scheduled": scheduled,
        "recovered": recovered,
        "exhausted": exhausted,
    }
