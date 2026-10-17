import json
import shutil
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
    results = diff_targets(store, settings, "@before", "@after", groups=["core"], title="CHG-1")
    assert list(results) == ["R1", "R2"]
    assert not results["R1"].changed and results["R2"].changed and results["R2"].output.title == "CHG-1"
    run = store.runs(kind="diff")[0]
    assert run.targets == ["R1", "R2"] and run.outputs == [results["R1"].name, results["R2"].name]
    assert run.has_differences is True and run.status == "ok"


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
