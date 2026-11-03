from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from mintrans_stats.config import Settings
from mintrans_stats.http import MintransClient

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / "html" / name).read_text(encoding="utf-8")


class FakeTime:
    """Часы и sleep без реального ожидания: sleep двигает часы вперёд."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", delay_s=1.0, max_attempts=3, backoff_base_s=2.0)


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def make_client(
    settings: Settings, fake_time: FakeTime
) -> Callable[[Callable[[httpx.Request], httpx.Response]], MintransClient]:
    """Клиент поверх ``httpx.MockTransport`` с фиктивным временем."""
    clients: list[MintransClient] = []

    def factory(handler, *, client_settings: Settings | None = None) -> MintransClient:
        client = MintransClient(
            client_settings or settings,
            transport=httpx.MockTransport(handler),
            sleep=fake_time.sleep,
            clock=fake_time.clock,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()
