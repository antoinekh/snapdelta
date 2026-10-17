"""Where snapshots and outputs are kept: an abstract store, the directory and SQLite backends, plugin backends."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field, ValidationError

from .activity import Run
from .errors import NotFoundError, SnapdeltaError
from .jsonfile import write_json
from .output import SCHEMA as OUTPUT_SCHEMA
from .output import Output, Review, described, load_output, reviewed, save_output
from .plugins import (
    ExtensionPoint,
    Problem,
    Severity,
    abstract_problems,
    plugins,
    require,
    settings_problems,
    subclass_check,
)
from .settings import Settings, StoreBackendSettings
from .snapshot import SCHEMA as SNAPSHOT_SCHEMA
from .snapshot import (
    SnapshotFile,
    StoredSource,
    build_snapshot_file,
    read_snapshot_file,
    stored_source_class,
)
from .source import Source

UNSAFE_RE = re.compile(r"[^\w.-]+")
STAMP = "%Y%m%dT%H%M%SZ"
SNAPSHOT_NAME_RE = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<label>.+))?$")
# `.N` after the time keeps two outputs of the same pair in the same second apart.
OUTPUT_NAME_RE = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)(?:\.\d+)?-(?P<left>.+)-vs-(?P<right>.+)$")
PART_RE = re.compile(r"^[\w.-]+$")
RUN_ID_RE = re.compile(r"^\d{8}T\d{12}Z-[a-z]+-[0-9a-f]{4}$")
# The store keeps the newest runs only: the activity is a recent history, not an archive.
RUNS_KEPT = 1000
LATEST = "latest"
SCHEME_SEPARATOR = "://"
SQLITE_PREFIX = "sqlite://"
# A location without a scheme is a directory of the file store.
DEFAULT_SCHEME = "file"
BUSY_TIMEOUT_S = 30.0


def default_store_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "snapdelta"


def open_store(location: str, settings: Settings | None = None) -> Store:
    """`SCHEME://ADDRESS` opens the backend of that scheme, with its `[stores.SCHEME]` settings.

    A location without a scheme is a directory. The settings choose the location of `SNAPDELTA_STORE` and the default.
    """
    scheme, address = split_location(location)
    cls = require(STORES, scheme)
    chosen = settings.plugin("stores", scheme, cls.settings_class) if settings else cls.settings_class()
    return cls.open(address, chosen)


def split_location(location: str) -> tuple[str, str]:
    """The scheme and the address of a store location."""
    scheme, separator, address = location.partition(SCHEME_SEPARATOR)
    return (scheme, address) if separator else (DEFAULT_SCHEME, location)


def open_store_from(settings: Settings) -> Store:
    return open_store(settings.store.location or str(default_store_path()), settings)


def age_cutoff(days: int) -> datetime:
    """The moment `days` days ago, for the age rule of a prune."""
    if days < 0:
        raise SnapdeltaError("an age cannot be negative")
    try:
        return datetime.now(UTC) - timedelta(days=days)
    except OverflowError as error:
        raise SnapdeltaError(f"an age of {days} days is too big") from error


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(STAMP)


def _parse_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, STAMP).replace(tzinfo=UTC)


def _safe(text: str) -> str:
    return UNSAFE_RE.sub("_", text)


def _valid_part(part: str) -> bool:
    """One name part: safe characters only, and never `.` or `..`, so that a name cannot leave the store."""
    return bool(PART_RE.match(part)) and part not in (".", "..")


@dataclass(frozen=True)
class SnapshotEntry:
    """A snapshot known from its reference only, so that long lists read no snapshot."""

    target: str
    reference: str
    captured_at: datetime
    label: str | None


@dataclass(frozen=True)
class OutputEntry:
    """An output known from its name only."""

    name: str
    created_at: datetime
    left: str
    right: str


@dataclass(frozen=True)
class StoreInfo:
    backend: str
    location: str
    targets: int
    snapshots: int
    outputs: int
    size_bytes: int
    runs: int = 0


@dataclass
class PrunePlan:
    snapshots: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


def snapshot_entry(reference: str) -> SnapshotEntry | None:
    """The entry of a snapshot from its reference alone, TARGET/TIME-LABEL; None for another name."""
    target, _, name = reference.partition("/")
    match = SNAPSHOT_NAME_RE.match(name)
    if match is None:
        return None
    return SnapshotEntry(target, reference, _parse_stamp(match["stamp"]), match["label"])


def output_entry(name: str) -> OutputEntry | None:
    """The entry of an output from its name alone, TIME-LEFT-vs-RIGHT; None for another name."""
    match = OUTPUT_NAME_RE.match(name)
    if match is None:
        return None
    return OutputEntry(name, _parse_stamp(match["stamp"]), match["left"], match["right"])


def _one(matches: list[str], reference: str, kind: str, location: str) -> str:
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFoundError(f"no {kind} matches {reference!r} in {location}")
    raise NotFoundError(f"{reference!r} matches {len(matches)} {kind}s: use TARGET/NAME")


class Store(ABC):
    backend: ClassVar[str]
    # True: the address is a path on this machine, so the settings file resolves it against its directory.
    local_path: ClassVar[bool] = False
    # The typed settings that snapdelta reads for this backend from the [stores.SCHEME] table.
    settings_class: ClassVar[type[StoreBackendSettings]] = StoreBackendSettings

    @classmethod
    @abstractmethod
    def open(cls, address: str, settings: StoreBackendSettings) -> Store:
        """The store at `address`, the part of the location after `SCHEME://`."""

    @property
    @abstractmethod
    def location(self) -> str:
        """The location that opens this store again, with its scheme when it has one."""

    def close(self) -> None:
        """Give back what the store holds open, such as the connections of a database; it is not used after."""
        return None

    # Snapshots

    def save_snapshot(self, adapter: Source) -> str:
        snapshot = build_snapshot_file(adapter)
        name = _stamp(snapshot.captured_at) + (f"-{_safe(snapshot.label)}" if snapshot.label else "")
        reference = f"{_safe(snapshot.target)}/{name}"
        if self._has_snapshot(reference):
            raise SnapdeltaError(f"snapshot {reference} already exists: give another label")
        self._write_snapshot(reference, snapshot)
        return reference

    def has_snapshot(self, reference: str) -> bool:
        return self._valid_reference(reference) and self._has_snapshot(reference)

    def read_snapshot(self, reference: str) -> SnapshotFile:
        if not self.has_snapshot(reference):
            raise NotFoundError(f"no snapshot {reference!r} in {self.location}")
        return self._read_snapshot(reference)

    def load_snapshot(self, reference: str) -> StoredSource:
        snapshot = self.read_snapshot(reference)
        adapter = stored_source_class(snapshot.source)(snapshot, reference)
        adapter.load()
        return adapter

    def resolve_snapshot(self, reference: str) -> str:
        target, at, tag = reference.partition("@")
        if at:
            # TARGET@latest is the newest snapshot of the target; TARGET@LABEL the newest with that label.
            entries = self.snapshot_entries(target)
            if tag != LATEST:
                entries = [entry for entry in entries if entry.label == _safe(tag)]
            if not entries:
                which = "snapshot" if tag == LATEST else f"snapshot labelled {tag!r}"
                raise NotFoundError(f"no {which} of {target} in {self.location}")
            return entries[0].reference
        wanted = reference.removesuffix(".json").removeprefix("snapshots/")
        if self._valid_reference(wanted) and self._has_snapshot(wanted):
            return wanted
        matches = [
            entry.reference
            for name in self.targets()
            for entry in self.snapshot_entries(name)
            if entry.reference.partition("/")[2] == wanted
        ]
        return _one(matches, reference, "snapshot", self.location)

    def delete_snapshot(self, reference: str) -> None:
        self.read_snapshot(reference)
        self._delete_snapshot(reference)

    @abstractmethod
    def targets(self, query: str = "") -> list[str]:
        """The target names that contain `query` (any case), sorted."""

    @abstractmethod
    def snapshot_entries(self, target: str) -> list[SnapshotEntry]:
        """The snapshots of one target, newest first."""

    # Outputs

    def save_output(self, output: Output) -> str:
        stamp, pair = _stamp(output.created_at), f"{_safe(output.left.target)}-vs-{_safe(output.right.target)}"
        name = f"{stamp}-{pair}"
        sequence = 1
        while self._has_output(name):
            sequence += 1
            name = f"{stamp}.{sequence}-{pair}"
        self._write_output(name, output)
        return name

    def load_output(self, name: str) -> Output:
        if not _valid_part(name) or not self._has_output(name):
            raise NotFoundError(f"no output {name!r} in {self.location}")
        return self._read_output(name)

    def resolve_output(self, reference: str) -> str:
        wanted = reference.removesuffix(".json").removeprefix("outputs/")
        if _valid_part(wanted) and self._has_output(wanted):
            return wanted
        return _one([], reference, "output", self.location)

    def delete_output(self, name: str) -> None:
        self.load_output(name)
        self._delete_output(name)

    def describe_output(self, name: str, title: str | None, description: str | None) -> Output:
        """Set the title and the description of a saved output; with the reviews, the only parts that change."""
        output = described(self.load_output(name), title, description)
        self._replace_output(name, output)
        return output

    def review_output(self, name: str, key: str, review: Review | None) -> Output:
        """Accept one change of a saved output, or withdraw the acceptance when `review` is None."""
        output = reviewed(self.load_output(name), key, review)
        self._replace_output(name, output)
        return output

    @abstractmethod
    def output_entries(self, query: str = "") -> list[OutputEntry]:
        """The outputs whose left or right target contains `query` (any case), newest first."""

    # Activity

    def save_run(self, run: Run) -> None:
        self._write_run(run)
        for old in self._run_ids()[RUNS_KEPT:]:
            self._delete_run(old)

    def runs(self, limit: int = 100, kind: str | None = None) -> list[Run]:
        """The newest runs, of one kind when `kind` is given."""
        found = []
        for run_id in self._run_ids():
            run = self._read_run(run_id)
            if kind is None or run.kind == kind:
                found.append(run)
            if len(found) >= limit:
                break
        return found

    def load_run(self, run_id: str) -> Run:
        if not RUN_ID_RE.match(run_id) or run_id not in self._run_ids():
            raise NotFoundError(f"no run {run_id!r} in {self.location}")
        return self._read_run(run_id)

    # Admin

    @abstractmethod
    def info(self) -> StoreInfo: ...

    def prune_plan(
        self,
        target: str | None = None,
        keep: int | None = None,
        older_than: datetime | None = None,
        outputs: bool = False,
    ) -> PrunePlan:
        """A snapshot goes when it fails every rule given: among the `keep` newest, or not older than `older_than`."""
        if keep is not None and keep < 0:
            raise SnapdeltaError("the number of snapshots to keep cannot be negative")
        if keep is None and older_than is None:
            raise SnapdeltaError("give a number of snapshots to keep, an age, or both")
        if outputs and older_than is None:
            raise SnapdeltaError("pruning outputs needs an age")
        plan = PrunePlan()
        for name in [target] if target else self.targets():
            for index, entry in enumerate(self.snapshot_entries(name)):
                kept_by_rank = keep is not None and index < keep
                kept_by_age = older_than is not None and entry.captured_at >= older_than
                if not kept_by_rank and not kept_by_age:
                    plan.snapshots.append(entry.reference)
        if outputs and older_than is not None:
            plan.outputs = [
                entry.name
                for entry in self.output_entries()
                if entry.created_at < older_than and (target is None or target in (entry.left, entry.right))
            ]
        return plan

    def apply(self, plan: PrunePlan) -> None:
        for reference in plan.snapshots:
            self._delete_snapshot(reference)
        for name in plan.outputs:
            self._delete_output(name)

    # Backend primitives

    def _valid_reference(self, reference: str) -> bool:
        target, separator, name = reference.partition("/")
        return bool(separator) and _valid_part(target) and _valid_part(name)

    @abstractmethod
    def _has_snapshot(self, reference: str) -> bool: ...

    @abstractmethod
    def _write_snapshot(self, reference: str, snapshot: SnapshotFile) -> None: ...

    @abstractmethod
    def _read_snapshot(self, reference: str) -> SnapshotFile: ...

    @abstractmethod
    def _delete_snapshot(self, reference: str) -> None: ...

    @abstractmethod
    def _has_output(self, name: str) -> bool: ...

    @abstractmethod
    def _write_output(self, name: str, output: Output) -> None: ...

    @abstractmethod
    def _read_output(self, name: str) -> Output: ...

    @abstractmethod
    def _replace_output(self, name: str, output: Output) -> None: ...

    @abstractmethod
    def _delete_output(self, name: str) -> None: ...

    @abstractmethod
    def _write_run(self, run: Run) -> None: ...

    @abstractmethod
    def _read_run(self, run_id: str) -> Run: ...

    @abstractmethod
    def _run_ids(self) -> list[str]:
        """Newest first."""

    @abstractmethod
    def _delete_run(self, run_id: str) -> None: ...


def _inspect_store(name: str, cls: type[Store]) -> list[Problem]:
    problems = abstract_problems(cls) + settings_problems(cls, StoreBackendSettings)
    backend = getattr(cls, "backend", None)
    if not backend:
        problems.append(Problem(Severity.ERROR, "the class attribute backend is not set: `snapdelta info` shows it"))
    elif backend != name:
        problems.append(Problem(Severity.WARNING, f"backend is {backend!r}: give it the name of its scheme, {name!r}"))
    return problems


STORES: ExtensionPoint[type[Store]] = ExtensionPoint(
    group="snapdelta.stores",
    kind="store backend",
    interface="snapdelta.store.Store",
    table="stores",
    check=subclass_check(Store),
    inspect=_inspect_store,
)


def store_classes() -> dict[str, type[Store]]:
    """The store backends by the scheme of their location, the built-in ones included; a broken one is left out."""
    return plugins(STORES)


class FileStore(Store):
    """One JSON file per snapshot (`snapshots/<target>/<name>.json`) and per output (`outputs/<name>.json`)."""

    backend = "file"
    local_path = True

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def open(cls, address: str, settings: StoreBackendSettings) -> FileStore:
        return cls(Path(address))

    @property
    def location(self) -> str:
        return str(self.root)

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    def resolve_snapshot(self, reference: str) -> str:
        path = Path(reference)
        if path.suffix == ".json" and path.is_file() and path.resolve().parent.parent == self.snapshots_dir.resolve():
            return f"{path.parent.name}/{path.stem}"
        return super().resolve_snapshot(reference)

    def resolve_output(self, reference: str) -> str:
        path = Path(reference)
        if path.suffix == ".json" and path.is_file() and path.resolve().parent == self.outputs_dir.resolve():
            return path.stem
        return super().resolve_output(reference)

    def targets(self, query: str = "") -> list[str]:
        if not self.snapshots_dir.is_dir():
            return []
        wanted = query.strip().lower()
        return sorted(
            path.name for path in self.snapshots_dir.iterdir() if path.is_dir() and wanted in path.name.lower()
        )

    def snapshot_entries(self, target: str) -> list[SnapshotEntry]:
        if not _valid_part(target):
            return []
        directory = self.snapshots_dir / target
        references = [f"{target}/{path.stem}" for path in directory.glob("*.json")] if directory.is_dir() else []
        entries = [entry for reference in references if (entry := snapshot_entry(reference))]
        return sorted(entries, key=lambda entry: entry.reference, reverse=True)

    def output_entries(self, query: str = "") -> list[OutputEntry]:
        wanted = query.strip().lower()
        paths = self.outputs_dir.glob("*.json") if self.outputs_dir.is_dir() else []
        entries = [entry for path in paths if (entry := output_entry(path.stem))]
        selected = [entry for entry in entries if wanted in entry.left.lower() or wanted in entry.right.lower()]
        return sorted(selected, key=lambda entry: entry.name, reverse=True)

    def info(self) -> StoreInfo:
        targets = self.targets()
        files = list(self.root.rglob("*.json")) if self.root.is_dir() else []
        return StoreInfo(
            backend=self.backend,
            location=self.location,
            targets=len(targets),
            snapshots=sum(len(self.snapshot_entries(target)) for target in targets),
            outputs=len(self.output_entries()),
            size_bytes=sum(path.stat().st_size for path in files),
            runs=len(self._run_ids()),
        )

    def _snapshot_path(self, reference: str) -> Path:
        target, _, name = reference.partition("/")
        return self.snapshots_dir / target / f"{name}.json"

    def _has_snapshot(self, reference: str) -> bool:
        return self._valid_reference(reference) and self._snapshot_path(reference).is_file()

    def _write_snapshot(self, reference: str, snapshot: SnapshotFile) -> None:
        write_json(self._snapshot_path(reference), snapshot.model_dump_json(by_alias=True, indent=2))

    def _read_snapshot(self, reference: str) -> SnapshotFile:
        return read_snapshot_file(self._snapshot_path(reference))

    def _delete_snapshot(self, reference: str) -> None:
        path = self._snapshot_path(reference)
        path.unlink(missing_ok=True)
        if path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()

    def _output_path(self, name: str) -> Path:
        return self.outputs_dir / f"{name}.json"

    def _has_output(self, name: str) -> bool:
        return _valid_part(name) and self._output_path(name).is_file()

    def _write_output(self, name: str, output: Output) -> None:
        save_output(output, self._output_path(name))

    def _read_output(self, name: str) -> Output:
        return load_output(self._output_path(name))

    def _replace_output(self, name: str, output: Output) -> None:
        save_output(output, self._output_path(name))

    def _delete_output(self, name: str) -> None:
        self._output_path(name).unlink(missing_ok=True)

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def _write_run(self, run: Run) -> None:
        write_json(self.runs_dir / f"{run.id}.json", run.model_dump_json(indent=2))

    def _read_run(self, run_id: str) -> Run:
        return parse_run((self.runs_dir / f"{run_id}.json").read_text(encoding="utf-8"), run_id)

    def _run_ids(self) -> list[str]:
        paths = self.runs_dir.glob("*.json") if self.runs_dir.is_dir() else []
        return sorted((path.stem for path in paths if RUN_ID_RE.match(path.stem)), reverse=True)

    def _delete_run(self, run_id: str) -> None:
        (self.runs_dir / f"{run_id}.json").unlink(missing_ok=True)


SQLITE_SCHEMA = """
create table if not exists snapshots (
    reference text primary key,
    target text not null,
    captured_at text not null,
    label text,
    source text not null,
    body text not null
);
create index if not exists snapshots_by_target on snapshots (target, reference);
create table if not exists outputs (
    name text primary key,
    created_at text not null,
    left_target text not null,
    right_target text not null,
    body text not null
);
create index if not exists outputs_by_time on outputs (created_at);
create table if not exists runs (
    id text primary key,
    body text not null
);
"""


class SqliteSettings(StoreBackendSettings):
    busy_timeout_s: float = Field(
        default=BUSY_TIMEOUT_S, gt=0, description="How long a writer waits for the lock of the database, in seconds."
    )


class SqliteStore(Store):
    """One SQLite file; the bodies are the same JSON documents as the files of a FileStore."""

    backend = "sqlite"
    local_path = True
    settings_class = SqliteSettings

    def __init__(self, path: Path, busy_timeout_s: float = BUSY_TIMEOUT_S) -> None:
        self.path = path
        # A parallel writer waits this long for the database lock instead of failing at once.
        self.busy_timeout_s = busy_timeout_s
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SQLITE_SCHEMA)

    @classmethod
    def open(cls, address: str, settings: StoreBackendSettings) -> SqliteStore:
        chosen = settings if isinstance(settings, SqliteSettings) else SqliteSettings()
        return cls(Path(address), chosen.busy_timeout_s)

    @property
    def location(self) -> str:
        return f"{SQLITE_PREFIX}{self.path}"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One connection per call, because the web server runs requests in threads."""
        with closing(sqlite3.connect(self.path, timeout=self.busy_timeout_s)) as db, db:
            yield db

    def targets(self, query: str = "") -> list[str]:
        wanted = query.strip().lower()
        with self._connect() as db:
            rows = db.execute(
                "select distinct target from snapshots where instr(lower(target), ?) > 0 order by target", (wanted,)
            ).fetchall()
        return [row[0] for row in rows]

    def snapshot_entries(self, target: str) -> list[SnapshotEntry]:
        with self._connect() as db:
            rows = db.execute(
                "select reference from snapshots where target = ? order by reference desc", (target,)
            ).fetchall()
        return [entry for (reference,) in rows if (entry := snapshot_entry(reference))]

    def output_entries(self, query: str = "") -> list[OutputEntry]:
        wanted = query.strip().lower()
        with self._connect() as db:
            rows = db.execute(
                "select name from outputs where instr(lower(left_target), ?) > 0 or instr(lower(right_target), ?) > 0 "
                "order by name desc",
                (wanted, wanted),
            ).fetchall()
        return [entry for (name,) in rows if (entry := output_entry(name))]

    def info(self) -> StoreInfo:
        with self._connect() as db:
            targets = db.execute("select count(distinct target) from snapshots").fetchone()[0]
            snapshots = db.execute("select count(*) from snapshots").fetchone()[0]
            outputs = db.execute("select count(*) from outputs").fetchone()[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return StoreInfo(self.backend, self.location, targets, snapshots, outputs, size, len(self._run_ids()))

    def _has_snapshot(self, reference: str) -> bool:
        with self._connect() as db:
            return db.execute("select 1 from snapshots where reference = ?", (reference,)).fetchone() is not None

    def _write_snapshot(self, reference: str, snapshot: SnapshotFile) -> None:
        values = (
            reference,
            reference.partition("/")[0],
            snapshot.captured_at.isoformat(),
            snapshot.label,
            snapshot.source,
            snapshot.model_dump_json(by_alias=True),
        )
        with self._connect() as db:
            db.execute(
                "insert into snapshots (reference, target, captured_at, label, source, body) values (?, ?, ?, ?, ?, ?)",
                values,
            )

    def _read_snapshot(self, reference: str) -> SnapshotFile:
        with self._connect() as db:
            row = db.execute("select body from snapshots where reference = ?", (reference,)).fetchone()
        # Another process can delete the row between the existence check and this read.
        if row is None:
            raise NotFoundError(f"no snapshot {reference!r} in {self.location}")
        return parse_snapshot(row[0], reference)

    def _delete_snapshot(self, reference: str) -> None:
        with self._connect() as db:
            db.execute("delete from snapshots where reference = ?", (reference,))

    def _has_output(self, name: str) -> bool:
        with self._connect() as db:
            return db.execute("select 1 from outputs where name = ?", (name,)).fetchone() is not None

    def _write_output(self, name: str, output: Output) -> None:
        values = (
            name,
            output.created_at.isoformat(),
            output.left.target,
            output.right.target,
            output.model_dump_json(by_alias=True),
        )
        with self._connect() as db:
            db.execute(
                "insert into outputs (name, created_at, left_target, right_target, body) values (?, ?, ?, ?, ?)", values
            )

    def _read_output(self, name: str) -> Output:
        with self._connect() as db:
            row = db.execute("select body from outputs where name = ?", (name,)).fetchone()
        if row is None:
            raise NotFoundError(f"no output {name!r} in {self.location}")
        return parse_output(row[0], name)

    def _replace_output(self, name: str, output: Output) -> None:
        with self._connect() as db:
            db.execute("update outputs set body = ? where name = ?", (output.model_dump_json(by_alias=True), name))

    def _delete_output(self, name: str) -> None:
        with self._connect() as db:
            db.execute("delete from outputs where name = ?", (name,))

    def _write_run(self, run: Run) -> None:
        with self._connect() as db:
            db.execute("insert or replace into runs (id, body) values (?, ?)", (run.id, run.model_dump_json()))

    def _read_run(self, run_id: str) -> Run:
        with self._connect() as db:
            row = db.execute("select body from runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no run {run_id!r} in {self.location}")
        return parse_run(row[0], run_id)

    def _run_ids(self) -> list[str]:
        with self._connect() as db:
            return [row[0] for row in db.execute("select id from runs order by id desc")]

    def _delete_run(self, run_id: str) -> None:
        with self._connect() as db:
            db.execute("delete from runs where id = ?", (run_id,))


def parse_snapshot(body: str | Mapping[str, Any], reference: str) -> SnapshotFile:
    """A saved snapshot, from its JSON text or its parsed JSON, checked against the schema that this snapdelta reads."""
    try:
        snapshot = SnapshotFile.model_validate(json.loads(body) if isinstance(body, str) else body)
    except ValidationError as error:
        raise SnapdeltaError(f"snapshot {reference}: not valid ({error.error_count()} errors)") from error
    except ValueError as error:
        raise SnapdeltaError(f"snapshot {reference}: not valid JSON ({error})") from error
    if snapshot.schema_version != SNAPSHOT_SCHEMA:
        found = snapshot.schema_version
        raise SnapdeltaError(f"snapshot {reference}: schema {found}, this snapdelta reads {SNAPSHOT_SCHEMA}")
    return snapshot


def parse_output(body: str | Mapping[str, Any], name: str) -> Output:
    """A saved output, from its JSON text or its parsed JSON, checked against the schema that this snapdelta reads."""
    try:
        output = Output.model_validate(json.loads(body) if isinstance(body, str) else body)
    except ValidationError as error:
        raise SnapdeltaError(f"output {name}: not valid ({error.error_count()} errors)") from error
    except ValueError as error:
        raise SnapdeltaError(f"output {name}: not valid JSON ({error})") from error
    if output.schema_version != OUTPUT_SCHEMA:
        found = output.schema_version
        raise SnapdeltaError(f"output {name}: schema {found}, this snapdelta reads {OUTPUT_SCHEMA}")
    return output


def parse_run(body: str | Mapping[str, Any], run_id: str) -> Run:
    """A saved run, from its JSON text or its parsed JSON."""
    try:
        return Run.model_validate(json.loads(body) if isinstance(body, str) else body)
    except ValidationError as error:
        raise SnapdeltaError(f"run {run_id} is not a valid run: {error.error_count()} problems") from error
    except ValueError as error:
        raise SnapdeltaError(f"run {run_id} is not valid JSON ({error})") from error
