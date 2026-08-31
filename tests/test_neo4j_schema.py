from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j.exceptions import ClientError, ServiceUnavailable

from text2cypher.components.retry import RetryPolicy
from text2cypher.infrastructure.neo4j.schema_fetcher import Neo4jSchemaFetcher


@dataclass
class FakeResult:
    """模拟官方 Driver 的 execute_query 返回值。"""

    records: list[dict[str, Any]]


@dataclass
class FakeSchemaNode:
    """模拟 Schema 可视化过程中的虚拟节点。"""

    name: str

    def get(self, key: str) -> str | None:
        return self.name if key == "name" else None


@dataclass
class FakeSchemaRelationship:
    """模拟 Schema 可视化过程中的虚拟关系。"""

    start_node: FakeSchemaNode
    type: str
    end_node: FakeSchemaNode


class FakeSchemaDriver:
    """按内置过程名称返回固定 Schema 记录的测试驱动。"""

    def __init__(
        self,
        *,
        visualization_error: Exception | None = None,
        label_failures: int = 0,
        observed_patterns: list[dict[str, Any]] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._visualization_error = visualization_error
        self._label_failures = label_failures
        self._observed_patterns = observed_patterns

    def execute_query(self, query: Any, **kwargs: Any) -> FakeResult:
        del kwargs
        query_text = str(query)
        self.calls.append(query_text)
        if "UNWIND labels(node)" in query_text:
            if self._label_failures:
                self._label_failures -= 1
                raise ServiceUnavailable("短暂不可用")
            return FakeResult(records=[{"label": "微服务"}, {"label": "接口"}])
        if "nodeTypeProperties" in query_text:
            return FakeResult(
                records=[
                    {
                        "nodeLabels": [f":{chr(96)}微服务{chr(96)}"],
                        "propertyName": "服务名称",
                        "propertyTypes": ["STRING"],
                        "mandatory": True,
                    },
                    {
                        "nodeLabels": [":`历史空标签`"],
                        "propertyName": "旧属性",
                        "propertyTypes": ["STRING"],
                        "mandatory": False,
                    },
                ]
            )
        if "relTypeProperties" in query_text:
            return FakeResult(
                records=[
                    {
                        "relType": ":`历史空关系`",
                        "propertyName": "旧属性",
                        "propertyTypes": ["STRING"],
                        "mandatory": False,
                    }
                ]
            )
        if "schema.visualization" in query_text:
            if self._visualization_error is not None:
                raise self._visualization_error
            return FakeResult(
                records=[
                    {
                        "relationships": [
                            FakeSchemaRelationship(
                                start_node=FakeSchemaNode(":微服务"),
                                type="调用",
                                end_node=FakeSchemaNode(":接口"),
                            )
                        ]
                    }
                ]
            )
        if "MATCH (start_node)" in query_text:
            if self._observed_patterns is not None:
                return FakeResult(records=self._observed_patterns)
            return FakeResult(
                records=[
                    {
                        "start_labels": ["微服务"],
                        "relationship_type": "调用",
                        "end_labels": ["接口"],
                    }
                ]
            )
        raise AssertionError("收到了未预期的查询")


def test_schema_fetcher_normalizes_and_sorts_schema_from_builtin_procedures() -> None:
    driver = FakeSchemaDriver()

    schema = Neo4jSchemaFetcher(driver, "neo4j", 5).fetch()

    assert [node.name for node in schema.nodes] == ["微服务", "接口"]
    assert schema.nodes[0].properties[0].name == "服务名称"
    assert schema.nodes[0].properties[0].types == ("STRING",)
    assert schema.nodes[0].properties[0].mandatory is True
    assert [relationship.name for relationship in schema.relationships] == ["调用"]
    assert schema.patterns[0].start_labels == ("微服务",)
    assert schema.patterns[0].relationship_type == "调用"
    assert schema.patterns[0].end_labels == ("接口",)
    assert any("MATCH (start_node)" in call for call in driver.calls)
    assert "历史空标签" not in {node.name for node in schema.nodes}
    assert "历史空关系" not in {
        relationship.name for relationship in schema.relationships
    }


def test_schema_fetcher_uses_fallback_when_visualization_procedure_fails() -> None:
    driver = FakeSchemaDriver(
        visualization_error=ClientError("过程不支持"),
    )
    waits: list[float] = []

    schema = Neo4jSchemaFetcher(
        driver,
        "neo4j",
        5,
        sleep_func=waits.append,
    ).fetch()

    assert schema.patterns[0].relationship_type == "调用"
    assert any("MATCH (start_node)" in call for call in driver.calls)
    assert waits == []


def test_schema_fetcher_retries_transient_schema_query() -> None:
    driver = FakeSchemaDriver(label_failures=1)
    waits: list[float] = []

    schema = Neo4jSchemaFetcher(
        driver,
        "neo4j",
        5,
        retry_policy=RetryPolicy(),
        sleep_func=waits.append,
    ).fetch()

    assert [node.name for node in schema.nodes] == ["微服务", "接口"]
    assert sum("UNWIND labels(node)" in call for call in driver.calls) == 2
    assert waits == [0.5]


def test_schema_fetcher_keeps_successfully_observed_empty_graph_empty() -> None:
    driver = FakeSchemaDriver(observed_patterns=[])

    schema = Neo4jSchemaFetcher(driver, "neo4j", 5).fetch()

    assert [node.name for node in schema.nodes] == ["微服务", "接口"]
    assert schema.relationships == ()
    assert schema.patterns == ()
