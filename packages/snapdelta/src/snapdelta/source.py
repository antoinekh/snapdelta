"""The base of every snapdelta source: the metadata of one load; a plugin adds its models and its load()."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, ClassVar

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectAlreadyExists
from pydantic import BaseModel

from .activity import LogLine
from .errors import LoadError
from .inputs import InputFile
from .inventory import Host
from .plugins import (
    ExtensionPoint,
    Problem,
    Severity,
    discover,
    package_of,
    plugins,
    register_plugin,
    require,
    settings_problems,
)
from .settings import SourceSettings


class InputRef(BaseModel):
    name: str
    sha256: str


class Source(Adapter):
    """A plugin subclass declares its models as class attributes and `top_level`, as diffsync does."""

    top_level: ClassVar[list[str]] = []

    # Must equal the entry point name, because a snapshot finds its models again by this name.
    source: str = ""
    # True: load() gets the data itself from `self.target` (target access), with no input file.
    fetches: ClassVar[bool] = False
    # The typed settings that snapdelta reads for this source from the [sources.NAME] table.
    settings_class: ClassVar[type[SourceSettings]] = SourceSettings

    def __init__(
        self,
        files: Sequence[Path] = (),
        target: str | None = None,
        label: str | None = None,
        captured_at: datetime | None = None,
        settings: SourceSettings | None = None,
        host: Host | None = None,
    ) -> None:
        super().__init__(name=target)
        self.files = _unique_files(files)
        self.target = target
        self.label = label
        self.captured_at = captured_at
        self.inputs: list[InputRef] = []
        self.loaded: list[str] = []
        self.unmapped: list[str] = []
        self.warnings: list[str] = []
        # The log messages of the load; see activity.collecting().
        self.log: list[LogLine] = []
        self.settings = settings if settings is not None else self.default_settings()
        # The inventory entry of the target, when there is an inventory: address, port, login, platform, data.
        self.host = host

    @classmethod
    def default_settings(cls) -> SourceSettings:
        return cls.settings_class()

    @classmethod
    def targets(cls, settings: SourceSettings | None = None) -> list[str]:
        """The targets that a fetching source can reach, for the pickers; empty when unknown."""
        return []

    @classmethod
    def model_order(cls) -> list[str]:
        """The top-level models in order, each followed by its children."""
        order: list[str] = []

        def visit(name: str) -> None:
            if name in order:
                return
            order.append(name)
            for child in getattr(cls, name).get_children_mapping():
                visit(child)

        for name in cls.top_level:
            visit(name)
        return order

    def read_inputs(self) -> list[InputFile]:
        """The input files, in any format; also records their names and hashes."""
        if not self.files:
            raise LoadError(self.source or type(self).__name__, "no input file")
        inputs = [InputFile.from_path(path) for path in self.files]
        self.inputs.extend(InputRef(name=item.name, sha256=item.sha256) for item in inputs)
        return inputs

    def add_or_warn(self, obj: DiffSyncModel) -> bool:
        try:
            self.add(obj)
        except ObjectAlreadyExists:
            self.warnings.append(f"duplicate {obj.get_type()} {obj.get_unique_id()}: the first one is kept")
            return False
        return True

    def add_child_or_warn(self, parent_model: str, parent_id: str, child: DiffSyncModel) -> bool:
        parent = self.get_or_none(parent_model, parent_id)
        if parent is None:
            # A child without its parent is never compared, because diffsync starts from the top-level models.
            self.warnings.append(f"{child.get_type()} {child.get_unique_id()}: no {parent_model} {parent_id}, skipped")
            return False
        if not self.add_or_warn(child):
            return False
        parent.add_child(child)
        return True

    def finish_load(self, target: str | None, models: Iterable[str]) -> None:
        """The last step of a load: target name, capture time and the models that the load filled."""
        if self.captured_at is None:
            self.captured_at = datetime.now(UTC)
        self.set_target(target)
        self.set_loaded(models)

    def set_target(self, from_config: str | None) -> None:
        if self.target and from_config and self.target != from_config:
            self.warnings.append(
                f"target name {self.target!r} given, the config says {from_config!r}: {self.target!r} is kept"
            )
        self.target = self.target or from_config
        if not self.target:
            source = ", ".join(ref.name for ref in self.inputs) or self.source or type(self).__name__
            raise LoadError(source, "no target name: the files hold no config, so give the target name")
        self.name = self.target

    def set_loaded(self, models: Iterable[str]) -> None:
        wanted = set(models)
        self.loaded = [name for name in self.model_order() if name in wanted]
        # diffsync reads `top_level` from the instance, so this limits a compare to the models that both loads filled;
        # setattr because diffsync types it as a ClassVar.
        setattr(self, "top_level", [name for name in type(self).top_level if name in wanted])  # noqa: B010

    def require_loaded(self) -> tuple[str, datetime]:
        if self.target is None or self.captured_at is None:
            raise ValueError(f"{type(self).__name__} is not loaded: call load() first")
        return self.target, self.captured_at

    def versions(self) -> dict[str, str]:
        """The versions of diffsync, snapdelta and the package of the source; a registered source has no package."""
        package = package_of(SOURCES, self.source)
        names = ["diffsync", "snapdelta", *([package] if package else [])]
        return {name: _version(name) for name in names}


def _unique_files(files: Sequence[Path]) -> list[Path]:
    """The files without the repeats: a file given twice holds the same objects twice, which read as duplicates."""
    seen: set[Path] = set()
    unique = []
    for path in files:
        if path.resolve() not in seen:
            seen.add(path.resolve())
            unique.append(path)
    return unique


def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _check_source(name: str, value: Any) -> str | None:
    if not (isinstance(value, type) and issubclass(value, Source)):
        return f"not a snapdelta.source.Source subclass: {value!r}"
    if value.source != name:
        # A snapshot records `source` and finds its models again by it, so another name makes it unreadable.
        return f"its class attribute source is {value.source!r}: it must be {name!r}, the name of its entry point"
    return None


def _inspect_source(name: str, cls: type[Source]) -> list[Problem]:
    problems = settings_problems(cls, SourceSettings)
    if cls.load is Adapter.load:
        problems.append(Problem(Severity.ERROR, "load() has no code: it must fill the models of one target"))
    if not cls.top_level:
        problems.append(Problem(Severity.ERROR, "top_level is empty: a compare would have no model"))
    seen = set(cls.top_level)
    parents: list[tuple[str, str | None]] = [(model, None) for model in cls.top_level]
    while parents:
        model_name, parent = parents.pop()
        model = getattr(cls, model_name, None)
        if not (isinstance(model, type) and issubclass(model, DiffSyncModel)):
            # diffsync checks the top-level models only; a missing child fails later, at the first load.
            where = f"{parent}: its child" if parent else "top_level names"
            problems.append(Problem(Severity.ERROR, f"{where} {model_name!r}, which is not a model of the source"))
            continue
        for child in model.get_children_mapping():
            if child not in seen:
                seen.add(child)
                parents.append((child, model_name))
    return problems


SOURCES: ExtensionPoint[type[Source]] = ExtensionPoint(
    group="snapdelta.sources",
    kind="source",
    interface="snapdelta.source.Source",
    table="sources",
    check=_check_source,
    inspect=_inspect_source,
)


def register_source(cls: type[Source]) -> None:
    """Add a source without an entry point, under its `source` name, for tests and for code that embeds snapdelta."""
    register_plugin(SOURCES, cls.source, cls)


def source_classes() -> dict[str, type[Source]]:
    """The installed sources by entry point name, and the registered ones; a broken plugin is left out."""
    return plugins(SOURCES)


def broken_sources() -> dict[str, str]:
    """The sources whose plugin cannot be used, with the error."""
    return {name: broken.error for name, broken in sorted(discover(SOURCES).broken.items())}


def require_source(name: str) -> type[Source]:
    """The class of one source; the error tells a source that is not installed from one that cannot load."""
    return require(SOURCES, name)
