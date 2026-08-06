from __future__ import annotations

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from text2cypher.components.retry import RetryPolicy
from text2cypher.config import Settings
from text2cypher.domain.errors import Neo4jConnectionError
from text2cypher.infrastructure.neo4j.driver import Neo4jDriverProvider


class FakeDriver:
    """模拟可验证并关闭的 Neo4j Driver。"""

    def __init__(self, *, failures: list[Exception] | None = None) -> None:
        self._failures = list(failures or [])
        self.closed = False
        self.verification_count = 0

    def verify_connectivity(self) -> None:
        self.verification_count += 1
        if self._failures:
            raise self._failures.pop(0)

    def close(self) -> None:
        self.closed = True


def _settings() -> Settings:
    return Settings(
        neo4j_uri="bolt://数据库.example:7687",
        neo4j_username="neo4j",
        neo4j_password="测试密码",
        llm_base_url="https://模型服务.example/v1",
        llm_api_key="测试密钥",
        llm_model="测试模型",
    )


def test_driver_provider_reuses_verified_driver_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_driver = FakeDriver()
    creation_count = 0

    def create_driver(*args: object, **kwargs: object) -> FakeDriver:
        nonlocal creation_count
        del args, kwargs
        creation_count += 1
        return fake_driver

    monkeypatch.setattr(
        "text2cypher.infrastructure.neo4j.driver.GraphDatabase.driver",
        create_driver,
    )
    provider = Neo4jDriverProvider(_settings())

    assert provider.driver is fake_driver
    assert provider.driver is fake_driver
    assert creation_count == 1
    assert fake_driver.verification_count == 1
    provider.close()
    assert fake_driver.closed is True


def test_driver_provider_closes_driver_when_connectivity_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_driver = FakeDriver(
        failures=[
            ServiceUnavailable("连接失败"),
            ServiceUnavailable("连接失败"),
            ServiceUnavailable("连接失败"),
        ]
    )
    monkeypatch.setattr(
        "text2cypher.infrastructure.neo4j.driver.GraphDatabase.driver",
        lambda *args, **kwargs: fake_driver,
    )

    with pytest.raises(Neo4jConnectionError, match="无法连接"):
        _ = Neo4jDriverProvider(
            _settings(),
            sleep_func=lambda _: None,
        ).driver

    assert fake_driver.closed is True
    assert fake_driver.verification_count == 3


def test_driver_provider_retries_transient_connectivity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_driver = FakeDriver(
        failures=[ServiceUnavailable("短暂故障"), ServiceUnavailable("短暂故障")]
    )
    waits: list[float] = []
    received_kwargs: dict[str, object] = {}

    def create_driver(*args: object, **kwargs: object) -> FakeDriver:
        del args
        received_kwargs.update(kwargs)
        return fake_driver

    monkeypatch.setattr(
        "text2cypher.infrastructure.neo4j.driver.GraphDatabase.driver",
        create_driver,
    )

    provider = Neo4jDriverProvider(
        _settings(),
        retry_policy=RetryPolicy(),
        sleep_func=waits.append,
    )

    assert provider.driver is fake_driver
    assert fake_driver.verification_count == 3
    assert waits == [0.5, 1.0]
    assert received_kwargs["max_transaction_retry_time"] == 0


def test_driver_provider_does_not_retry_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_driver = FakeDriver(failures=[AuthError("认证失败")])
    monkeypatch.setattr(
        "text2cypher.infrastructure.neo4j.driver.GraphDatabase.driver",
        lambda *args, **kwargs: fake_driver,
    )

    with pytest.raises(Neo4jConnectionError, match="无法连接"):
        _ = Neo4jDriverProvider(
            _settings(),
            sleep_func=lambda _: pytest.fail("认证错误不应重试"),
        ).driver

    assert fake_driver.verification_count == 1
