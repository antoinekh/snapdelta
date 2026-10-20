import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from snapdelta.source import Source
from snapdelta_example import ExampleSource


def device_data(
    target: str, description: str, primary: bool, extra_interface: bool, with_config: bool, with_state: bool
) -> dict[str, Any]:
    data: dict[str, Any] = {"device": target}
    if with_config:
        interfaces: list[dict[str, Any]] = [
            {
                "name": "eth0",
                "description": description,
                "mtu": 1500,
                "addresses": [{"prefix": "10.0.0.1/30", "primary": primary}],
            },
            {"name": "eth1", "mtu": 9000},
        ]
        if extra_interface:
            interfaces.append({"name": "eth2"})
        data["interfaces"] = interfaces
        data["vlans"] = [{"id": 10, "name": "users"}]
    if with_state:
        data["state"] = {"eth0": "up"}
    return data


@pytest.fixture
def make_adapter(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., Source]:
    """A loaded example source, from a JSON file written for the test."""

    def make(
        with_config: bool = True,
        with_state: bool = False,
        description: str = "to R2",
        primary: bool = True,
        extra_interface: bool = False,
        target: str = "R1",
        label: str | None = None,
        captured_at: datetime = datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
        ticket: str | None = None,
    ) -> Source:
        path = tmp_path_factory.mktemp("input") / f"{target}.json"
        data = device_data(target, description, primary, extra_interface, with_config, with_state)
        path.write_text(json.dumps(data))
        adapter = ExampleSource(files=[path], label=label, ticket=ticket, captured_at=captured_at)
        adapter.load()
        return adapter

    return make


@pytest.fixture(params=["file", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path):
    """Each store test runs on both backends."""
    from snapdelta.store import FileStore, SqliteStore

    return FileStore(tmp_path / "store") if request.param == "file" else SqliteStore(tmp_path / "store.db")


@pytest.fixture(autouse=True)
def no_machine_settings(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings files of the machine never change a test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.delenv("SNAPDELTA_SETTINGS", raising=False)
