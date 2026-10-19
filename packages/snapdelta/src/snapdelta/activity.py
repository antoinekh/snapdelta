"""What snapdelta did: one run per command (snapshots, a diff), with its result and the log messages of its loads.

A source plugin logs with the standard `logging` module. While a load runs, `LogCollector` adds each message to
the log of that load, even from the threads of `run_parallel()`, which copy the context of their caller.
"""

from __future__ import annotations

import getpass
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

from .errors import SnapdeltaError

if TYPE_CHECKING:
    from .settings import Settings
    from .store import Store

_log = logging.getLogger(__name__)
# The log that receives the messages of the current context, and the target that they are about.
_lines: ContextVar[list[LogLine] | None] = ContextVar("snapdelta_log_lines", default=None)
_target: ContextVar[str | None] = ContextVar("snapdelta_log_target", default=None)
# Microseconds, so that the ids of two runs of the same second sort in their order.
RUN_STAMP = "%Y%m%dT%H%M%S%fZ"
# Only a load collects: the compare of a run logs too (diffsync), and that says nothing about the targets.
_loading: ContextVar[bool] = ContextVar("snapdelta_loading", default=False)
# The user who asks for the run, when a server knows it: the login of a web request.
acting_user: ContextVar[str | None] = ContextVar("snapdelta_acting_user", default=None)


class LogLine(BaseModel):
    at: datetime
    level: str
    logger: str
    target: str | None = None
    message: str


class RunStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class Run(BaseModel):
    """One command: what it took or compared, how it ended, and the log of its loads."""

    id: str
    kind: str = Field(description="snapshot or diff")
    origin: str = Field(description="cli, web, api, mcp, or python for a direct call")
    user: str | None = None
    started_at: datetime
    ended_at: datetime
    status: RunStatus
    targets: list[str] = []
    saved: list[str] = Field(default=[], description="The references of the saved snapshots.")
    outputs: list[str] = Field(default=[], description="The names of the saved diffs, one per target.")
    errors: dict[str, str] = Field(default={}, description="The error of each failed target, or of the run under `*`.")
    has_differences: bool | None = Field(default=None, description="For a diff: whether it found a difference.")
    notified: dict[str, str] = Field(default={}, description="`sent`, or the error, by the name of each notifier.")
    log: list[LogLine] = []

    @model_validator(mode="before")
    @classmethod
    def _one_output(cls, value: Any) -> Any:
        # A run saved by snapdelta 0.1 names its one diff in `output`.
        if isinstance(value, dict) and "output" in value:
            old = value["output"]
            value = {key: item for key, item in value.items() if key != "output"}
            value.setdefault("outputs", [old] if old else [])
        return value

    @property
    def duration_s(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


def run_started_at(run_id: str) -> datetime:
    """The start of a run from its id alone, so that a prune reads no run."""
    return datetime.strptime(run_id.partition("-")[0], RUN_STAMP).replace(tzinfo=UTC)


class LogCollector(logging.Handler):
    """Adds each message of a load to the log of the current context; any other message goes nowhere."""

    def emit(self, record: logging.LogRecord) -> None:
        lines = _lines.get()
        if lines is None or not _loading.get():
            return
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        lines.append(
            LogLine(
                at=datetime.fromtimestamp(record.created, UTC),
                level=record.levelname.lower(),
                logger=record.name,
                target=_target.get(),
                message=message,
            )
        )


def configure_logging(level: str = "info") -> None:
    """Collect the messages at `level` and above, and keep the console at warning; once per process."""
    root = logging.getLogger()
    wanted = logging.getLevelNamesMapping()[level.upper()]
    for handler in root.handlers:
        # The console handler of logging.basicConfig() shows warnings only, as before the collector.
        if type(handler) is logging.StreamHandler and handler.level == logging.NOTSET:
            handler.setLevel(logging.WARNING)
    if not any(isinstance(handler, LogCollector) for handler in root.handlers):
        root.addHandler(LogCollector(wanted))
    else:
        for handler in root.handlers:
            if isinstance(handler, LogCollector):
                handler.setLevel(wanted)
    root.setLevel(min(root.level or logging.WARNING, wanted))


@contextmanager
def collecting(target: str | None = None, loading: bool = True) -> Iterator[list[LogLine]]:
    """The log of the load in the block, about `target`; the lines also go to the log around it."""
    outer = _lines.get()
    lines: list[LogLine] = []
    tokens = (_lines.set(lines), _target.set(target), _loading.set(loading))
    try:
        yield lines
    finally:
        _lines.reset(tokens[0])
        _target.reset(tokens[1])
        _loading.reset(tokens[2])
        if outer is not None:
            outer.extend(lines)


class RunRecord:
    """A run while it goes on; the caller fills `saved`, `outputs` and `errors`."""

    def __init__(self, kind: str, origin: str, targets: list[str]) -> None:
        self.kind = kind
        self.origin = origin
        self.user = acting_user.get() or (_user() if origin == "cli" else None)
        self.targets = targets
        self.started_at = datetime.now(UTC)
        self.saved: list[str] = []
        self.outputs: list[str] = []
        self.errors: dict[str, str] = {}
        self.has_differences: bool | None = None
        self.log: list[LogLine] = []

    def finished(self) -> Run:
        done = len(self.saved) + len(self.outputs)
        status = RunStatus.OK if not self.errors else RunStatus.PARTIAL if done else RunStatus.FAILED
        stamp = self.started_at.strftime(RUN_STAMP)
        return Run(
            id=f"{stamp}-{self.kind}-{secrets.token_hex(2)}",
            kind=self.kind,
            origin=self.origin,
            user=self.user,
            started_at=self.started_at,
            ended_at=datetime.now(UTC),
            status=status,
            targets=self.targets,
            saved=self.saved,
            outputs=self.outputs,
            errors=self.errors,
            has_differences=self.has_differences,
            # The loads add their lines when they end: the time gives back the order of the events.
            log=sorted(self.log, key=lambda line: line.at),
        )


@contextmanager
def recorded(
    store: Store, kind: str, origin: str, targets: list[str], settings: Settings | None = None
) -> Iterator[RunRecord]:
    """Record the block as a run of the store, then send it to the notifiers of `settings`.

    An error fails the run and goes on to the caller.
    """
    record = RunRecord(kind, origin, targets)
    try:
        with collecting(loading=False) as lines:
            record.log = lines
            yield record
    except Exception as error:
        record.errors["*"] = str(error) if isinstance(error, SnapdeltaError) else f"{type(error).__name__}: {error}"
        raise
    finally:
        run = record.finished()
        _save(store, run)
        if settings is not None and settings.notifiers:
            from .notify import deliver

            _save(store, run.model_copy(update={"notified": dict(deliver(run, settings))}))


def _save(store: Store, run: Run) -> None:
    try:
        store.save_run(run)
    except Exception as error:
        # The command itself went well: a run that cannot be saved, by any backend, must not turn it into an error.
        _log.warning("the run was not saved: %s: %s", type(error).__name__, error)


def _user() -> str | None:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return None
