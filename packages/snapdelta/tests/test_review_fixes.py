"""Failures that must stay local: a run that cannot be saved, a notifier that waits, a progress callback that raises."""

import time
from typing import Any

from snapdelta.activity import recorded
from snapdelta.notify import NOTIFIERS, Notifier, NotifierSettings, RunEvent, notify
from snapdelta.plugins import EXTENSION_POINTS, ExtensionPoint, discover, register_plugin, subclass_check
from snapdelta.runners import LoadTask, RunnerSettings, ThreadRunner
from snapdelta.settings import Settings
from snapdelta.store import FileStore


class Unsaved(FileStore):
    def save_run(self, run: Any) -> None:
        raise RuntimeError("the database is gone")


def test_a_run_that_cannot_be_saved_does_not_fail_the_command(tmp_path) -> None:
    with recorded(Unsaved(tmp_path), "diff", "cli", ["R1"]) as record:
        record.outputs = ["x"]


class Slow(Notifier):
    @classmethod
    def send(cls, event: RunEvent, settings: NotifierSettings) -> None:
        time.sleep(0.4)


def test_the_notifiers_wait_at_the_same_time_and_a_bad_filter_is_their_error(tmp_path) -> None:
    from snapdelta.activity import Run

    register_plugin(NOTIFIERS, "slow", Slow)
    settings = Settings()
    settings.notifiers = {
        "a": {"type": "slow", "on": ["success"]},
        "b": {"type": "slow", "on": ["success"]},
        "c": {"type": "slow", "on": ["success"], "groups": ["core"]},
    }
    run = Run.model_validate(
        {
            "id": "20261014T080000000000Z-diff-abcd",
            "kind": "diff",
            "origin": "cli",
            "started_at": "2026-10-14T08:00:00Z",
            "ended_at": "2026-10-14T08:00:01Z",
            "status": "ok",
            "targets": ["R1"],
        }
    )
    start = time.monotonic()
    results = notify(run, settings)
    assert time.monotonic() - start < 0.75
    assert results == {"a": "sent", "b": "sent"}


def test_a_progress_callback_that_raises_is_the_error_of_its_target(tmp_path) -> None:
    def progress(target: str, step: Any) -> None:
        if step.status != "running":
            raise ValueError("the page is gone")

    outcomes = ThreadRunner.run(
        [LoadTask(source="example-live", target="R1")], Settings(), FileStore(tmp_path), 1, RunnerSettings(), progress
    )
    assert outcomes["R1"].error == "R1: ValueError: the page is gone"


def test_an_extension_point_needs_a_known_settings_table(fake_entry_points) -> None:
    point = ExtensionPoint("snapdelta.printers", "printer", "my.Printer", "printers", subclass_check(object))
    fake_entry_points(EXTENSION_POINTS.group, "printers", lambda: point, "snapdelta-printers")
    broken = discover(EXTENSION_POINTS).broken["printers"]
    assert broken.error == "its settings table [printers] is not a table of snapdelta.toml"


def test_a_settings_reload_keeps_the_old_store_open_until_the_server_stops(tmp_path) -> None:
    from snapdelta.settings import load_settings
    from snapdelta_web.state import AppState

    closed: list[str] = []

    class Closing(FileStore):
        def close(self) -> None:
            closed.append(str(self.root))

    path = tmp_path / "snapdelta.toml"
    path.write_text(f'[store]\nlocation = "{tmp_path / "old"}"\n')
    state = AppState(load_settings(path), Closing(tmp_path / "old"), settings_option=path)
    path.write_text(f'[store]\nlocation = "{tmp_path / "new"}"\n')
    state.reload()
    assert state.store.location == str(tmp_path / "new") and closed == []
    state.close()
    assert closed == [str(tmp_path / "old")]
