from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from text2cypher.domain.errors import FewShotLibraryError
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader


def _example_payload(identifier: str = "example-01") -> dict[str, Any]:
    return {
        "id": identifier,
        "category": "属性过滤",
        "question": "查询名称为 alpha 的 A",
        "cypher": "MATCH (a:A) WHERE a.name = 'alpha' RETURN a.name",
        "aliases": ["按名称查找 A"],
        "tags": ["查询", "属性过滤"],
        "schema_requirements": {
            "node_labels": ["A", "B"],
            "relationship_types": ["R"],
            "node_properties": {"A": ["name"]},
            "relationship_properties": {"R": ["kind"]},
            "patterns": [
                {
                    "start_labels": ["A"],
                    "relationship_type": "R",
                    "end_labels": ["B"],
                }
            ],
        },
    }


def _write_library(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_loader_builds_immutable_domain_examples(tmp_path: Path) -> None:
    library_path = tmp_path / "examples.json"
    _write_library(library_path, [_example_payload()])

    examples = JsonFewShotExampleLoader(library_path).load()

    assert len(examples) == 1
    example = examples[0]
    assert example.id == "example-01"
    assert example.aliases == ("按名称查找 A",)
    assert example.schema_requirements.node_properties["A"] == ("name",)
    assert example.schema_requirements.patterns[0].start_labels == ("A",)
    assert example.schema_requirements.patterns[0].relationship_type == "R"
    assert example.schema_requirements.patterns[0].end_labels == ("B",)
    with pytest.raises(TypeError):
        example.schema_requirements.node_properties["A"] = ("other",)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        example.question = "不能修改"  # type: ignore[misc]


def test_loader_allows_call_as_relationship_variable_name(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    payload["cypher"] = "MATCH (a:A)-[call:R]->(b:B) RETURN call.kind"
    _write_library(library_path, [payload])

    examples = JsonFewShotExampleLoader(library_path).load()

    assert examples[0].cypher == payload["cypher"]


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    library_path = tmp_path / "examples.json"
    _write_library(
        library_path,
        [_example_payload(), _example_payload()],
    )

    with pytest.raises(FewShotLibraryError, match="ID 不能重复"):
        JsonFewShotExampleLoader(library_path).load()


@pytest.mark.parametrize("missing_field", ["question", "cypher", "aliases"])
def test_loader_rejects_missing_fields(
    tmp_path: Path,
    missing_field: str,
) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    del payload[missing_field]
    _write_library(library_path, [payload])

    with pytest.raises(FewShotLibraryError, match="字段集合不合法"):
        JsonFewShotExampleLoader(library_path).load()


@pytest.mark.parametrize(
    "cypher",
    [
        "",
        "CREATE (:A)",
        "MATCH (a:A) DELETE a RETURN a",
        "MATCH (a:A) RETURN a; MATCH (b:B) RETURN b",
        "CALL db.labels()",
    ],
)
def test_loader_rejects_empty_or_unsafe_cypher(
    tmp_path: Path,
    cypher: str,
) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    payload["cypher"] = cypher
    _write_library(library_path, [payload])

    with pytest.raises(FewShotLibraryError):
        JsonFewShotExampleLoader(library_path).load()


def test_loader_rejects_blank_property_owner(tmp_path: Path) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    requirements = payload["schema_requirements"]
    assert isinstance(requirements, dict)
    requirements["node_properties"] = {" ": ["name"]}
    _write_library(library_path, [payload])

    with pytest.raises(FewShotLibraryError, match="键必须是非空文本"):
        JsonFewShotExampleLoader(library_path).load()


@pytest.mark.parametrize(
    ("requirements_field", "invalid_value", "error"),
    [
        ("node_properties", {"Missing": ["name"]}, "未声明的节点标签"),
        (
            "relationship_properties",
            {"MISSING": ["kind"]},
            "未声明的关系类型",
        ),
    ],
)
def test_loader_rejects_property_owner_not_declared_in_requirements(
    tmp_path: Path,
    requirements_field: str,
    invalid_value: object,
    error: str,
) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    requirements = payload["schema_requirements"]
    assert isinstance(requirements, dict)
    requirements[requirements_field] = invalid_value
    _write_library(library_path, [payload])

    with pytest.raises(FewShotLibraryError, match=error):
        JsonFewShotExampleLoader(library_path).load()


def test_loader_rejects_pattern_elements_not_declared_in_requirements(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    requirements = payload["schema_requirements"]
    assert isinstance(requirements, dict)
    patterns = requirements["patterns"]
    assert isinstance(patterns, list)
    pattern = patterns[0]
    assert isinstance(pattern, dict)
    pattern["end_labels"] = ["Missing"]
    _write_library(library_path, [payload])

    with pytest.raises(FewShotLibraryError, match="未声明的 Schema 元素"):
        JsonFewShotExampleLoader(library_path).load()


def test_loader_rejects_duplicate_patterns(tmp_path: Path) -> None:
    library_path = tmp_path / "examples.json"
    payload = _example_payload()
    requirements = payload["schema_requirements"]
    assert isinstance(requirements, dict)
    patterns = requirements["patterns"]
    assert isinstance(patterns, list)
    patterns.append(dict(patterns[0]))
    _write_library(library_path, [payload])

    with pytest.raises(FewShotLibraryError, match="patterns 不能包含重复值"):
        JsonFewShotExampleLoader(library_path).load()


@pytest.mark.parametrize("payload", [None, {}, [], "invalid"])
def test_loader_rejects_non_array_or_empty_library(
    tmp_path: Path,
    payload: object,
) -> None:
    library_path = tmp_path / "examples.json"
    _write_library(library_path, payload)

    with pytest.raises(FewShotLibraryError, match="必须是非空数组"):
        JsonFewShotExampleLoader(library_path).load()


def test_loader_reports_invalid_json_without_leaking_path(tmp_path: Path) -> None:
    library_path = tmp_path / "secret-location.json"
    library_path.write_text("{", encoding="utf-8")

    with pytest.raises(FewShotLibraryError, match="不是有效 JSON") as error:
        JsonFewShotExampleLoader(library_path).load()

    assert str(library_path) not in str(error.value)


def test_loader_rejects_missing_external_library(tmp_path: Path) -> None:
    library_path = tmp_path / "missing.json"

    with pytest.raises(FewShotLibraryError, match="无法读取"):
        JsonFewShotExampleLoader(library_path).load()
