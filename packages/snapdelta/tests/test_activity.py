import logging
from pathlib import Path

import pytest

from snapdelta.activity import collecting, configure_logging
from snapdelta.errors import SnapdeltaError
from snapdelta.parallel import fetch_parallel
from snapdelta.settings import Settings
from snapdelta.take import diff_snapshots, load_source, take_snapshots
from snapdelta_example import ExampleLiveSettings, ExampleLiveSource

DEVICES = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "devices"
log = logging.getLogger("test.plugin")


@pytest.fixture(autouse=True)
def collected_at_info() -> None:
    configure_logging("info")


def test_a_load_keeps_the_messages_of_its_plugin_even_from_its_threads() -> None:
    def fetch() -> int:
        log.warning("slow answer")
        return 1

    with collecting("R1") as lines:
        log.info("start")
        fetch_parallel({"one": fetch, "two": fetch})
        log.debug("not kept at info")
    assert [(line.level, line.target, line.message) for line in lines] == [
        ("info", "R1", "start"),
        ("warning", "R1", "slow answer"),
        ("warning", "R1", "slow answer"),
    ]
    log.info("outside a load: kept nowhere")


def test_the_log_of_a_load_goes_into_its_snapshot(store) -> None:
    loaded = load_source(ExampleLiveSource, target="R1", settings=ExampleLiveSettings(data_dir=DEVICES))
    assert [line.message for line in loaded.log] == [f"read R1.json from {DEVICES}"]
    reference = store.save_snapshot(loaded)
    assert store.load_snapshot(reference).log == loaded.log


def test_take_snapshots_records_one_run_with_the_log_of_each_load(store) -> None:
    settings = Settings(sources={"example-live": {"data_dir": str(DEVICES)}})
    plan = {"R1": ExampleLiveSource, "R9": ExampleLiveSource}
    taken, failures = take_snapshots(store, plan, "before", settings=settings, origin="cli")
    [run] = store.runs()
    assert (run.kind, run.origin, run.status, run.targets) == ("snapshot", "cli", "partial", ["R1", "R9"])
    assert run.saved == [taken["R1"].reference] and run.errors == failures and "R9" in failures
    assert [(line.target, line.message) for line in run.log] == [("R1", f"read R1.json from {DEVICES}")]
    assert store.load_run(run.id) == run


def test_a_saved_diff_is_a_run_and_a_diff_that_saves_nothing_is_not(store, make_adapter) -> None:
    left = store.save_snapshot(make_adapter(label="before", description="old"))
    right = store.save_snapshot(make_adapter(label="after", description="new"))
    done = diff_snapshots(store, Settings(), left, right, note="Change 1", origin="api")
    [run] = store.runs(kind="diff")
    assert (run.origin, run.status, run.outputs, run.targets) == ("api", "ok", [done.name], ["R1"])
    diff_snapshots(store, Settings(), left, right, save=False)
    assert len(store.runs()) == 1
    with pytest.raises(SnapdeltaError):
        diff_snapshots(store, Settings(), left, "R1/nope")
    failed = store.runs(kind="diff")[0]
    assert failed.status == "failed" and "nope" in failed.errors["*"]


def test_take_snapshots_reports_each_target_as_it_starts_and_ends(store) -> None:
    settings = Settings(sources={"example-live": {"data_dir": str(DEVICES)}})
    heard: list[tuple[str, str]] = []
    take_snapshots(
        store,
        {"R1": ExampleLiveSource, "R9": ExampleLiveSource},
        settings=settings,
        progress=lambda target, step: heard.append((target, step.status)),
    )
    assert sorted(heard) == [("R1", "running"), ("R1", "saved"), ("R9", "failed"), ("R9", "running")]
