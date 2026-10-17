"""Fixtures of every test package of the workspace."""

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from snapdelta import plugins

type AddEntryPoint = Callable[..., None]


@pytest.fixture(autouse=True)
def fresh_plugins() -> Iterator[None]:
    """Each test reads the installed plugins again, without the plugins that another test registered."""
    plugins.reset_plugins()
    yield
    plugins.reset_plugins()


@pytest.fixture
def fake_entry_points(monkeypatch: pytest.MonkeyPatch) -> AddEntryPoint:
    """Add an entry point next to the installed ones: `add(group, name, load, package=None)`."""
    added: dict[str, list[Any]] = {}
    installed = plugins.entry_points

    def entry_points(group: str) -> list[Any]:
        return [*installed(group=group), *added.get(group, [])]

    def add(group: str, name: str, load: Callable[[], Any], package: str | None = None) -> None:
        dist = SimpleNamespace(name=package, version="1.0") if package else None
        added.setdefault(group, []).append(SimpleNamespace(name=name, load=load, value=f"fake:{name}", dist=dist))
        plugins.reset_plugins()

    monkeypatch.setattr(plugins, "entry_points", entry_points)
    return add
