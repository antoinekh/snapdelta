import json
import sqlite3
from datetime import UTC, datetime

import pytest

from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.output import compare
from snapdelta.settings import Settings
from snapdelta.snapshot import save_snapshot
from snapdelta.store import (
    FileStore,
    OutputQuery,
    SnapshotQuery,
    SqliteStore,
    default_store_location,
    open_store,
    open_store_from,
)
from snapdelta.testing import StoreContract, sample


class TestFileStore(StoreContract):
    @pytest.fixture
    def store(self, tmp_path) -> FileStore:
        return FileStore(tmp_path / "store")


class TestSqliteStore(StoreContract):
    @pytest.fixture
    def store(self, tmp_path) -> SqliteStore:
        return SqliteStore(tmp_path / "store.db")


def at(hour: int) -> datetime:
    return datetime(2026, 9, 25, hour, 0, tzinfo=UTC)


@pytest.mark.parametrize("window", [{"limit": -1}, {"offset": -1}, {"limit": 2**63}, {"offset": 2**63}])
def test_a_page_window_outside_the_integer_range_of_sql_is_refused(window) -> None:
    with pytest.raises(SnapdeltaError, match="from 0 to 9223372036854775807"):
        SnapshotQuery(**window)


def test_the_largest_page_window_is_accepted() -> None:
    assert SnapshotQuery(limit=2**63 - 1, offset=2**63 - 1).page([1, 2]) == []


def test_the_targets_of_a_query_are_not_one_string() -> None:
    with pytest.raises(SnapdeltaError, match="a tuple of names"):
        SnapshotQuery(targets="R1")  # type: ignore[arg-type]


def test_the_blank_and_repeated_targets_of_a_query_are_dropped() -> None:
    assert SnapshotQuery(targets=("R2", " ", "R1", "R2", "")).targets == ("R2", "R1")


def test_a_reference_with_a_slash_that_is_not_found_lists_no_snapshot(monkeypatch, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(sample())

    def listing(query: SnapshotQuery = SnapshotQuery()) -> list:
        raise AssertionError("the list was read")

    monkeypatch.setattr(store, "snapshot_entries", listing)
    with pytest.raises(NotFoundError, match="no snapshot matches 'R1/20200101T000000Z' in "):
        store.resolve_snapshot("R1/20200101T000000Z")


def test_open_store_chooses_the_backend(monkeypatch, tmp_path) -> None:
    assert isinstance(open_store(str(tmp_path / "dir")), FileStore)
    assert isinstance(open_store(f"sqlite://{tmp_path}/a.db"), SqliteStore)
    assert isinstance(open_store(str(tmp_path / "b.db")), FileStore)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_store_location() == f"sqlite://{tmp_path}/snapdelta/snapdelta.db"


def test_no_location_opens_the_sqlite_store_of_the_data_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("SNAPDELTA_STORE", raising=False)
    store = open_store_from(Settings())
    assert isinstance(store, SqliteStore)
    assert store.info().backend == "sqlite"
    assert (tmp_path / "snapdelta" / "snapdelta.db").is_file()


def test_prune_refuses_a_negative_keep(store, make_adapter) -> None:
    store.save_snapshot(make_adapter(captured_at=at(8)))
    with pytest.raises(SnapdeltaError, match="cannot be negative"):
        store.prune_plan(keep=-1)
    assert len(store.prune_plan(keep=0).snapshots) == 1
    assert len(store.snapshot_entries(SnapshotQuery(targets=("R1",)))) == 1


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
    assert [entry.reference for entry in store.snapshot_entries(SnapshotQuery(targets=("R1",)))] == [
        "R1/20260925T100000Z"
    ]


def test_the_file_store_lists_a_snapshot_without_validating_its_data(make_adapter, tmp_path) -> None:
    import json

    store = FileStore(tmp_path)
    path = tmp_path / "snapshots" / "R1" / "20260925T100000Z.json"
    path.parent.mkdir(parents=True)
    save_snapshot(make_adapter(ticket="chg  1"), path)
    path.write_text(json.dumps(json.loads(path.read_text()) | {"data": "not the models"}))
    [entry] = store.snapshot_entries(SnapshotQuery(targets=("R1",), ticket="CHG 1"))
    assert (entry.ticket, entry.source) == ("chg 1", "example")
    with pytest.raises(SnapdeltaError):
        store.read_snapshot(entry.reference)


def test_a_snapshot_saved_with_unmapped_still_reads(make_adapter, tmp_path) -> None:
    import json

    store = FileStore(tmp_path)
    path = tmp_path / "snapshots" / "R1" / "20260925T100000Z.json"
    path.parent.mkdir(parents=True)
    save_snapshot(make_adapter(), path)
    path.write_text(json.dumps(json.loads(path.read_text()) | {"unmapped": ["interfaces.eth0.speed"]}))
    assert store.read_snapshot("R1/20260925T100000Z").target == "R1"


def test_sqlite_tables_use_target_columns(tmp_path) -> None:
    store = SqliteStore(tmp_path / "s.db")
    with sqlite3.connect(store.path) as db:
        snapshots = [row[1] for row in db.execute("pragma table_info(snapshots)")]
        outputs = [row[1] for row in db.execute("pragma table_info(outputs)")]
    assert snapshots == ["reference", "target", "captured_at", "label", "source", "ticket", "ticket_key", "body"]
    assert outputs == ["name", "created_at", "left_target", "right_target", "ticket", "ticket_key", "body"]


def test_sqlite_store_of_an_older_snapdelta_is_refused(tmp_path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("create table snapshots (reference text primary key, target text not null, body text not null)")
    with pytest.raises(SnapdeltaError, match="made by an older snapdelta: make it again"):
        SqliteStore(path)


def test_a_store_of_a_development_build_is_refused(tmp_path) -> None:
    path = tmp_path / "dev.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "create table snapshots (reference text primary key, target text not null, captured_at text not null, "
            "label text, source text not null, ticket text, ticket_key text, body text not null)"
        )
        # The development build wrote the label as it was given, the release writes it as the reference writes it.
        db.execute(
            "insert into snapshots values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "R1/20260101T100000Z-before_swap",
                "R1",
                "2026-01-01T10:00:00+00:00",
                "before swap",
                "junos",
                None,
                None,
                "{}",
            ),
        )
    with pytest.raises(SnapdeltaError, match="made by an older snapdelta: make it again"):
        SqliteStore(path)


def test_a_file_store_of_an_older_snapdelta_is_refused(tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    path = store.snapshots_dir / "R1" / "20260925T100000Z-before.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": 2, "target": "R1", "source": "example", "label": "before"}))
    with pytest.raises(SnapdeltaError, match="made by an older snapdelta: make it again"):
        store.snapshot_entries()
    with pytest.raises(SnapdeltaError, match="made by an older snapdelta: make it again"):
        store.read_snapshot("R1/20260925T100000Z-before")



def test_open_store_from_settings(tmp_path) -> None:
    from snapdelta.settings import Settings, StoreSettings
    from snapdelta.store import open_store_from

    location = f"sqlite://{tmp_path / 's.db'}"
    settings = Settings(store=StoreSettings(location=location), stores={"sqlite": {"busy_timeout_s": 5}})
    store = open_store_from(settings)
    assert isinstance(store, SqliteStore) and store.busy_timeout_s == 5


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
    with pytest.raises(SnapdeltaError, match=r"no store backend 's3' is installed \(installed: file, "):
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


def test_a_saved_output_gets_a_ticket_and_keeps_it(store, make_adapter) -> None:
    name = store.save_output(compare(make_adapter(), make_adapter()))
    assert store.load_output(name).ticket is None
    assert store.describe_output(name, ticket="  CHG-1042 ").ticket == "CHG-1042"
    assert store.load_output(name).ticket == "CHG-1042"
    assert store.describe_output(name, note="VLAN 20").ticket == "CHG-1042"
    assert store.describe_output(name, ticket="").ticket is None
    with pytest.raises(SnapdeltaError, match="ticket"):
        store.describe_output(name, ticket="x" * 81)


def test_a_saved_output_gets_a_note(store, make_adapter) -> None:
    name = store.save_output(compare(make_adapter(), make_adapter(description="other")))
    changed = store.describe_output(name, note="  Change 42 on R1\nAdd VLAN 30 on the trunk ")
    assert changed.note == "Change 42 on R1\nAdd VLAN 30 on the trunk"
    assert store.load_output(name).note == changed.note
    assert store.describe_output(name, note="").note is None


def test_the_file_store_reads_no_output_for_an_empty_query(tmp_path, make_adapter, monkeypatch) -> None:
    store = FileStore(tmp_path / "store")
    name = store.save_output(compare(make_adapter(), make_adapter()))

    def unread(*_: object) -> None:
        raise AssertionError("an empty query must not read an output")

    monkeypatch.setattr(FileStore, "_read_output", unread)
    assert [entry.name for entry in store.output_entries(OutputQuery(search=""))] == [name]


def test_the_file_store_reads_no_output_for_a_query_that_names_a_target(tmp_path, make_adapter, monkeypatch) -> None:
    store = FileStore(tmp_path / "store")
    name = store.save_output(compare(make_adapter(), make_adapter()))
    target = store.load_output(name).left.target

    def unread(*_: object) -> None:
        raise AssertionError("a query that names a target must not read an output")

    monkeypatch.setattr(FileStore, "_read_output", unread)
    assert [entry.name for entry in store.output_entries(OutputQuery(search=target.lower()))] == [name]


def test_a_search_by_part_of_a_ticket_folds_the_case_beyond_ascii(tmp_path, make_adapter) -> None:
    for store in (FileStore(tmp_path / "store"), SqliteStore(tmp_path / "store.db")):
        name = store.save_output(compare(make_adapter(), make_adapter()))
        store.describe_output(name, ticket="Évolution LOT 2")
        assert [entry.name for entry in store.output_entries(OutputQuery(search="ÉVOLUTION"))] == [name]
        assert [entry.name for entry in store.output_entries(OutputQuery(search="évolution lot"))] == [name]
