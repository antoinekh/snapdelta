"""The plugin registry: each extension point of snapdelta is an entry point group, and the built-ins are plugins too.

A plugin that cannot load, or that is not of the kind of its group, is kept apart as broken: the other plugins and
the commands that do not need it go on, and `snapdelta doctor` shows the error.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .errors import PluginError, SnapdeltaError


class Severity(StrEnum):
    OK = "ok"
    # The plugin works, but a part of it will not do what its author meant.
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Problem:
    severity: Severity
    message: str


def no_problems(name: str, value: Any) -> list[Problem]:
    return []


@dataclass(frozen=True)
class ExtensionPoint[T]:
    """One entry point group: what its plugins must be, and the settings table of each plugin."""

    group: str
    # The word for one plugin of the group in a message: "source", "store backend".
    kind: str
    # The interface that a plugin implements, for the docs and for `snapdelta doctor`.
    interface: str
    # The table of the settings of one plugin, `[TABLE.NAME]`; None when the plugins have no settings.
    table: str | None
    # The problem of a value loaded under a name, or None when the value is a plugin of this group.
    check: Callable[[str, Any], str | None] = field(repr=False, compare=False)
    # The deeper checks of `snapdelta doctor` on a plugin that passed `check`.
    inspect: Callable[[str, T], list[Problem]] = field(default=no_problems, repr=False, compare=False)


@dataclass(frozen=True)
class Plugin[T]:
    name: str
    value: T
    # The package that declares the entry point; None for a plugin registered in code.
    distribution: str | None = None
    version: str | None = None
    # `module:attribute` of the entry point.
    target: str | None = None


@dataclass(frozen=True)
class BrokenPlugin:
    name: str
    error: str
    distribution: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class Discovered[T]:
    plugins: dict[str, Plugin[T]]
    broken: dict[str, BrokenPlugin]


_lock = threading.Lock()
_cache: dict[str, Discovered[Any]] = {}
_registered: dict[str, dict[str, Any]] = {}


def discover[T](point: ExtensionPoint[T]) -> Discovered[T]:
    """The plugins of one group and the broken ones, read once per process; see reset_plugins()."""
    with _lock:
        if point.group not in _cache:
            _cache[point.group] = _discover(point)
        return _cache[point.group]


def plugins[T](point: ExtensionPoint[T]) -> dict[str, T]:
    """The working plugins of one group by name, sorted."""
    return {name: plugin.value for name, plugin in sorted(discover(point).plugins.items())}


def require[T](point: ExtensionPoint[T], name: str) -> T:
    """One plugin by name; the error tells a plugin that is not installed from one that is broken."""
    found = discover(point)
    if name in found.plugins:
        return found.plugins[name].value
    if name in found.broken:
        raise PluginError(f"the {point.kind} {name!r} cannot be used: {found.broken[name].error}")
    installed = ", ".join(sorted(found.plugins)) or "none"
    raise SnapdeltaError(f"no {point.kind} {name!r} is installed (installed: {installed})")


def package_of(point: ExtensionPoint[Any], name: str) -> str | None:
    """The package that gives a plugin; None for a plugin registered in code or not installed."""
    plugin = discover(point).plugins.get(name)
    return plugin.distribution if plugin is not None else None


def register_plugin[T](point: ExtensionPoint[T], name: str, value: T) -> None:
    """Add a plugin without an entry point, for tests and for code that embeds snapdelta; it wins over one."""
    problem = point.check(name, value)
    if problem is not None:
        raise PluginError(f"{point.kind} {name!r}: {problem}")
    with _lock:
        _registered.setdefault(point.group, {})[name] = value
        _cache.pop(point.group, None)


def unregister_plugin(point: ExtensionPoint[Any], name: str) -> None:
    """Take back a plugin of register_plugin(); the installed plugin of that name, if any, comes back."""
    with _lock:
        _registered.get(point.group, {}).pop(name, None)
        _cache.pop(point.group, None)


def reset_plugins() -> None:
    """Forget the plugins read and the plugins registered, so that the next read sees the installed ones again."""
    with _lock:
        _cache.clear()
        _registered.clear()


def _discover[T](point: ExtensionPoint[T]) -> Discovered[T]:
    by_name: dict[str, list[EntryPoint]] = {}
    for entry in entry_points(group=point.group):
        by_name.setdefault(entry.name, []).append(entry)
    found: dict[str, Plugin[T]] = {}
    broken: dict[str, BrokenPlugin] = {}
    for name, entries in by_name.items():
        if len(entries) > 1:
            # Neither wins: which one would load depends on the order of the installed packages.
            packages = ", ".join(sorted(_distribution(entry) or entry.value for entry in entries))
            broken[name] = BrokenPlugin(name, f"two packages give this name: {packages}; uninstall one")
            continue
        entry = entries[0]
        distribution = _distribution(entry)
        try:
            value = entry.load()
        except Exception as error:
            broken[name] = BrokenPlugin(name, f"{type(error).__name__}: {error}", distribution, entry.value)
            continue
        problem = point.check(name, value)
        if problem is not None:
            broken[name] = BrokenPlugin(name, problem, distribution, entry.value)
            continue
        version = entry.dist.version if entry.dist is not None else None
        found[name] = Plugin(name, value, distribution, version, entry.value)
    for name, value in _registered.get(point.group, {}).items():
        broken.pop(name, None)
        found[name] = Plugin(name, value)
    return Discovered(found, broken)


def _distribution(entry: EntryPoint) -> str | None:
    return entry.dist.name if entry.dist is not None else None


def subclass_check(base: type) -> Callable[[str, Any], str | None]:
    """The check of a group whose plugins are subclasses of `base`."""

    def check(name: str, value: Any) -> str | None:
        if isinstance(value, type) and issubclass(value, base):
            return None
        return f"not a {base.__module__}.{base.__qualname__} subclass: {value!r}"

    return check


def settings_problems(value: type, base: type) -> list[Problem]:
    """The problems of the `settings_class` of a plugin class, whose settings must subclass `base`."""
    chosen = getattr(value, "settings_class", None)
    if not (isinstance(chosen, type) and issubclass(chosen, base)):
        return [Problem(Severity.ERROR, f"settings_class must be a {base.__qualname__} subclass, not {chosen!r}")]
    return []


def abstract_problems(value: type) -> list[Problem]:
    """An error when a plugin class leaves abstract methods of its interface without code."""
    if not inspect.isabstract(value):
        return []
    missing = ", ".join(sorted(getattr(value, "__abstractmethods__", ())))
    return [Problem(Severity.ERROR, f"these methods of the interface have no code: {missing}")]


def extension_points() -> list[ExtensionPoint[Any]]:
    """Every extension point of snapdelta, in the order of the docs."""
    from .inventory import INVENTORIES
    from .render import RENDERERS
    from .secrets import SECRETS
    from .source import SOURCES
    from .store import STORES

    return [SOURCES, STORES, RENDERERS, INVENTORIES, SECRETS]
