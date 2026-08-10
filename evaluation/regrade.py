"""Recompute semantic verdicts for an existing evaluation run without I/O calls."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.comparator import compare_case
from evaluation.dataset import DEFAULT_CASES_PATH, load_cases
from evaluation.metrics import calculate_metrics
from evaluation.models import EvaluationCase
from evaluation.report import render_report


def regrade_records(
    cases: Sequence[EvaluationCase],
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rejudge stored result rows while preserving every observed Pipeline event."""

    cases_by_id = {case.id: case for case in cases}
    if len(cases_by_id) != len(cases):
        raise ValueError("evaluation cases contain duplicate IDs")

    regraded: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    for record in records:
        case_id = str(record.get("case_id", ""))
        case = cases_by_id.get(case_id)
        if case is None:
            raise ValueError(f"unknown evaluation case in result records: {case_id}")
        verdict = compare_case(case, _result_sets(record))
        old_success = bool(record.get("semantic_success"))
        updated = dict(record)
        updated["semantic_success"] = verdict.matched
        updated["semantic_outcome"] = verdict.semantic_outcome.value
        updated["intent_verdicts"] = [asdict(item) for item in verdict.intents]
        updated["failure_stage"] = _regraded_failure_stage(updated, verdict.matched)
        regraded.append(updated)
        if old_success != verdict.matched:
            flips.append(
                {
                    "case_id": case_id,
                    "run": record.get("run"),
                    "from": old_success,
                    "to": verdict.matched,
                    "old_intent_verdicts": record.get("intent_verdicts", []),
                    "new_intent_verdicts": updated["intent_verdicts"],
                }
            )

    old_matched = sum(bool(record.get("semantic_success")) for record in records)
    new_matched = sum(bool(record["semantic_success"]) for record in regraded)
    outcome_counts = {
        outcome: sum(record["semantic_outcome"] == outcome for record in regraded)
        for outcome in ("full", "partial", "incorrect")
    }
    intent_total = sum(len(record["intent_verdicts"]) for record in regraded)
    intent_matched = sum(
        bool(intent.get("matched"))
        for record in regraded
        for intent in record["intent_verdicts"]
    )
    summary = {
        "sample_count": len(records),
        "old_matched": old_matched,
        "new_matched": new_matched,
        "remaining_failures": len(records) - new_matched,
        "flip_count": len(flips),
        "semantic_outcomes": outcome_counts,
        "intent_coverage": {
            "matched": intent_matched,
            "total": intent_total,
            "value": intent_matched / intent_total if intent_total else None,
        },
        "flips": flips,
    }
    return regraded, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args(argv)

    source = args.input.resolve()
    output = (
        args.output.resolve()
        if args.output is not None
        else source.with_name(f"{source.name}-regraded")
    )
    if source == output:
        parser.error("--output must differ from --input")
    if output.exists() and any(output.iterdir()):
        parser.error(f"regrade output directory is not empty: {output}")

    metadata = _read_object(source / "metadata.json")
    source_metrics = _read_object(source / "metrics.json")
    records = _read_jsonl(source / "case-results.jsonl")
    cases = load_cases(args.cases)
    regraded, summary = regrade_records(cases, records)

    output.mkdir(parents=True, exist_ok=True)
    probes = _recovery_probes(source_metrics)
    regrade_metadata = dict(metadata)
    regrade_metadata["regrade"] = {
        "source_run": source.name,
        "regraded_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "old_matched": summary["old_matched"],
        "new_matched": summary["new_matched"],
        "flip_count": summary["flip_count"],
    }
    metrics = calculate_metrics(regraded, probes)

    _write_json(output / "metadata.json", regrade_metadata)
    _write_json(output / "regrade-summary.json", summary)
    _write_jsonl(output / "case-results.jsonl", regraded)
    _write_csv(output / "case-results.csv", regraded)
    _write_json(output / "metrics.json", metrics)
    render_report(output, regrade_metadata, regraded, metrics, probes)
    return 0


def _result_sets(
    record: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    sub_queries = record.get("sub_queries", ())
    if not isinstance(sub_queries, Sequence) or isinstance(sub_queries, str):
        raise ValueError("record sub_queries must be a sequence")
    result_sets: list[tuple[Mapping[str, Any], ...]] = []
    for sub_query in sub_queries:
        if not isinstance(sub_query, Mapping):
            raise ValueError("sub_query must be an object")
        rows = sub_query.get("rows", ())
        if not isinstance(rows, Sequence) or isinstance(rows, str):
            raise ValueError("sub_query rows must be a sequence")
        if not all(isinstance(row, Mapping) for row in rows):
            raise ValueError("sub_query rows must contain objects")
        result_sets.append(tuple(rows))
    return tuple(result_sets)


def _regraded_failure_stage(record: Mapping[str, Any], matched: bool) -> str | None:
    stage = record.get("failure_stage")
    if matched:
        return None if stage == "semantic" else str(stage) if stage else None
    if record.get("execution_success") and record.get("sub_queries"):
        return "semantic"
    return str(stage) if stage else None


def _recovery_probes(metrics: Mapping[str, Any]) -> dict[str, bool]:
    probe_section = metrics.get("recovery_probes", {})
    if not isinstance(probe_section, Mapping):
        return {}
    results = probe_section.get("results", {})
    if not isinstance(results, Mapping):
        return {}
    return {str(name): bool(value) for name, value in results.items()}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"record {line_number} is not a JSON object")
        records.append(value)
    if not records:
        raise ValueError("evaluation result file is empty")
    return records


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
        "semantic_outcome",
        "failure_stage",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


if __name__ == "__main__":
    raise SystemExit(main())
