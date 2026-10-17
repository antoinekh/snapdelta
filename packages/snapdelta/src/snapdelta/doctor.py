"""`snapdelta doctor`: the plugins against their interfaces, then the settings, the store and the inventory."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import catalog
from .errors import SnapdeltaError
from .plugins import ExtensionPoint, Problem, Severity, discover, extension_points
from .settings import Settings, load_settings
from .source import SOURCES, model_groups, source_classes
from .store import open_store_from
from .take import platform_source


def worst(problems: list[Problem]) -> Severity:
    severities = {problem.severity for problem in problems}
    return next((level for level in (Severity.ERROR, Severity.WARNING) if level in severities), Severity.OK)


@dataclass(frozen=True)
class PluginReport:
    group: str
    kind: str
    name: str
    package: str | None
    version: str | None
    target: str | None
    problems: list[Problem]
    # Where the settings use the plugin: "the store", "[snapshot] runner", "web.token"; empty when they do not.
    used_by: list[str] = field(default_factory=list)

    @property
    def severity(self) -> Severity:
        return worst(self.problems)


@dataclass(frozen=True)
class Check:
    """One part of the setup: what it is, where it is, and its problems."""

    subject: str
    detail: str
    problems: list[Problem] = field(default_factory=list)

    @property
    def severity(self) -> Severity:
        return worst(self.problems)


@dataclass(frozen=True)
class Diagnosis:
    plugins: list[PluginReport]
    checks: list[Check]

    def count(self, severity: Severity) -> int:
        reports = [*self.plugins, *self.checks]
        return sum(problem.severity == severity for report in reports for problem in report.problems)

    @property
    def healthy(self) -> bool:
        return self.count(Severity.ERROR) == 0


def plugin_reports(
    points: list[ExtensionPoint[Any]] | None = None, settings: Settings | None = None
) -> list[PluginReport]:
    """Every plugin of every extension point, the broken ones included, with its problems, and its use by `settings`."""
    reports = []
    for point in points or extension_points():
        found = discover(point)
        used = _in_use(point, settings) if settings is not None else {}
        for name, plugin in sorted(found.plugins.items()):
            try:
                problems = point.inspect(name, plugin.value)
            except Exception as error:
                # A check that fails on a plugin says as much about the plugin as a problem does.
                problems = [Problem(Severity.ERROR, f"the check failed: {type(error).__name__}: {error}")]
            reports.append(
                PluginReport(
                    point.group,
                    point.kind,
                    name,
                    plugin.distribution,
                    plugin.version,
                    plugin.target,
                    problems,
                    used.get(name, []),
                )
            )
        for name, broken in sorted(found.broken.items()):
            problem = Problem(Severity.ERROR, broken.error)
            reports.append(
                PluginReport(
                    point.group,
                    point.kind,
                    name,
                    broken.distribution,
                    None,
                    broken.target,
                    [problem],
                    used.get(name, []),
                )
            )
    return reports


def _in_use(point: ExtensionPoint[Any], settings: Settings) -> dict[str, list[str]]:
    try:
        return point.in_use(settings)
    except Exception:
        # The use is a help to read the list: a broken inventory or table is the error of `snapdelta doctor`.
        return {}


def model_check(reports: list[PluginReport]) -> Check:
    """The models of the sources that pass their checks: two sources that name a model the same way must agree."""
    failed = {report.name for report in reports if report.group == SOURCES.group and report.severity == Severity.ERROR}
    classes = {name: cls for name, cls in source_classes().items() if name not in failed}
    problems = [Problem(Severity.WARNING, conflict) for found in catalog(classes) for conflict in found.conflicts]
    return Check("models", f"{len(classes)} sources", problems)


def diagnose(settings_file: Path | None = None, store_option: str | None = None) -> Diagnosis:
    try:
        settings = load_settings(settings_file, store_option)
    except SnapdeltaError as error:
        reports = plugin_reports()
        checks = [model_check(reports)]
        checks.append(
            Check("settings", str(settings_file or "the settings file"), [Problem(Severity.ERROR, str(error))])
        )
        return Diagnosis(reports, checks)
    reports = plugin_reports(settings=settings)
    checks = [model_check(reports)]
    where = str(settings.file) if settings.file else "none, defaults"
    checks.append(Check("settings", where, [Problem(Severity.WARNING, warning) for warning in settings.warnings]))
    checks.append(_store_check(settings))
    checks.append(_inventory_check(settings))
    names = ", ".join(sorted(settings.notifiers)) or "none"
    checks.append(Check("notifiers", names))
    return Diagnosis(reports, checks)


def _store_check(settings: Settings) -> Check:
    location = settings.store.location or ""
    try:
        store = open_store_from(settings)
        try:
            info = store.info()
        finally:
            store.close()
    except (SnapdeltaError, OSError, sqlite3.Error) as error:
        return Check("store", location, [Problem(Severity.ERROR, f"{type(error).__name__}: {error}")])
    detail = (
        f"{info.backend}: {info.location}, {info.snapshots} snapshots of {info.targets} targets, {info.outputs} diffs"
    )
    return Check("store", detail)


def _inventory_check(settings: Settings) -> Check:
    if not settings.inventory.location:
        return Check("inventory", "none")
    where = f"{settings.inventory.format}: {settings.inventory.location}"
    try:
        inventory = settings.hosts()
    except SnapdeltaError as error:
        return Check("inventory", where, [Problem(Severity.ERROR, str(error))])
    if inventory is None:
        return Check("inventory", "none")
    problems = [Problem(Severity.WARNING, warning) for warning in inventory.warnings]
    lost = [
        f"{name} ({host.platform or 'no platform'})"
        for name, host in sorted(inventory.hosts.items())
        if platform_source(settings, host.platform) is None
    ]
    if lost:
        problems.append(
            Problem(
                Severity.WARNING, f"no source for the platform of {', '.join(lost)}: map it in [inventory.platforms]"
            )
        )
    for name, cls in sorted(source_classes().items()):
        for model in cls.top_level:
            unknown = [group for group in model_groups(getattr(cls, model)) if group not in inventory.groups]
            if unknown:
                message = (
                    f"source {name}: the model {model} is for groups that the inventory has not: {', '.join(unknown)}"
                )
                problems.append(Problem(Severity.WARNING, message))
    return Check("inventory", f"{where}, {len(inventory.hosts)} hosts", problems)
