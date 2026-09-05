"""Regression coverage for evaluation provenance metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import run
from evaluation.report import _provenance_lines
from text2cypher.config import Settings


def _settings() -> Settings:
    return Settings(
        neo4j_uri="bolt://localhost:7687",
        neo4j_username="neo4j",
        neo4j_password="secret",
        llm_base_url="https://example.test/v1",
        llm_api_key="key",
        llm_model="demo-model",
    )


def _resource_tree(root: Path) -> None:
    for name in run._RESOURCE_FILES:
        (root / name).write_text(f"{name}\n", encoding="utf-8")
    rules = root / run._BUSINESS_RULES_DIRECTORY
    rules.mkdir()
    (rules / "zeta.md").write_text("zeta\n", encoding="utf-8")
    (rules / "alpha.md").write_text("alpha\n", encoding="utf-8")


def test_dataset_provenance_uses_exact_bytes_and_declared_version(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.json"
    content = b'{"version": 5, "cases": []}\n'
    dataset.write_bytes(content)

    provenance = run._dataset_provenance(dataset)

    assert provenance == {
        "path": "cases.json",
        "version": 5,
        "sha256": "fe52eee0038f708ee0e621850ee4e419931f74ace0a675581277d95655e686c2",
    }


def test_dataset_provenance_rejects_missing_or_unversioned_documents(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="cannot be loaded"):
        run._dataset_provenance(tmp_path / "missing.json")

    unversioned = tmp_path / "unversioned.json"
    unversioned.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="integer version"):
        run._dataset_provenance(unversioned)


def test_query_resource_provenance_is_sorted_and_changes_with_content(
    tmp_path: Path,
) -> None:
    _resource_tree(tmp_path)

    first = run._query_resource_provenance(tmp_path)
    repeat = run._query_resource_provenance(tmp_path)
    paths = [entry["path"] for entry in first["files"]]

    assert first == repeat
    assert paths == sorted(paths)
    assert paths == [
        "code_graph_business_rules/alpha.md",
        "code_graph_business_rules/zeta.md",
        "few_shot_examples.json",
        "primary_agent_semantic_capabilities.md",
        "query_shape_templates.json",
    ]

    (tmp_path / "few_shot_examples.json").write_text("changed\n", encoding="utf-8")

    assert run._query_resource_provenance(tmp_path)["sha256"] != first["sha256"]


def test_query_resource_provenance_rejects_missing_required_assets(
    tmp_path: Path,
) -> None:
    _resource_tree(tmp_path)
    (tmp_path / "query_shape_templates.json").unlink()

    with pytest.raises(ValueError, match="query_shape_templates.json"):
        run._query_resource_provenance(tmp_path)


def test_metadata_records_safe_reproducibility_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text('{"version": 5, "cases": []}\n', encoding="utf-8")
    monkeypatch.setattr(run, "_evaluator_revision_sha", lambda: "evaluator-sha")
    monkeypatch.setattr(
        run,
        "_query_resource_provenance",
        lambda: {"sha256": "resource-sha", "files": []},
    )

    metadata = run._metadata(_settings(), "revision", "production-sha", 3, 40, dataset)
    serialized = json.dumps(metadata, ensure_ascii=False)

    assert metadata["evaluator_revision_sha"] == "evaluator-sha"
    assert metadata["dataset"] == {
        "path": "cases.json",
        "version": 5,
        "sha256": run._dataset_provenance(dataset)["sha256"],
    }
    assert metadata["query_resources"] == {"sha256": "resource-sha", "files": []}
    assert "secret" not in serialized
    assert '"key"' not in serialized


def test_report_provenance_keeps_historical_metadata_readable() -> None:
    assert _provenance_lines({}) == (
        "- 评测器提交：`unknown`",
        "- 数据集：vunknown (`unknown`)",
        "- 查询资源指纹：`unknown`",
    )

    assert _provenance_lines(
        {
            "evaluator_revision_sha": "evaluator-sha",
            "dataset": {"version": 5, "sha256": "dataset-sha"},
            "query_resources": {"sha256": "resource-sha"},
        }
    ) == (
        "- 评测器提交：`evaluator-sha`",
        "- 数据集：v5 (`dataset-sha`)",
        "- 查询资源指纹：`resource-sha`",
    )
