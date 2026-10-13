"""What a user or a support request needs to know: the versions, the plugins and the project links."""

from __future__ import annotations

import platform
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version

from .source import broken_sources, source_classes

PROJECT_URL = "https://github.com/antoinekh/snapdelta"
DOCS_URL = f"{PROJECT_URL}/tree/main/docs"
CHANGELOG_URL = f"{PROJECT_URL}/blob/main/CHANGELOG.md"
ISSUES_URL = f"{PROJECT_URL}/issues"


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    sources: list[str] = field(default_factory=list)


def package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def components(extra: Iterable[str] = ()) -> list[Component]:
    """snapdelta, the other given packages (the web UI), diffsync and Python."""
    names = ["snapdelta", *extra, "diffsync"]
    return [*(Component(name, package_version(name)) for name in names), Component("Python", platform.python_version())]


def plugins() -> list[Component]:
    """The installed plugins with their sources; a source with no distribution is listed under its own name."""
    by_plugin: dict[str, list[str]] = {}
    for name, cls in sorted(source_classes().items()):
        by_plugin.setdefault(cls.distribution or name, []).append(name)
    for name in sorted(broken_sources()):
        by_plugin.setdefault(f"{name} (cannot load)", []).append(name)
    return [Component(plugin, package_version(plugin), names) for plugin, names in sorted(by_plugin.items())]


def version_line() -> str:
    core, diffsync = package_version("snapdelta"), package_version("diffsync")
    return f"snapdelta {core} (diffsync {diffsync}, Python {platform.python_version()})"
