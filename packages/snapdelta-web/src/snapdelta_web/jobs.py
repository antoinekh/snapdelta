"""Snapshots that run in the background of the web server, so that a page can follow each target live."""

import contextvars
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.runners import Progress
from snapdelta.source import Source
from snapdelta.take import take_snapshots

from .state import AppState

# The pages of the newest jobs only: the Activity of the store keeps every run for longer.
JOBS_KEPT = 50


@dataclass
class TargetLine:
    name: str
    status: str = "queued"
    reference: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None:
            return None
        return ((self.ended_at or datetime.now(UTC)) - self.started_at).total_seconds()


@dataclass
class TakeJob:
    """One snapshot of several targets: the form that started it, and where each target stands."""

    id: str
    source: str
    label: str | None
    lines: dict[str, TargetLine]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.ended_at is not None

    def count(self, status: str) -> int:
        return sum(line.status == status for line in self.lines.values())

    @property
    def failed(self) -> list[str]:
        return [line.name for line in self.lines.values() if line.status == "failed"]

    def update(self, target: str, step: Progress) -> None:
        line = self.lines[target]
        line.status, line.reference, line.error = step.status, step.reference, step.error
        if step.status == "running":
            line.started_at = datetime.now(UTC)
        else:
            line.ended_at = datetime.now(UTC)


class TakeJobs:
    """The jobs of this server, in memory; each runs in its own thread."""

    def __init__(self) -> None:
        self._jobs: dict[str, TakeJob] = {}
        self._lock = threading.Lock()

    def start(self, state: AppState, plan: dict[str, type[Source]], source: str, label: str | None) -> TakeJob:
        job = TakeJob(secrets.token_hex(4), source, label, {name: TargetLine(name) for name in plan})
        with self._lock:
            self._jobs[job.id] = job
            for old in list(self._jobs)[:-JOBS_KEPT]:
                del self._jobs[old]
        # The context of the request goes with the thread, so that the run records its user.
        context = contextvars.copy_context()
        thread = threading.Thread(
            target=context.run, args=(self._run, job, state, plan), name=f"take-{job.id}", daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> TakeJob:
        with self._lock:
            found = self._jobs.get(job_id)
        if found is None:
            raise NotFoundError(f"no snapshot job {job_id!r} on this server: Activity keeps every run")
        return found

    @staticmethod
    def _run(job: TakeJob, state: AppState, plan: dict[str, type[Source]]) -> None:
        try:
            take_snapshots(
                state.store,
                plan,
                job.label,
                jobs=state.settings.snapshot.jobs,
                settings=state.settings,
                origin="web",
                progress=job.update,
            )
        except SnapdeltaError as error:
            job.error = str(error)
        finally:
            job.ended_at = datetime.now(UTC)
