"""Runners: how the loads of a snapshot run, in threads, in processes, or in the workers of a plugin.

A task is data, not a function: the source, the target, the label. A runner can so send it to another process
or another machine, where `execute()` loads the target with the plugins, the settings and the store found there.
"""

from __future__ import annotations

import atexit
import contextvars
import multiprocessing
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from .activity import LogLine, collecting, configure_logging
from .errors import SnapdeltaError
from .parallel import run_parallel
from .plugins import ExtensionPoint, Problem, abstract_problems, settings_problems, subclass_check
from .settings import Section, Settings, load_settings
from .store import Store, open_store

# How often a runner that waits on other processes looks at their state, in seconds.
POLL_S = 0.2


class LoadTask(BaseModel):
    """One load: fetch a target with a source, and save a snapshot of it."""

    source: str
    target: str
    label: str | None = None
    captured_at: datetime | None = None


class LoadOutcome(BaseModel):
    """The end of one load: the saved snapshot, or the error; and the log of the load."""

    target: str
    reference: str | None = None
    loaded: list[str] = []
    warnings: list[str] = []
    unmapped: int = 0
    error: str | None = None
    log: list[LogLine] = []

    @property
    def ok(self) -> bool:
        return self.reference is not None


@dataclass(frozen=True)
class Progress:
    """Where one target stands: running, then saved or failed."""

    status: str
    reference: str | None = None
    error: str | None = None


type OnProgress = Callable[[str, Progress], None]


class RunnerSettings(Section):
    """The base of the settings of a runner; a plugin subclass adds its typed fields."""


class Runner(ABC):
    """A way to run the loads of a snapshot; snapdelta waits for every outcome."""

    settings_class: ClassVar[type[RunnerSettings]] = RunnerSettings

    @classmethod
    @abstractmethod
    def run(
        cls,
        tasks: Sequence[LoadTask],
        settings: Settings,
        store: Store,
        jobs: int,
        options: RunnerSettings,
        progress: OnProgress | None = None,
    ) -> dict[str, LoadOutcome]:
        """The outcome of each task by target; `jobs` loads at most at the same time.

        `progress` hears each target when it starts and when it ends; a failed load is an outcome with its error.
        """


def execute(task: LoadTask, settings: Settings, store: Store) -> LoadOutcome:
    """Run one task in this process, with its plugins, settings and store; an error becomes the outcome."""
    from .source import require_source
    from .take import load_source

    # Inside the block, the log of the load holds every line, also when the load fails.
    with collecting(task.target) as lines:
        try:
            cls = require_source(task.source)
            inventory = settings.hosts()
            loaded = load_source(
                cls,
                target=task.target,
                label=task.label,
                captured_at=task.captured_at,
                settings=settings.source(cls),
                host=inventory.host(task.target) if inventory else None,
            )
            reference = store.save_snapshot(loaded)
        except Exception as error:
            message = (
                str(error) if isinstance(error, SnapdeltaError) else f"{task.target}: {type(error).__name__}: {error}"
            )
            return LoadOutcome(target=task.target, error=message, log=lines)
        return LoadOutcome(
            target=task.target,
            reference=reference,
            loaded=loaded.loaded,
            warnings=loaded.warnings,
            unmapped=len(loaded.unmapped),
            log=lines,
        )


def _report(progress: OnProgress | None, outcome: LoadOutcome) -> None:
    if progress is not None:
        status = "saved" if outcome.ok else "failed"
        progress(outcome.target, Progress(status, reference=outcome.reference, error=outcome.error))


class ThreadRunner(Runner):
    """The loads in threads of this process: a fetch waits on the network, so threads run many at once."""

    @classmethod
    def run(
        cls,
        tasks: Sequence[LoadTask],
        settings: Settings,
        store: Store,
        jobs: int,
        options: RunnerSettings,
        progress: OnProgress | None = None,
    ) -> dict[str, LoadOutcome]:
        def one(task: LoadTask) -> LoadOutcome:
            if progress is not None:
                progress(task.target, Progress("running"))
            # A new context, so that the log of the load comes back in its outcome only, as from another process.
            outcome = contextvars.Context().run(execute, task, settings, store)
            _report(progress, outcome)
            return outcome

        outcomes, _ = run_parallel({task.target: _bound(one, task) for task in tasks}, jobs)
        return outcomes


def _bound(function: Callable[[LoadTask], LoadOutcome], task: LoadTask) -> Callable[[], LoadOutcome]:
    return lambda: function(task)


class ProcessSettings(RunnerSettings):
    start_method: Literal["spawn", "forkserver"] = Field(
        default="spawn",
        description="How a process starts: spawn (each on every system) or forkserver (faster, Linux only).",
    )


class ProcessRunner(Runner):
    """The loads in processes: for a plugin that parses much (one CPU each) or whose library is not thread-safe.

    Each process reads the settings file and opens the store again by its location, and sees only the installed
    plugins, not the ones registered in code.
    """

    settings_class = ProcessSettings

    @classmethod
    def run(
        cls,
        tasks: Sequence[LoadTask],
        settings: Settings,
        store: Store,
        jobs: int,
        options: RunnerSettings,
        progress: OnProgress | None = None,
    ) -> dict[str, LoadOutcome]:
        chosen = options if isinstance(options, ProcessSettings) else ProcessSettings()
        if not tasks:
            return {}
        start = (settings.file, store.location, settings.snapshot.log_level)
        with ProcessPoolExecutor(
            max_workers=min(jobs, len(tasks)),
            mp_context=multiprocessing.get_context(chosen.start_method),
            initializer=_start_worker,
            initargs=start,
        ) as pool:
            pending: dict[Future[LoadOutcome], LoadTask] = {
                pool.submit(_execute_in_worker, task): task for task in tasks
            }
            return _wait(pending, progress)


def _wait(pending: Mapping[Future[LoadOutcome], LoadTask], progress: OnProgress | None) -> dict[str, LoadOutcome]:
    outcomes: dict[str, LoadOutcome] = {}
    started: set[str] = set()
    waiting = dict(pending)
    while waiting:
        done, _ = wait(waiting, timeout=POLL_S, return_when=FIRST_COMPLETED)
        for future, task in waiting.items():
            if progress is not None and task.target not in started and (future.running() or future in done):
                started.add(task.target)
                progress(task.target, Progress("running"))
        for future in done:
            task = waiting.pop(future)
            try:
                outcome = future.result()
            except Exception as error:
                # The process of the task died, for example with a crash of a library in C.
                outcome = LoadOutcome(target=task.target, error=f"{task.target}: {type(error).__name__}: {error}")
            outcomes[task.target] = outcome
            _report(progress, outcome)
    return {task.target: outcomes[task.target] for task in pending.values()}


_worker: list[tuple[Settings, Store]] = []


def _start_worker(settings_file: Path | None, location: str, log_level: str) -> None:
    configure_logging(log_level)
    settings = load_settings(settings_file, location)
    store = open_store(location, settings)
    atexit.register(store.close)
    _worker.append((settings, store))


def _execute_in_worker(task: LoadTask) -> LoadOutcome:
    settings, store = _worker[0]
    return execute(task, settings, store)


def _inspect_runner(name: str, cls: type[Runner]) -> list[Problem]:
    return abstract_problems(cls) + settings_problems(cls, RunnerSettings)


RUNNERS: ExtensionPoint[type[Runner]] = ExtensionPoint(
    group="snapdelta.runners",
    kind="runner",
    interface="snapdelta.runners.Runner",
    table="runners",
    check=subclass_check(Runner),
    inspect=_inspect_runner,
)
