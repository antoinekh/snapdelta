"""What a user or a support request needs to know: the versions, the plugins and the project links."""

from __future__ import annotations

import platform
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version

from .plugins import discover, extension_points

PROJECT_URL = "https://github.com/antoinekh/snapdelta"
DOCS_URL = f"{PROJECT_URL}/tree/main/docs"
CHANGELOG_URL = f"{PROJECT_URL}/blob/main/CHANGELOG.md"
ISSUES_URL = f"{PROJECT_URL}/issues"


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    # What a package gives, one plugin each: "source example", "store backend file".
    plugins: list[str] = field(default_factory=list)


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
    """The packages that give plugins, snapdelta and its built-ins included, with the plugins of each."""
    by_package: dict[str, list[str]] = {}
    for point in extension_points():
        found = discover(point)
        for name, plugin in found.plugins.items():
            by_package.setdefault(plugin.distribution or "registered in code", []).append(f"{point.kind} {name}")
        for name, broken in found.broken.items():
            package = broken.distribution or "unknown package"
            by_package.setdefault(package, []).append(f"{point.kind} {name} (cannot load)")
    return [Component(name, package_version(name), sorted(given)) for name, given in sorted(by_package.items())]


def version_line() -> str:
    core, diffsync = package_version("snapdelta"), package_version("diffsync")
    return f"snapdelta {core} (diffsync {diffsync}, Python {platform.python_version()})"
