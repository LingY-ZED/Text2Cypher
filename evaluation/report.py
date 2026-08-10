"""Markdown and PNG reporting for the versioned evaluation harness."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def render_report(
    output: Path,
    metadata: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any] | None,
    recovery_probes: Mapping[str, bool],
    *,
    oracle_drift: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Render a self-contained Markdown report and referenced PNG charts."""

    if oracle_drift:
        _render_drift_report(output, metadata, oracle_drift, recovery_probes)
        return
    if metrics is None:
        raise ValueError("metrics are required when Oracle drift is absent")

    charts = output / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    _render_charts(charts, records, metrics)
    diagnostics = metrics["diagnostics"]
    run_summary = (
        f"- 题目：30；每题运行：{metadata['runs_per_case']} 次；"
        f"总样本：{metrics['sample_count']}"
    )
    raw_regrade = metadata.get("regrade")
    regrade = raw_regrade if isinstance(raw_regrade, Mapping) else None
    lines = [
        (
            "# Text2Cypher Week 4 历史结果重判报告"
            if regrade is not None
            else "# Text2Cypher Week 4 完整评测报告"
        ),
        "",
        f"- 分支：`{metadata['revision']}`",
        f"- 提交：`{metadata['revision_sha']}`",
        f"- 模型：`{metadata['model']}`",
        run_summary,
        f"- Schema 指纹：`{metadata.get('schema_fingerprint', 'unknown')}`",
    ]
    if regrade is not None:
        lines.extend(
            (
                f"- 原始运行：`{regrade['source_run']}`",
                f"- 判定数据集：`{regrade['dataset_sha256']}`",
                f"- 语义正确数：{regrade['old_matched']} → "
                f"{regrade['new_matched']}",
                f"- 判定翻转：{regrade['flip_count']} 个题次",
                "- 说明：仅依据已保存的最终查询结果重新判定，"
                "未调用 LLM 或 Neo4j。",
            )
        )
    lines.extend(
        (
            "",
            "## 质量门",
            "",
            "| 指标 | 结果 | 目标 | 判定 |",
            "| --- | ---: | ---: | --- |",
        )
    )
    official = metrics["official"]
    for key, label in (
        ("generation_rate", "Cypher 生成率"),
        ("syntax_rate", "Cypher 语法正确率"),
        ("execution_rate", "Cypher 可执行率"),
        ("query_accuracy", "查询正确率"),
        ("error_recovery_rate", "自然错误恢复成功率"),
    ):
        value = official[key]
        actual = "N/A" if value["value"] is None else _percent(value["value"])
        verdict = (
            "N/A"
            if value["passed"] is None
            else "通过"
            if value["passed"]
            else "未通过"
        )
        lines.append(
            f"| {label} | {actual} | {_percent(value['target'])} | {verdict} |"
        )
    p95 = official["p95_response_seconds"]
    average_line = (
        f"| 平均响应时间 | {official['average_response_seconds']:.3f} 秒 | 只报告 | - |"
    )
    p95_verdict = "通过" if p95["passed"] else "未通过"
    p95_line = (
        f"| P95 响应时间 | {p95['value']:.3f} 秒 | "
        f"≤{p95['target']:.0f} 秒 | {p95_verdict} |"
    )
    lines.extend(
        (
            average_line,
            p95_line,
            "",
            f"**总体质量门：{'通过' if metrics['quality_gate_passed'] else '未通过'}**",
            "",
            "![质量指标与目标值](charts/quality-gates.png)",
            "",
            "图中柱形为实际百分比，菱形标记为目标值；自然恢复无样本时显示 N/A。",
            "",
            "## 三级语义结果",
            "",
            "![完全正确、部分正确与错误分布](charts/semantic-outcomes.png)",
            "",
            "该图按题次展示三级结果；正式查询正确率仍只统计完全正确。",
            "",
            "| 结果 | 题次数 | 比例 |",
            "| --- | ---: | ---: |",
        )
    )
    for outcome, label in (
        ("full", "完全正确"),
        ("partial", "部分正确"),
        ("incorrect", "错误"),
    ):
        value = diagnostics["semantic_outcomes"][outcome]
        lines.append(
            f"| {label} | {value['count']}/{value['total']} | "
            f"{_percent(value['value'])} |"
        )
    lines.extend(
        (
            "",
            f"- Intent 覆盖率：{_format_rate(diagnostics['intent_coverage'])}",
            "- 部分正确表示至少一个、但并非全部 Oracle intent 通过。",
            "",
            "## 难度与类别",
            "",
            "![难度与类别表现](charts/difficulty-and-category.png)",
            "",
            "上图比较三档难度的执行率、完整正确率和 Intent 覆盖率；"
            "下图展示各类别的完整、部分及 Intent 覆盖表现。",
            "",
            "| 难度 | 样本数 | 生成率 | 执行率 | 完整正确率 | "
            "部分正确率 | Intent 覆盖率 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for difficulty, value in metrics["diagnostics"]["by_difficulty"].items():
        generation_rate = _percent(value["generation_rate"])
        execution_rate = _percent(value["execution_rate"])
        query_accuracy = _percent(value["query_accuracy"])
        partial_rate = _percent(value["partial_rate"])
        intent_coverage = _percent(value["intent_coverage"])
        lines.append(
            f"| {difficulty} | {value['samples']} | {generation_rate} | "
            f"{execution_rate} | {query_accuracy} | {partial_rate} | "
            f"{intent_coverage} |"
        )
    initial_syntax = _format_rate(diagnostics["initial_syntax_rate"])
    initial_execution = _format_rate(diagnostics["initial_execution_rate"])
    nonempty = _format_rate(diagnostics["nonempty_result_rate"])
    corrected_semantics = _format_rate(diagnostics["semantic_after_correction"])
    probe_summary = (
        f"{metrics['recovery_probes']['passed']}/{metrics['recovery_probes']['total']}"
    )
    retry_summary = json.dumps(
        diagnostics["transport_retries"],
        ensure_ascii=False,
    )
    lines.extend(
        (
            "",
            "## 响应时间",
            "",
            "![不同难度的响应时间分布](charts/latency-distribution.png)",
            "",
            "箱线图按难度展示总 Pipeline 耗时，虚线标出全体平均值与 P95。",
            "",
            "## 错误恢复",
            "",
            "![自然恢复与确定性探针](charts/recovery-outcomes.png)",
            "",
            "左图为真实运行中的 Corrector 触发原因，右图为四类确定性恢复探针。",
            "",
            f"- 初次语法正确率：{initial_syntax}",
            f"- 初次执行成功率：{initial_execution}",
            f"- 非空结果率：{nonempty}",
            f"- 纠错样本语义正确率：{corrected_semantics}",
            f"- 确定性恢复探针：{probe_summary}",
            f"- 瞬态重试：`{retry_summary}`",
            "",
            "## 三轮稳定性",
            "",
            "| 题目 | 难度 | 语义通过 | 稳定 |",
            "| --- | --- | ---: | --- |",
        )
    )
    cases_by_id = {str(record["case_id"]): record for record in records}
    stability = metrics["diagnostics"]["stability"]["cases"]
    for case_id, value in stability.items():
        difficulty = cases_by_id[case_id]["difficulty"]
        lines.append(
            f"| `{case_id}` | {difficulty} | {value['passed']}/{value['runs']} | "
            f"{'是' if value['stable'] else '否'} |"
        )

    partial_records = [
        record for record in records if _record_outcome(record) == "partial"
    ]
    incorrect_records = [
        record for record in records if _record_outcome(record) == "incorrect"
    ]
    lines.extend(("", "## 部分正确案例", ""))
    if not partial_records:
        lines.append("没有部分正确题次。")
    else:
        for record in partial_records:
            _append_result_detail(lines, record)
    lines.extend(("", "## 错误案例", ""))
    if not incorrect_records:
        lines.append("没有完全错误题次。")
    else:
        for record in incorrect_records:
            _append_result_detail(lines, record)
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_result_detail(
    lines: list[str],
    record: Mapping[str, Any],
) -> None:
    intent_summary = "; ".join(
        _intent_summary(item) for item in record.get("intent_verdicts", ())
    )
    lines.extend(
        (
            f"### {record['case_id']} / run {record['run']}",
            "",
            f"- 阶段：`{record.get('failure_stage')}`",
            f"- 耗时：{record['duration_seconds']:.3f} 秒",
            f"- 错误：`{_error_text(record.get('error'))}`",
            "- 意图判定：" + intent_summary,
            "",
        )
    )
    for sub_query in record.get("sub_queries", ()):
        lines.extend(
            (
                f"子问题：{sub_query['question']}",
                "",
                "```cypher",
                str(sub_query["cypher"]),
                "```",
                "",
            )
        )


def _record_outcome(record: Mapping[str, Any]) -> str:
    outcome = record.get("semantic_outcome")
    if outcome in {"full", "partial", "incorrect"}:
        return str(outcome)
    verdicts = [
        item
        for item in record.get("intent_verdicts", ())
        if isinstance(item, Mapping)
    ]
    if verdicts:
        passed = sum(bool(item.get("matched")) for item in verdicts)
        if passed == len(verdicts):
            return "full"
        if passed:
            return "partial"
    return "full" if record.get("semantic_success") else "incorrect"


def _render_drift_report(
    output: Path,
    metadata: Mapping[str, Any],
    drift: Sequence[Mapping[str, Any]],
    recovery_probes: Mapping[str, bool],
) -> None:
    lines = [
        "# Text2Cypher Week 4 评测预检失败",
        "",
        f"- 分支：`{metadata['revision']}`",
        f"- 提交：`{metadata['revision_sha']}`",
        "- 原因：实时 Neo4j Oracle 与冻结快照不一致，未调用 LLM。",
        f"- 确定性恢复探针：{sum(recovery_probes.values())}/{len(recovery_probes)}",
        "",
        "## 数据漂移",
        "",
    ]
    for item in drift:
        lines.extend(
            (
                f"### {item['case_id']} / {item['intent_id']}",
                "",
                "```json",
                json.dumps(
                    {"expected": item["expected"], "actual": item["actual"]},
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
            )
        )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_charts(
    charts: Path,
    records: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> None:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager

    for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        try:
            font_manager.findfont(family, fallback_to_default=False)
        except ValueError:
            continue
        plt.rcParams["font.family"] = family
        break
    plt.rcParams["axes.unicode_minus"] = False

    _quality_chart(plt, charts / "quality-gates.png", metrics)
    _semantic_outcome_chart(plt, charts / "semantic-outcomes.png", metrics)
    _difficulty_category_chart(
        plt,
        np,
        charts / "difficulty-and-category.png",
        metrics,
    )
    _latency_chart(plt, charts / "latency-distribution.png", records, metrics)
    _recovery_chart(plt, charts / "recovery-outcomes.png", metrics)


def _semantic_outcome_chart(
    plt: Any,
    path: Path,
    metrics: Mapping[str, Any],
) -> None:
    outcomes = metrics["diagnostics"]["semantic_outcomes"]
    labels = ("完全正确", "部分正确", "错误")
    keys = ("full", "partial", "incorrect")
    values = [outcomes[key]["count"] for key in keys]
    colors = ("#15803d", "#f59e0b", "#b91c1c")
    figure, axis = plt.subplots(figsize=(9, 4.8))
    left = 0
    total = sum(values)
    for label, value, color in zip(labels, values, colors, strict=True):
        axis.barh(["全部题次"], [value], left=left, label=label, color=color)
        if value:
            axis.text(
                left + value / 2,
                0,
                f"{value}\n{value / total:.1%}",
                ha="center",
                va="center",
                color="white" if color != "#f59e0b" else "black",
                fontweight="bold",
            )
        left += value
    axis.set_xlim(0, total)
    axis.set_xlabel("题次数")
    axis.set_title("三级语义结果分布")
    axis.legend(ncols=3, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _quality_chart(plt: Any, path: Path, metrics: Mapping[str, Any]) -> None:
    official = metrics["official"]
    entries = (
        ("生成", official["generation_rate"]),
        ("语法", official["syntax_rate"]),
        ("执行", official["execution_rate"]),
        ("语义", official["query_accuracy"]),
        ("自然恢复", official["error_recovery_rate"]),
    )
    values = [
        0.0 if item[1]["value"] is None else item[1]["value"] * 100 for item in entries
    ]
    targets = [item[1]["target"] * 100 for item in entries]
    fig, axis = plt.subplots(figsize=(10, 5.5))
    bars = axis.bar([item[0] for item in entries], values, color="#3b82f6")
    axis.scatter(
        range(len(entries)), targets, marker="D", color="#b91c1c", label="目标"
    )
    axis.set_ylim(0, 110)
    axis.set_ylabel("百分比（%）")
    axis.set_title("质量指标与目标值")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    for bar, value, entry in zip(bars, values, entries, strict=True):
        label = "N/A" if entry[1]["value"] is None else f"{value:.1f}%"
        axis.text(bar.get_x() + bar.get_width() / 2, value + 2, label, ha="center")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _difficulty_category_chart(
    plt: Any,
    np: Any,
    path: Path,
    metrics: Mapping[str, Any],
) -> None:
    difficulties = metrics["diagnostics"]["by_difficulty"]
    categories = metrics["diagnostics"]["by_category"]
    figure, axes = plt.subplots(
        2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [1, 2]}
    )
    labels = list(difficulties)
    positions = np.arange(len(labels))
    width = 0.25
    execution = [difficulties[label]["execution_rate"] * 100 for label in labels]
    accuracy = [difficulties[label]["query_accuracy"] * 100 for label in labels]
    intent_coverage = [
        difficulties[label]["intent_coverage"] * 100 for label in labels
    ]
    first = axes[0].bar(
        positions - width, execution, width, label="执行率", color="#0f766e"
    )
    second = axes[0].bar(
        positions, accuracy, width, label="完整正确率", color="#f59e0b"
    )
    third = axes[0].bar(
        positions + width,
        intent_coverage,
        width,
        label="Intent 覆盖率",
        color="#2563eb",
    )
    axes[0].set_xticks(positions, labels)
    axes[0].set_ylim(0, 110)
    axes[0].set_ylabel("百分比（%）")
    axes[0].set_title("不同难度的执行与语义表现")
    axes[0].legend()
    axes[0].grid(axis="y", alpha=0.2)
    axes[0].bar_label(first, fmt="%.1f%%", padding=2)
    axes[0].bar_label(second, fmt="%.1f%%", padding=2)
    axes[0].bar_label(third, fmt="%.1f%%", padding=2)

    category_names = list(categories)
    matrix = np.array(
        [
            [categories[name][metric] * 100 for name in category_names]
            for metric in (
                "query_accuracy",
                "partial_rate",
                "incorrect_rate",
                "intent_coverage",
            )
        ]
    )
    image = axes[1].imshow(matrix, aspect="auto", vmin=0, vmax=100, cmap="YlGnBu")
    axes[1].set_xticks(
        range(len(category_names)), category_names, rotation=55, ha="right"
    )
    axes[1].set_yticks(
        range(4),
        ["完整正确率", "部分正确率", "错误率", "Intent 覆盖率"],
    )
    axes[1].set_title("类别三级语义指标热力图")
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            axes[1].text(
                column, row, f"{matrix[row, column]:.0f}", ha="center", va="center"
            )
    figure.colorbar(image, ax=axes[1], label="百分比（%）")
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _latency_chart(
    plt: Any,
    path: Path,
    records: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        grouped[str(record["difficulty"])].append(float(record["duration_seconds"]))
    labels = list(grouped)
    values = [grouped[label] for label in labels]
    figure, axis = plt.subplots(figsize=(9, 5.5))
    axis.boxplot(values, tick_labels=labels, showmeans=True)
    average = metrics["official"]["average_response_seconds"]
    p95 = metrics["official"]["p95_response_seconds"]["value"]
    axis.axhline(average, color="#2563eb", linestyle="--", label=f"平均 {average:.2f}s")
    axis.axhline(p95, color="#b91c1c", linestyle=":", label=f"P95 {p95:.2f}s")
    axis.set_ylabel("秒")
    axis.set_title("不同难度的端到端响应时间")
    axis.grid(axis="y", alpha=0.2)
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _recovery_chart(plt: Any, path: Path, metrics: Mapping[str, Any]) -> None:
    reasons = metrics["diagnostics"]["correction_reasons"]
    probes = metrics["recovery_probes"]["results"]
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    reason_names = list(reasons) or ["无自然触发"]
    reason_values = list(reasons.values()) or [0]
    bars = axes[0].bar(reason_names, reason_values, color="#7c3aed")
    axes[0].set_title("自然 Corrector 触发原因")
    axes[0].set_ylabel("次数")
    axes[0].bar_label(bars, padding=2)
    axes[0].tick_params(axis="x", rotation=25)
    probe_names = list(probes)
    probe_values = [1 if probes[name] else 0 for name in probe_names]
    probe_bars = axes[1].bar(
        probe_names,
        probe_values,
        color=["#15803d" if value else "#b91c1c" for value in probe_values],
    )
    axes[1].set_ylim(0, 1.2)
    axes[1].set_yticks([0, 1], ["失败", "通过"])
    axes[1].set_title("确定性恢复探针")
    axes[1].bar_label(
        probe_bars,
        labels=["通过" if value else "失败" for value in probe_values],
        padding=2,
    )
    axes[1].tick_params(axis="x", rotation=25)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_rate(value: Mapping[str, Any]) -> str:
    if value["value"] is None:
        return "N/A"
    return f"{_percent(value['value'])}（{value['count']}/{value['total']}）"


def _error_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return "无异常；语义不匹配"
    return f"{value.get('type')}: {value.get('message')}"


def _intent_summary(item: Mapping[str, Any]) -> str:
    verdict = "通过" if item["matched"] else "失败"
    return f"{item['intent_id']}={verdict}（{item['reason']}）"
