from datetime import UTC, datetime

import pytest

from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.output import compare
from snapdelta.snapshot import save_snapshot
from snapdelta.store import FileStore, SqliteStore, default_store_path, open_store


def at(hour: int) -> datetime:
    return datetime(2026, 9, 25, hour, 0, tzinfo=UTC)


def test_open_store_chooses_the_backend(monkeypatch, tmp_path) -> None:
    assert isinstance(open_store(str(tmp_path / "dir")), FileStore)
    assert isinstance(open_store(f"sqlite://{tmp_path}/a.db"), SqliteStore)
    assert isinstance(open_store(str(tmp_path / "b.db")), FileStore)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_store_path() == tmp_path / "snapdelta"


def test_save_load_and_names(store, make_adapter) -> None:
    reference = store.save_snapshot(make_adapter(label="before swap"))
    assert reference == "R1/20260925T100000Z-before_swap"
    assert store.read_snapshot(reference).label == "before swap"
    loaded = store.load_snapshot(reference)
    assert (loaded.target, loaded.reference) == ("R1", reference)
    late = store.save_snapshot(make_adapter(captured_at=at(11)))
    assert store.resolve_snapshot("R1@latest") == late
    assert store.resolve_snapshot("20260925T100000Z-before_swap") == reference
    assert store.resolve_snapshot(f"{reference}.json") == reference


def test_existing_snapshot_is_not_overwritten(store, make_adapter) -> None:
    store.save_snapshot(make_adapter())
    with pytest.raises(SnapdeltaError, match="already exists"):
        store.save_snapshot(make_adapter())


def test_unknown_ambiguous_and_unsafe_references(store, make_adapter) -> None:
    with pytest.raises(NotFoundError, match="no snapshot of R9"):
        store.resolve_snapshot("R9@latest")
    with pytest.raises(NotFoundError, match="no snapshot matches"):
        store.resolve_snapshot("nothing")
    for target in ("R1", "R2"):
        store.save_snapshot(make_adapter(target=target))
    with pytest.raises(NotFoundError, match="matches 2 snapshots"):
        store.resolve_snapshot("20260925T100000Z")
    for unsafe in ("../R1/20260925T100000Z", "R1/..", "R1/../../x", "/etc/passwd"):
        with pytest.raises(NotFoundError):
            store.read_snapshot(unsafe)
        with pytest.raises(NotFoundError):
            store.resolve_snapshot(unsafe)
    with pytest.raises(NotFoundError):
        store.load_output("../outputs/x")


def test_devices_and_entries(store, make_adapter) -> None:
    assert store.targets() == []
    for target in ("R2", "R1", "EDGE1"):
        store.save_snapshot(make_adapter(target=target))
    store.save_snapshot(make_adapter(label="before", captured_at=at(9)))
    assert store.targets() == ["EDGE1", "R1", "R2"]
    assert store.targets("r") == ["R1", "R2"]
    assert store.targets("ge1") == ["EDGE1"]
    entries = store.snapshot_entries("R1")
    assert [(entry.reference, entry.label) for entry in entries] == [
        ("R1/20260925T100000Z", None),
        ("R1/20260925T090000Z-before", "before"),
    ]
    assert entries[1].captured_at == at(9)
    assert store.snapshot_entries("R9") == []


def test_outputs(store, make_adapter) -> None:
    names = [store.save_output(compare(make_adapter(), make_adapter(target=right))) for right in ("R2", "EDGE1")]
    assert all(name.endswith(("-R1-vs-R2", "-R1-vs-EDGE1")) for name in names)
    entries = store.output_entries()
    assert {entry.name for entry in entries} == set(names)
    assert [entry.name for entry in entries] == sorted((entry.name for entry in entries), reverse=True)
    assert [entry.right for entry in store.output_entries("edge")] == ["EDGE1"]
    assert store.resolve_output(names[0]) == names[0]
    assert store.load_output(names[0]).right.target in ("R2", "EDGE1")
    with pytest.raises(NotFoundError, match="no output matches"):
        store.resolve_output("nope")


def test_delete_and_info(store, make_adapter) -> None:
    reference = store.save_snapshot(make_adapter())
    name = store.save_output(compare(make_adapter(target="R2"), store.load_snapshot(reference)))
    info = store.info()
    assert (info.backend, info.targets, info.snapshots, info.outputs) == (store.backend, 1, 1, 1)
    assert info.size_bytes > 0
    store.delete_snapshot(reference)
    store.delete_output(name)
    assert (store.targets(), store.output_entries()) == ([], [])
    with pytest.raises(NotFoundError):
        store.delete_snapshot(reference)
    with pytest.raises(NotFoundError):
        store.delete_output(name)


def test_prune_keeps_what_any_rule_keeps(store, make_adapter) -> None:
    for hour in (8, 9, 10, 11):
        store.save_snapshot(make_adapter(captured_at=at(hour)))
    store.save_snapshot(make_adapter(target="R2", captured_at=at(8)))
    by_rank = store.prune_plan(keep=2)
    assert by_rank.snapshots == ["R1/20260925T090000Z", "R1/20260925T080000Z"]
    by_age = store.prune_plan(older_than=at(10), target="R1")
    assert by_age.snapshots == ["R1/20260925T090000Z", "R1/20260925T080000Z"]
    both = store.prune_plan(keep=1, older_than=at(10))
    # R2 has one snapshot only: the newest of its target, so the rank rule keeps it.
    assert both.snapshots == ["R1/20260925T090000Z", "R1/20260925T080000Z"]
    store.apply(both)
    assert [entry.reference for entry in store.snapshot_entries("R1")] == ["R1/20260925T110000Z", "R1/20260925T100000Z"]
    assert store.targets() == ["R1", "R2"]


def test_prune_refuses_a_negative_keep(store, make_adapter) -> None:
    store.save_snapshot(make_adapter(captured_at=at(8)))
    with pytest.raises(SnapdeltaError, match="cannot be negative"):
        store.prune_plan(keep=-1)
    assert len(store.prune_plan(keep=0).snapshots) == 1
    assert len(store.snapshot_entries("R1")) == 1


def test_prune_outputs_and_rules(store, make_adapter) -> None:
    name = store.save_output(compare(make_adapter(), make_adapter(target="R2")))
    future = datetime(2100, 1, 1, tzinfo=UTC)
    assert store.prune_plan(older_than=future, outputs=True).outputs == [name]
    assert store.prune_plan(older_than=future, outputs=True, target="R9").outputs == []
    with pytest.raises(SnapdeltaError, match="give a number of snapshots to keep"):
        store.prune_plan()
    with pytest.raises(SnapdeltaError, match="needs an age"):
        store.prune_plan(keep=1, outputs=True)


def test_file_store_reads_the_current_layout_and_paths(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path)
    path = tmp_path / "snapshots" / "R1" / "20260925T100000Z.json"
    path.parent.mkdir(parents=True)
    save_snapshot(make_adapter(), path)
    assert store.resolve_snapshot(str(path)) == "R1/20260925T100000Z"
    outside = tmp_path / "elsewhere.json"
    save_snapshot(make_adapter(), outside)
    with pytest.raises(NotFoundError):
        store.resolve_snapshot(str(outside))
    (tmp_path / "snapshots" / "R1" / "notes.json").write_text("{}")
    assert [entry.reference for entry in store.snapshot_entries("R1")] == ["R1/20260925T100000Z"]


def test_sqlite_tables_use_target_columns(tmp_path) -> None:
    import sqlite3

    store = SqliteStore(tmp_path / "s.db")
    with sqlite3.connect(store.path) as db:
        snapshots = [row[1] for row in db.execute("pragma table_info(snapshots)")]
        outputs = [row[1] for row in db.execute("pragma table_info(outputs)")]
    assert snapshots == ["reference", "target", "captured_at", "label", "source", "body"]
    assert outputs == ["name", "created_at", "left_target", "right_target", "body"]


def test_open_store_from_settings(tmp_path) -> None:
    from snapdelta.settings import Settings, StoreSettings
    from snapdelta.store import open_store_from

    location = f"sqlite://{tmp_path / 's.db'}"
    settings = Settings(store=StoreSettings(location=location), stores={"sqlite": {"busy_timeout_s": 5}})
    store = open_store_from(settings)
    assert isinstance(store, SqliteStore) and store.busy_timeout_s == 5


def test_two_outputs_of_one_pair_in_one_second_get_two_names(store, make_adapter) -> None:
    output = compare(make_adapter(), make_adapter(description="other"))
    first, second = store.save_output(output), store.save_output(output)
    assert second == first.replace("Z-", "Z.2-", 1)
    assert [entry.name for entry in store.output_entries()] == [second, first]
    assert {(entry.left, entry.right) for entry in store.output_entries()} == {("R1", "R1")}


def test_a_row_deleted_after_the_check_is_not_found(tmp_path, make_adapter) -> None:
    store = SqliteStore(tmp_path / "store.db")
    reference = store.save_snapshot(make_adapter())
    name = store.save_output(compare(store.load_snapshot(reference), store.load_snapshot(reference)))
    store._delete_snapshot(reference)
    store._delete_output(name)
    with pytest.raises(NotFoundError):
        store._read_snapshot(reference)
    with pytest.raises(NotFoundError):
        store._read_output(name)


def test_a_location_with_a_scheme_opens_its_backend(tmp_path) -> None:
    assert isinstance(open_store(f"file://{tmp_path}/files"), FileStore)
    assert isinstance(open_store(f"sqlite://{tmp_path}/store.db"), SqliteStore)
    with pytest.raises(SnapdeltaError, match=r"no store backend 's3' is installed \(installed: file, sqlite\)"):
        open_store("s3://bucket/snapdelta")


def test_a_plugin_backend_opens_by_its_scheme(tmp_path) -> None:
    from snapdelta.plugins import register_plugin
    from snapdelta.settings import StoreBackendSettings
    from snapdelta.store import STORES

    class MemoryStore(FileStore):
        backend = "memory"

        @classmethod
        def open(cls, address: str, settings: StoreBackendSettings) -> FileStore:
            return cls(tmp_path / address)

    register_plugin(STORES, "mem", MemoryStore)
    opened = open_store("mem://one")
    assert isinstance(opened, MemoryStore) and opened.root == tmp_path / "one"


def test_a_saved_output_gets_a_title_and_a_description(store, make_adapter) -> None:
    name = store.save_output(compare(make_adapter(), make_adapter(description="other")))
    changed = store.describe_output(name, "  Change 42 on R1 ", "Add VLAN 30 on the trunk")
    assert (changed.title, changed.description) == ("Change 42 on R1", "Add VLAN 30 on the trunk")
    assert store.load_output(name).title == "Change 42 on R1"
    assert store.describe_output(name, "", None).title is None
    with pytest.raises(SnapdeltaError, match="title"):
        store.describe_output(name, "two\nlines", None)


def test_target_at_label_is_the_newest_snapshot_with_that_label(store, make_adapter) -> None:
    from datetime import timedelta

    first = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    store.save_snapshot(make_adapter(label="before", captured_at=first))
    newest_before = store.save_snapshot(make_adapter(label="before", captured_at=first + timedelta(hours=1)))
    after = store.save_snapshot(make_adapter(label="after", captured_at=first + timedelta(hours=2)))
    assert store.resolve_snapshot("R1@before") == newest_before
    assert store.resolve_snapshot("R1@after") == store.resolve_snapshot("R1@latest") == after
    with pytest.raises(NotFoundError, match="no snapshot labelled 'nope' of R1"):
        store.resolve_snapshot("R1@nope")
