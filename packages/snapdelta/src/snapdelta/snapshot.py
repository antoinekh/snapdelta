"""Snapshot of a loaded source: the diffsync data plus the metadata of the load, in a JSON file."""

from __future__ import annotations

import functools
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .activity import LogLine
from .errors import SnapdeltaError
from .jsonfile import read_json, write_json
from .source import InputRef, Source, require_source

SCHEMA = 2


class SnapshotFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=SCHEMA, alias="schema")
    target: str
    source: str
    label: str | None = None
    captured_at: datetime
    created_at: datetime
    versions: dict[str, str]
    inputs: list[InputRef]
    loaded: list[str]
    unmapped: list[str]
    warnings: list[str]
    # The log messages of the load, from the source plugin and from snapdelta.
    log: list[LogLine] = []
    data: dict[str, dict[str, dict[str, Any]]]


class StoredSource(Source):
    """Mixed in before a plugin source class: loads that source's models from a snapshot file."""

    def __init__(self, snapshot: SnapshotFile, reference: str | None = None) -> None:
        # The plugin constructor is skipped on purpose: it may need inputs that a snapshot does not have.
        Source.__init__(self, target=snapshot.target, label=snapshot.label, captured_at=snapshot.captured_at)
        self.snapshot = snapshot
        self.reference = reference
        self.source = snapshot.source

    def load(self) -> None:
        self.load_from_dict(self.snapshot.data)
        self.inputs = list(self.snapshot.inputs)
        self.unmapped = list(self.snapshot.unmapped)
        self.warnings = list(self.snapshot.warnings)
        self.log = list(self.snapshot.log)
        self.set_loaded(self.snapshot.loaded)


def build_snapshot_file(adapter: Source) -> SnapshotFile:
    target, captured_at = adapter.require_loaded()
    return SnapshotFile(
        target=target,
        source=adapter.source,
        label=adapter.label,
        captured_at=captured_at,
        created_at=datetime.now(UTC),
        versions=adapter.versions(),
        inputs=adapter.inputs,
        loaded=adapter.loaded,
        unmapped=adapter.unmapped,
        warnings=adapter.warnings,
        log=adapter.log,
        # `model_flags` is a diffsync runtime flag and is not JSON-serializable.
        data=adapter.dict(exclude_defaults=False, exclude={"adapter", "model_flags"}),
    )


def save_snapshot(adapter: Source, path: Path) -> None:
    write_json(path, build_snapshot_file(adapter).model_dump_json(by_alias=True, indent=2))


def read_snapshot_file(path: Path) -> SnapshotFile:
    value = read_json(path, SCHEMA)
    try:
        return SnapshotFile.model_validate(value)
    except ValidationError as error:
        raise SnapdeltaError(f"{path}: not a valid snapshot ({error.error_count()} errors)") from error


def stored_source_class(source: str) -> type[StoredSource]:
    return _stored_class(require_source(source))


@functools.cache
def _stored_class(base: type[Source]) -> type[StoredSource]:
    # One class per plugin class, so that loading many snapshots does not build a class each time.
    return type(f"{base.__name__}Snapshot", (StoredSource, base), {})


def top_level_models(snapshot: SnapshotFile) -> list[str]:
    """The top-level models that a snapshot holds, in the order of its source, without loading its data."""
    return [name for name in stored_source_class(snapshot.source).top_level if name in snapshot.loaded]


def load_snapshot(path: Path) -> StoredSource:
    snapshot = read_snapshot_file(path)
    adapter = stored_source_class(snapshot.source)(snapshot, f"{path.parent.name}/{path.stem}")
    adapter.load()
    return adapter
