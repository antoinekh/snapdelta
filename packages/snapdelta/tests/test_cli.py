from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from snapdelta.cli import app
from snapdelta.store import FileStore, SqliteStore

runner = CliRunner()


@pytest.fixture(params=["file", "sqlite"])
def location(request, tmp_path) -> str:
    return str(tmp_path / "store") if request.param == "file" else f"sqlite://{tmp_path}/store.db"


def open_location(location: str):
    from snapdelta.store import open_store

    return open_store(location)


def test_diff_saves_the_output_and_exit_code_1(make_adapter, location) -> None:
    store = open_location(location)
    store.save_snapshot(make_adapter(label="before", description="old"))
    store.save_snapshot(make_adapter(label="after", description="new"))
    result = runner.invoke(app, ["--store", location, "diff", "20260925T100000Z-before", "20260925T100000Z-after"])
    assert result.exit_code == 1, result.output
    assert 'description: "old" -> "new"' in result.output
    names = [entry.name for entry in store.output_entries()]
    assert len(names) == 1
    shown = runner.invoke(app, ["--store", location, "output", names[0]])
    assert shown.exit_code == 1
    assert 'description: "old" -> "new"' in shown.output


def test_identical_snapshots_exit_code_0_and_no_save(make_adapter, location) -> None:
    store = open_location(location)
    store.save_snapshot(make_adapter(label="a"))
    store.save_snapshot(make_adapter(label="b"))
    args = ["--store", location, "diff", "20260925T100000Z-a", "20260925T100000Z-b", "--no-save", "--format", "json"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert '"schema": 3' in result.output
    assert store.output_entries() == []


def test_error_gives_exit_code_2_and_a_message(location) -> None:
    result = runner.invoke(app, ["--store", location, "diff", "R9@latest", "R9@latest"])
    assert result.exit_code == 2
    assert "error: no snapshot of R9" in result.output


def test_no_common_model_is_an_error(make_adapter, location) -> None:
    store = open_location(location)
    store.save_snapshot(make_adapter(label="config"))
    store.save_snapshot(make_adapter(label="state", with_config=False, with_state=True))
    result = runner.invoke(app, ["--store", location, "diff", "20260925T100000Z-config", "20260925T100000Z-state"])
    assert result.exit_code == 2
    assert "no loaded model in common" in result.output


def test_right_needs_a_snapshot_or_files(make_adapter, location) -> None:
    open_location(location).save_snapshot(make_adapter())
    result = runner.invoke(app, ["--store", location, "diff", "R1@latest"])
    assert result.exit_code == 2
    assert "give one right side: a RIGHT snapshot, or --live" in result.output
    both = runner.invoke(app, ["--store", location, "diff", "R1@latest", "R1@latest", "--target", "R2"])
    assert both.exit_code == 2 and "with targets, LEFT and RIGHT are labels" in both.output


def test_show_and_list(make_adapter, location) -> None:
    open_location(location).save_snapshot(make_adapter(with_state=True))
    listed = runner.invoke(app, ["--store", location, "list"])
    assert "R1/20260925T100000Z" in listed.output
    shown = runner.invoke(app, ["--store", location, "show", "R1@latest", "--model", "interface_state"])
    assert shown.exit_code == 0, shown.output
    assert "eth0" in shown.output


def test_unknown_source(tmp_path) -> None:
    sample = tmp_path / "a.xml"
    sample.write_text("<configuration/>")
    result = runner.invoke(app, ["--store", str(tmp_path), "snapshot", str(sample), "--source", "nope"])
    assert result.exit_code == 2
    assert "no source 'nope' is installed" in result.output


def test_store_option_selects_the_backend(tmp_path) -> None:
    assert isinstance(open_location(str(tmp_path / "dir")), FileStore)
    assert isinstance(open_location(f"sqlite://{tmp_path}/s.db"), SqliteStore)


def test_prune_refuses_a_bad_number(location, make_adapter) -> None:
    store = open_location(location)
    store.save_snapshot(make_adapter())
    for option in ("--keep=-1", "--older-than=-1"):
        refused = runner.invoke(app, ["--store", location, "prune", option, "--dry-run"])
        assert refused.exit_code == 2, refused.output
        assert "Invalid value" in refused.output
    too_big = runner.invoke(app, ["--store", location, "prune", "--older-than=1000000000000", "--dry-run"])
    assert too_big.exit_code == 2, too_big.output
    assert "too big" in too_big.output
    assert len(store.snapshot_entries("R1")) == 1


def test_info_delete_and_prune(make_adapter, location) -> None:
    store = open_location(location)
    for hour in (8, 9, 10):
        store.save_snapshot(make_adapter(captured_at=datetime(2026, 9, 25, hour, 0, tzinfo=UTC)))
    info = runner.invoke(app, ["--store", location, "info"])
    assert info.exit_code == 0, info.output
    assert "snapshots: 3" in info.output and f"backend: {store.backend}" in info.output
    preview = runner.invoke(app, ["--store", location, "prune", "--keep", "1", "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "would delete 2 snapshots and 0 outputs" in preview.output
    assert len(store.snapshot_entries("R1")) == 3
    pruned = runner.invoke(app, ["--store", location, "prune", "--keep", "2"])
    assert "deleted 1 snapshots and 0 outputs" in pruned.output
    deleted = runner.invoke(app, ["--store", location, "delete", "snapshot", "R1@latest"])
    assert deleted.exit_code == 0, deleted.output
    assert "deleted R1/20260925T100000Z" in deleted.output
    assert [entry.reference for entry in store.snapshot_entries("R1")] == ["R1/20260925T090000Z"]
    missing = runner.invoke(app, ["--store", location, "delete", "output", "nope"])
    assert missing.exit_code == 2
    no_rule = runner.invoke(app, ["--store", location, "prune"])
    assert no_rule.exit_code == 2 and "give a number of snapshots to keep" in no_rule.output


def test_snapshot_from_a_device_needs_no_file(location) -> None:
    taken = runner.invoke(app, ["--store", location, "snapshot", "--source", "example-live", "--target", "R2"])
    assert taken.exit_code == 0, taken.output
    assert "saved R2/" in taken.output
    no_device = runner.invoke(app, ["--store", location, "snapshot", "--source", "example-live"])
    assert no_device.exit_code == 2 and "give --target" in no_device.output
    no_files = runner.invoke(app, ["--store", location, "snapshot", "--source", "example", "--target", "R2"])
    assert no_files.exit_code == 2 and "give the input files" in no_files.output


def test_sources_lists_how_each_source_gets_its_data(tmp_path) -> None:
    result = runner.invoke(app, ["--store", str(tmp_path), "sources"])
    assert result.exit_code == 0, result.output
    assert "example\tsnapdelta-example\tfiles" in result.output
    assert "example-live\tsnapdelta-example\tfetches" in result.output


def test_snapshot_of_several_targets(location) -> None:
    result = runner.invoke(
        app, ["--store", location, "snapshot", "--source", "example-live", "--target", "R1", "--target", "R2"]
    )
    assert result.exit_code == 0, result.output
    assert "saved R1/" in result.output and "saved R2/" in result.output
    assert sorted(open_location(location).targets()) == ["R1", "R2"]


def test_snapshot_of_all_targets_with_jobs(location) -> None:
    result = runner.invoke(
        app, ["--store", location, "snapshot", "--source", "example-live", "--all-targets", "--jobs", "2"]
    )
    assert result.exit_code == 0, result.output
    assert sorted(open_location(location).targets()) == ["EDGE1", "R1", "R2"]


def test_a_failed_target_keeps_the_others_and_exits_2(location) -> None:
    result = runner.invoke(
        app, ["--store", location, "snapshot", "--source", "example-live", "--target", "R1", "--target", "R9"]
    )
    assert result.exit_code == 2
    assert "saved R1/" in result.output
    assert "error: R9: not reachable" in result.output
    assert open_location(location).targets() == ["R1"]


def test_several_targets_need_a_fetching_source(tmp_path) -> None:
    sample = tmp_path / "r1.json"
    sample.write_text('{"device": "R1", "interfaces": []}')
    store = ["--store", str(tmp_path / "store")]
    several = runner.invoke(
        app, [*store, "snapshot", str(sample), "--source", "example", "--target", "A", "--target", "B"]
    )
    assert several.exit_code == 2 and "one target at most with input files" in several.output
    every = runner.invoke(app, [*store, "snapshot", str(sample), "--source", "example", "--all-targets"])
    assert every.exit_code == 2 and "reads input files, so it lists no targets" in every.output


def test_diff_of_selected_models(make_adapter, location) -> None:
    store = open_location(location)
    store.save_snapshot(make_adapter(label="before", description="old"))
    store.save_snapshot(make_adapter(label="after", description="new"))
    names = [entry.reference for entry in store.snapshot_entries("R1")]
    only_vlan = runner.invoke(app, ["--store", location, "diff", names[1], names[0], "--model", "vlan"])
    assert only_vlan.exit_code == 0, only_vlan.output
    assert "not selected: interface" in only_vlan.output
    unknown = runner.invoke(app, ["--store", location, "diff", names[1], names[0], "--model", "bgp"])
    assert unknown.exit_code == 2 and "bgp is not loaded on both sides" in unknown.output


def test_settings_command_shows_values_and_origins(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "snapdelta.toml").write_text("[snapshot]\njobs = 2\n")
    result = runner.invoke(app, ["settings"])
    assert result.exit_code == 0, result.output
    assert f"file: {tmp_path / 'snapdelta.toml'}" in result.output
    assert "snapshot.jobs = 2  (file)" in result.output
    assert "web.port = 8765  (default)" in result.output
    as_json = runner.invoke(app, ["settings", "--json"])
    assert '"key": "snapshot.jobs"' in as_json.output


def test_a_bad_settings_file_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.toml").write_text("[web]\nport = 0\n")
    result = runner.invoke(app, ["--settings", str(tmp_path / "bad.toml"), "settings"])
    assert result.exit_code == 2 and "web.port" in result.output


def test_store_and_jobs_come_from_the_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SNAPDELTA_STORE", raising=False)
    (tmp_path / "snapdelta.toml").write_text('[store]\nlocation = "here"\n[snapshot]\njobs = 1\n')
    result = runner.invoke(app, ["snapshot", "--source", "example-live", "--all-targets"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "here" / "snapshots" / "R1").is_dir()


def test_a_plugin_bug_is_an_error_message_not_a_traceback(tmp_path) -> None:
    from snapdelta.source import register_source
    from snapdelta_example import ExampleLiveSource

    class Broken(ExampleLiveSource):
        source = "broken"

        def load(self) -> None:
            raise KeyError("interfaces")

    register_source(Broken)
    result = runner.invoke(app, ["--store", str(tmp_path), "snapshot", "--source", "broken", "--target", "R1"])
    assert result.exit_code == 2
    assert "error: R1: KeyError: 'interfaces'" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_a_command_without_a_store_creates_no_sqlite_file(tmp_path) -> None:
    path = tmp_path / "data" / "store.db"
    assert runner.invoke(app, ["--store", f"sqlite://{path}", "sources"]).exit_code == 0
    assert runner.invoke(app, ["--store", f"sqlite://{path}", "settings"]).exit_code == 0
    assert not path.exists() and not path.parent.exists()


def test_diff_and_output_print_any_format(make_adapter, location) -> None:
    store = open_location(location)
    store.save_snapshot(make_adapter(label="before", description="old"))
    store.save_snapshot(make_adapter(label="after", description="new"))
    base = ["--store", location, "diff", "20260925T100000Z-before", "20260925T100000Z-after"]
    markdown = runner.invoke(app, [*base, "--format", "markdown"])
    assert markdown.exit_code == 1 and '| eth0 | changed | description | "old" | "new" |' in markdown.output
    name = store.output_entries()[0].name
    table = runner.invoke(app, ["--store", location, "output", name, "--format", "csv"])
    assert 'interface,eth0,,changed,description,"""old""","""new"""' in table.output
    unknown = runner.invoke(app, [*base, "--format", "pdf"])
    assert unknown.exit_code == 2 and "no output format 'pdf' is installed" in unknown.output
    assert len(store.output_entries()) == 1


def test_diff_live_fetches_the_target_of_the_left_snapshot(tmp_path) -> None:
    location = str(tmp_path / "store")
    taken = runner.invoke(app, ["--store", location, "snapshot", "--source", "example-live", "--target", "R1"])
    assert taken.exit_code == 0, taken.output
    result = runner.invoke(app, ["--store", location, "diff", "R1@latest", "--live"])
    assert result.exit_code == 0, result.output
    store = FileStore(tmp_path / "store")
    live = store.snapshot_entries("R1")[0]
    assert live.label == "live" and f"saved {live.reference}" in result.output
    output = store.load_output(store.output_entries()[0].name)
    assert output.right.snapshot == live.reference
    quick = runner.invoke(app, ["--store", location, "diff", "R1@latest", "--live", "--no-save"])
    assert quick.exit_code == 0 and len(store.snapshot_entries("R1")) == 2


def test_diff_live_needs_a_source_that_fetches(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter())
    result = runner.invoke(app, ["--store", str(tmp_path / "store"), "diff", "R1@latest", "--live"])
    assert result.exit_code == 2 and "reads input files, so it cannot fetch" in result.output
    both = runner.invoke(app, ["--store", str(tmp_path / "store"), "diff", "R1@latest", "R1@latest", "--live"])
    assert both.exit_code == 2 and "give one right side" in both.output


def test_diff_with_a_profile_of_the_settings(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter(label="before", description="old"))
    store.save_snapshot(make_adapter(label="after", description="new"))
    settings = tmp_path / "snapdelta.toml"
    settings.write_text('[profiles.quiet]\nignore = ["interface.description"]\n')
    base = ["--store", str(tmp_path / "store"), "--settings", str(settings), "diff", "R1/20260925T100000Z-before"]
    quiet = runner.invoke(app, [*base, "R1/20260925T100000Z-after", "--profile", "quiet"])
    assert quiet.exit_code == 0, quiet.output
    assert "no difference" in quiet.output and "profile: quiet" in quiet.output
    unknown = runner.invoke(app, [*base, "R1/20260925T100000Z-after", "--profile", "nope"])
    assert unknown.exit_code == 2 and "no profile 'nope' in the settings (profiles: quiet)" in unknown.output
    shown = runner.invoke(app, ["--settings", str(settings), "settings"])
    assert "profiles.quiet.ignore = ['interface.description']  (file)" in shown.output


def test_a_broken_settings_file_stops_only_the_commands_that_need_it(tmp_path) -> None:
    settings = tmp_path / "bad.toml"
    settings.write_text('[web]\nport = "x"\n')
    for command in (["sources"], ["models"]):
        assert runner.invoke(app, ["--settings", str(settings), *command]).exit_code == 0
    listed = runner.invoke(app, ["--settings", str(settings), "list"])
    assert listed.exit_code == 2 and "web.port" in listed.output


def test_snapshot_saves_the_ticket(tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    args = ["--store", str(tmp_path / "store"), "snapshot", "--source", "example-live", "--target", "R1"]
    result = runner.invoke(app, [*args, "--label", "before", "--ticket", "  CHG-1 "])
    assert result.exit_code == 0, result.output
    assert store.load_snapshot(store.resolve_snapshot("R1@latest")).ticket == "CHG-1"


def test_diff_resolves_its_labels_in_its_ticket(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    older = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    store.save_snapshot(make_adapter(label="before", description="old", ticket="CHG-1", captured_at=older))
    store.save_snapshot(make_adapter(label="before", description="other", ticket="CHG-2"))
    store.save_snapshot(make_adapter(label="after", description="new", ticket="CHG-1"))
    base = ["--store", str(tmp_path / "store"), "diff", "R1@before", "R1@after", "--format", "json"]
    result = runner.invoke(app, [*base, "--ticket", "chg-1", "--note", "first\nsecond"])
    assert result.exit_code == 1, result.output
    saved = store.load_output(store.output_entries()[0].name)
    assert (saved.ticket, saved.note) == ("chg-1", "first\nsecond")
    assert '"old"' in result.output


def test_describe_sets_the_ticket_and_the_note_and_keeps_what_is_not_given(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter(label="before", description="old", ticket="CHG-7"))
    store.save_snapshot(make_adapter(label="after", description="new", ticket="CHG-7"))
    base = ["--store", str(tmp_path / "store")]
    runner.invoke(app, [*base, "diff", "R1@before", "R1@after", "--ticket", "CHG-7", "--note", "uplink"])
    name = store.output_entries()[0].name
    noted = runner.invoke(app, [*base, "describe", name, "--note", "r1 uplink"])
    assert noted.exit_code == 0 and noted.output == f"{name}: CHG-7\n"
    assert (store.load_output(name).ticket, store.load_output(name).note) == ("CHG-7", "r1 uplink")
    cleared = runner.invoke(app, [*base, "describe", name, "--note", ""])
    assert cleared.output == f"{name}: CHG-7\n"
    assert (store.load_output(name).ticket, store.load_output(name).note) == ("CHG-7", None)
    removed = runner.invoke(app, [*base, "describe", name, "--ticket", ""])
    assert removed.output == f"{name}: no ticket\n" and store.load_output(name).ticket is None


def test_list_filters_the_outputs_by_ticket_and_by_a_part_of_it(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter(label="before", description="old", ticket="CHG-7"))
    store.save_snapshot(make_adapter(label="after", description="new", ticket="CHG-7"))
    base = ["--store", str(tmp_path / "store")]
    runner.invoke(app, [*base, "diff", "R1@before", "R1@after", "--ticket", "CHG-7"])
    name = store.output_entries()[0].name
    exact = runner.invoke(app, [*base, "list", "--ticket", "chg-7"])
    assert exact.output.endswith(f"outputs:\n  {name}\n")
    part = runner.invoke(app, [*base, "list", "--target", "hg-7"])
    assert part.output.endswith(f"outputs:\n  {name}\n")
    other = runner.invoke(app, [*base, "list", "--ticket", "hg-7"])
    assert other.output.endswith("outputs:\n")


def test_accept_changes_after_a_review(make_adapter, tmp_path, monkeypatch) -> None:
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: "alice")
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter(label="before", description="old"))
    store.save_snapshot(make_adapter(label="after", description="new", extra_interface=True))
    base = ["--store", str(tmp_path / "store")]
    runner.invoke(app, [*base, "diff", "R1@before", "R1@after"])
    name = store.output_entries()[0].name
    one = runner.invoke(app, [*base, "accept", name, "interface:eth0", "--comment", "new description"])
    assert one.exit_code == 0 and one.output == f"{name}: accepted 1 of 2 changes\n"
    assert store.load_output(name).reviews["interface:eth0"].by == "alice"
    assert runner.invoke(app, [*base, "accept", name, "--all"]).output.endswith("accepted 2 of 2 changes\n")
    assert runner.invoke(app, [*base, "accept", name, "--all", "--undo"]).output.endswith("accepted 0 of 2 changes\n")
    missing = runner.invoke(app, [*base, "accept", name])
    assert missing.exit_code == 2 and "give the keys of the changes, or --all" in missing.output


def test_the_activity_lists_the_runs_and_shows_one(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter(label="before", description="old"))
    store.save_snapshot(make_adapter(label="after", description="new"))
    base = ["--store", str(tmp_path / "store")]
    runner.invoke(app, [*base, "diff", "R1@before", "R1@after"])
    listed = runner.invoke(app, [*base, "activity"])
    assert listed.exit_code == 0 and "-diff-" in listed.output and " cli  ok " in listed.output
    run_id = listed.output.split()[0]
    shown = runner.invoke(app, [*base, "activity", run_id])
    assert shown.output.startswith(f"{run_id}: diff from cli, ok") and "diff: " in shown.output
    assert '"kind": "diff"' in runner.invoke(app, [*base, "activity", "--json"]).output


def test_a_command_closes_the_store_that_it_opened(tmp_path) -> None:
    from snapdelta.plugins import register_plugin
    from snapdelta.settings import StoreBackendSettings
    from snapdelta.store import STORES, FileStore

    closed: list[str] = []

    class ClosingStore(FileStore):
        backend = "closing"

        @classmethod
        def open(cls, address: str, settings: StoreBackendSettings) -> FileStore:
            return cls(tmp_path / address)

        def close(self) -> None:
            closed.append(str(self.root))

    register_plugin(STORES, "closing", ClosingStore)
    assert runner.invoke(app, ["--store", "closing://one", "info"]).exit_code == 0
    assert closed == [str(tmp_path / "one")]
    assert runner.invoke(app, ["--store", "closing://one", "sources"]).exit_code == 0
    assert closed == [str(tmp_path / "one")]
