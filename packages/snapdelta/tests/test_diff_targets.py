import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from snapdelta.activity import Run
from snapdelta.cli import app
from snapdelta.errors import SnapdeltaError
from snapdelta.settings import load_settings
from snapdelta.store import FileStore
from snapdelta.take import diff_targets, snapshot_targets

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin"
runner = CliRunner()


@pytest.fixture
def lab(tmp_path):
    """R1, R2 and EDGE1 before and after a change of the MTU of eth0 on R2; the settings file of the lab."""
    devices = tmp_path / "devices"
    shutil.copytree(EXAMPLE / "devices", devices)
    path = tmp_path / "snapdelta.toml"
    path.write_text(
        f'[store]\nlocation = "{tmp_path / "store"}"\n[inventory]\nlocation = "{EXAMPLE / "inventory"}"\n'
        f'[sources.example-live]\ndata_dir = "{devices}"\n'
    )
    settings = load_settings(path)
    store = FileStore(tmp_path / "store")
    snapshot_targets(store, settings, "example-live", all_targets=True, label="before")
    r2 = json.loads((devices / "R2.json").read_text())
    r2["interfaces"][0]["mtu"] = 1400
    (devices / "R2.json").write_text(json.dumps(r2))
    snapshot_targets(store, settings, "example-live", targets=["R1", "R2"], label="after")
    return path, settings, store


def test_a_group_is_one_diff_per_host_in_one_run(lab) -> None:
    _, settings, store = lab
    results = diff_targets(store, settings, "@before", "@after", groups=["core"], note="VLAN 20")
    assert list(results) == ["R1", "R2"]
    assert not results["R1"].changed and results["R2"].changed and results["R2"].output.note == "VLAN 20"
    run = store.runs(kind="diff")[0]
    assert run.targets == ["R1", "R2"] and run.outputs == [results["R1"].name, results["R2"].name]
    assert run.has_differences is True and run.status == "ok"


def test_diff_targets_pairs_the_snapshots_of_its_ticket(lab) -> None:
    path, settings, store = lab
    now = datetime.now(UTC)
    snapshot_targets(
        store,
        settings,
        "example-live",
        targets=["R1"],
        label="before",
        ticket="CHG-1",
        captured_at=now + timedelta(hours=1),
    )
    snapshot_targets(
        store,
        settings,
        "example-live",
        targets=["R1"],
        label="after",
        ticket="CHG-1",
        captured_at=now + timedelta(hours=2),
    )
    device = path.parent / "devices" / "R1.json"
    data = json.loads(device.read_text())
    data["interfaces"][0]["mtu"] = 1400
    device.write_text(json.dumps(data))
    snapshot_targets(
        store,
        settings,
        "example-live",
        targets=["R1"],
        label="after",
        ticket="CHG-2",
        captured_at=now + timedelta(hours=3),
    )
    [result] = diff_targets(store, settings, "before", "after", targets=["R1"], ticket="CHG-1").values()
    assert result.output is not None and result.output.ticket == "CHG-1"
    assert not result.changed
    [unscoped] = diff_targets(store, settings, "before", "after", targets=["R1"]).values()
    assert unscoped.changed


def test_a_label_missing_in_the_ticket_fails_with_the_ticket(lab) -> None:
    _, settings, store = lab
    [result] = diff_targets(store, settings, "before", live=True, targets=["R1"], ticket="CHG-9").values()
    assert result.error is not None and "in ticket 'CHG-9'" in result.error


def test_a_live_diff_takes_its_snapshot_in_the_ticket(lab) -> None:
    _, settings, store = lab
    snapshot_targets(store, settings, "example-live", targets=["R1"], label="start", ticket="CHG-1")
    [result] = diff_targets(store, settings, "start", live=True, targets=["R1"], ticket="CHG-1").values()
    assert result.live is not None and store.load_snapshot(result.live).ticket == "CHG-1"
    assert result.output is not None and result.output.ticket == "CHG-1"


def test_all_targets_with_a_ticket_are_those_that_have_the_label_in_it(lab) -> None:
    _, settings, store = lab
    snapshot_targets(store, settings, "example-live", targets=["R2"], label="start", ticket="CHG-1")
    assert list(diff_targets(store, settings, "start", live=True, all_targets=True, ticket="CHG-1")) == ["R2"]


def test_a_target_without_its_snapshot_fails_alone(lab) -> None:
    _, settings, store = lab
    results = diff_targets(store, settings, "before", "after", all_targets=True)
    assert set(results) == {"EDGE1", "R1", "R2"}
    assert results["EDGE1"].error == f"no snapshot labelled 'after' of EDGE1 in {store.location}"
    assert store.runs(kind="diff")[0].status == "partial"
    with pytest.raises(SnapdeltaError, match="give targets, groups or all_targets"):
        diff_targets(store, settings, "before", "after")


def test_a_live_diff_of_many_targets_takes_them_in_one_snapshot_run(lab) -> None:
    _, settings, store = lab
    results = diff_targets(store, settings, "before", targets=["R1", "R2", "R9"], live=True)
    assert results["R1"].live and results["R1"].live.startswith("R1/") and results["R2"].changed
    assert results["R9"].error == f"no snapshot labelled 'before' of R9 in {store.location}"
    snapshot_run = store.runs(kind="snapshot")[0]
    assert snapshot_run.targets == ["R1", "R2"]
    assert store.runs(kind="diff")[0].saved == [results["R1"].live, results["R2"].live]
    with pytest.raises(SnapdeltaError, match="saves the snapshots"):
        diff_targets(store, settings, "before", targets=["R1"], live=True, save=False)


def test_the_cli_prints_each_diff_and_a_summary(lab) -> None:
    path, _, _ = lab
    base = ["--settings", str(path), "diff"]
    changed = runner.invoke(app, [*base, "--group", "core", "@before", "@after"])
    assert changed.exit_code == 1, changed.output
    assert "changed: R2" in changed.output and "unchanged: R1" in changed.output
    assert changed.output.count("left:  R") == 2
    same = runner.invoke(app, [*base, "--target", "R1", "@before", "@after"])
    assert same.exit_code == 0 and "changed: none" in same.output
    failed = runner.invoke(app, [*base, "--all-targets", "@before", "@after"])
    assert failed.exit_code == 2 and "error: no snapshot labelled 'after' of EDGE1" in failed.output
    listed = runner.invoke(app, [*base, "--group", "core", "@before", "@after", "--format", "json", "--no-save"])
    outputs = json.loads(listed.stdout)
    assert [item["left"]["target"] for item in outputs] == ["R1", "R2"]
    plain = runner.invoke(app, [*base, "--group", "core", "R1@before", "@after"])
    assert plain.exit_code == 2 and "LEFT and RIGHT are labels" in plain.output


def test_a_run_of_snapdelta_0_1_reads_with_its_one_diff() -> None:
    run = Run.model_validate(
        {
            "id": "20261014T080000000000Z-diff-abcd",
            "kind": "diff",
            "origin": "cli",
            "started_at": "2026-10-14T08:00:00Z",
            "ended_at": "2026-10-14T08:00:01Z",
            "status": "ok",
            "output": "20261014T080000Z-R1-vs-R1",
        }
    )
    assert run.outputs == ["20261014T080000Z-R1-vs-R1"]


def test_a_target_whose_source_is_gone_fails_alone(lab) -> None:
    from snapdelta.plugins import unregister_plugin
    from snapdelta.source import SOURCES, register_source
    from snapdelta.take import load_source
    from snapdelta_example import ExampleLiveSource

    _, settings, store = lab

    class Gone(ExampleLiveSource):
        source = "gone"

    register_source(Gone)
    later = datetime.now(UTC) + timedelta(hours=1)
    loaded = load_source(Gone, target="EDGE1", label="before", captured_at=later, settings=settings.source(Gone))
    store.save_snapshot(loaded)
    unregister_plugin(SOURCES, "gone")
    results = diff_targets(store, settings, "before", targets=["EDGE1", "R1"], live=True)
    assert results["EDGE1"].error and "no source 'gone' is installed" in results["EDGE1"].error
    assert results["R1"].output is not None


def test_an_expected_change_names_a_compared_target(lab) -> None:
    from snapdelta.expected import ExpectedChange

    _, settings, store = lab
    wrong = [ExpectedChange(model="vlan", object="20", change="added", target="r1")]
    with pytest.raises(SnapdeltaError, match="name targets that this diff does not compare: r1"):
        diff_targets(store, settings, "before", "after", groups=["core"], expected=wrong)
