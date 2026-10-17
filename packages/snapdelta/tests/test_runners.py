from collections.abc import Sequence
from pathlib import Path

import pytest

from snapdelta.errors import SnapdeltaError
from snapdelta.plugins import register_plugin
from snapdelta.runners import (
    RUNNERS,
    LoadOutcome,
    LoadTask,
    OnProgress,
    Progress,
    Runner,
    RunnerSettings,
    execute,
)
from snapdelta.settings import Settings, SettingsError, load_settings
from snapdelta.store import FileStore, Store
from snapdelta.take import take_snapshots
from snapdelta_example import ExampleLiveSource

DEVICES = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "devices"


def settings_file(tmp_path: Path, runner: str) -> Path:
    path = tmp_path / "snapdelta.toml"
    path.write_text(
        f'[store]\nlocation = "{tmp_path / "store"}"\n[snapshot]\nrunner = "{runner}"\nlog_level = "info"\n'
        f'[sources.example-live]\ndata_dir = "{DEVICES}"\n'
    )
    return path


@pytest.mark.parametrize("runner", ["threads", "processes"])
def test_each_runner_saves_the_snapshots_and_brings_back_the_logs(tmp_path, runner) -> None:
    from snapdelta.activity import configure_logging

    configure_logging("info")
    settings = load_settings(settings_file(tmp_path, runner))
    store = FileStore(tmp_path / "store")
    heard: list[tuple[str, str]] = []
    taken, failures = take_snapshots(
        store,
        {"R1": ExampleLiveSource, "R2": ExampleLiveSource, "R9": ExampleLiveSource},
        "before",
        jobs=2,
        settings=settings,
        progress=lambda target, step: heard.append((target, step.status)),
    )
    assert list(taken) == ["R1", "R2"] and taken["R1"].reference.endswith("-before")
    assert taken["R1"].loaded == ["interface", "address", "vlan", "interface_state"]
    assert failures["R9"].startswith("R9: not reachable")
    for target, last in (("R1", "saved"), ("R2", "saved"), ("R9", "failed")):
        assert [status for name, status in heard if name == target] == ["running", last]
    [run] = store.runs()
    assert run.saved == [taken["R1"].reference, taken["R2"].reference] and set(run.errors) == {"R9"}
    assert {line.target for line in run.log if "read" in line.message} == {"R1", "R2"}
    assert store.load_snapshot(taken["R1"].reference).log


def test_a_failed_load_keeps_its_log(tmp_path) -> None:
    from snapdelta.activity import configure_logging

    configure_logging("info")
    settings = load_settings(settings_file(tmp_path, "threads"))
    outcome = execute(LoadTask(source="example-live", target="R9"), settings, FileStore(tmp_path / "store"))
    assert outcome.reference is None and outcome.error and outcome.error.startswith("R9: not reachable")


def test_an_unknown_runner_stops_the_settings(tmp_path) -> None:
    with pytest.raises(SettingsError) as caught:
        load_settings(settings_file(tmp_path, "celery"))
    assert caught.value.errors == {"snapshot.runner": "no runner 'celery' is installed (installed: processes, threads)"}


class Recorder(Runner):
    tasks: list[LoadTask] = []

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
        cls.tasks = list(tasks)
        if progress is not None:
            progress("R1", Progress("running"))
        return {task.target: LoadOutcome(target=task.target, error="queued elsewhere") for task in tasks}


def test_a_plugin_runner_gets_the_tasks_as_data(tmp_path) -> None:
    register_plugin(RUNNERS, "recorder", Recorder)
    settings = load_settings(settings_file(tmp_path, "recorder"))
    taken, failures = take_snapshots(FileStore(tmp_path / "store"), {"R1": ExampleLiveSource}, "x", settings=settings)
    assert (taken, failures) == ({}, {"R1": "queued elsewhere"})
    assert Recorder.tasks == [LoadTask(source="example-live", target="R1", label="x")]


def test_a_class_that_is_not_the_registered_source_is_refused(tmp_path) -> None:
    class Other(ExampleLiveSource):
        pass

    with pytest.raises(SnapdeltaError, match="Other is not the source 'example-live' of the registry"):
        take_snapshots(FileStore(tmp_path), {"R1": Other})
