"""The base of every snapdelta source: the metadata of one load; a plugin adds its models and its load()."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, entry_points, version
from pathlib import Path
from typing import ClassVar

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectAlreadyExists
from pydantic import BaseModel

from .activity import LogLine
from .errors import LoadError, SnapdeltaError
from .inputs import InputFile
from .inventory import Host
from .settings import SourceSettings

SOURCE_GROUP = "snapdelta.sources"

_REGISTERED: dict[str, type[Source]] = {}


class InputRef(BaseModel):
    name: str
    sha256: str


class Source(Adapter):
    """A plugin subclass declares its models as class attributes and `top_level`, as diffsync does."""

    top_level: ClassVar[list[str]] = []

    # Must equal the entry point name, because a snapshot finds its models again by this name.
    source: str = ""
    distribution: ClassVar[str | None] = None
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
        """The versions of diffsync, snapdelta and the plugin; a plugin run from a source tree has no version."""
        names = ["diffsync", "snapdelta", *([self.distribution] if self.distribution else [])]
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


def register_source(name: str, cls: type[Source]) -> None:
    """Add a source without an entry point, for tests and for code that embeds snapdelta."""
    _REGISTERED[name] = cls


def source_classes() -> dict[str, type[Source]]:
    """The installed sources by entry point name, then the registered ones; a plugin that cannot load is left out."""
    return _load_sources()[0] | _REGISTERED


def broken_sources() -> dict[str, str]:
    """The sources whose plugin cannot load, with the error of the import."""
    return _load_sources()[1]


def require_source(name: str) -> type[Source]:
    """The class of one source; the error tells a source that is not installed from one that cannot load."""
    found, broken = _load_sources()
    classes = found | _REGISTERED
    if name in classes:
        return classes[name]
    if name in broken:
        raise LoadError(name, f"the plugin of this source cannot load ({broken[name]}): fix or reinstall it")
    installed = ", ".join(sorted(classes)) or "none"
    raise SnapdeltaError(f"no source {name!r} is installed (installed: {installed}): install its plugin package")


def _load_sources() -> tuple[dict[str, type[Source]], dict[str, str]]:
    found: dict[str, type[Source]] = {}
    broken: dict[str, str] = {}
    for entry in entry_points(group=SOURCE_GROUP):
        try:
            loaded = entry.load()
        except Exception as error:
            # One broken plugin must not stop the other sources, nor the commands that need no source.
            broken[entry.name] = f"{type(error).__name__}: {error}"
            continue
        if isinstance(loaded, type) and issubclass(loaded, Source):
            found[entry.name] = loaded
    return found, broken
