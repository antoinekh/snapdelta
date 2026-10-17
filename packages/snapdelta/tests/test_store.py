from datetime import UTC, datetime

import pytest

from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.output import compare
from snapdelta.snapshot import save_snapshot
from snapdelta.store import FileStore, SqliteStore, default_store_path, open_store
from snapdelta.testing import StoreContract


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


def test_open_store_chooses_the_backend(monkeypatch, tmp_path) -> None:
    assert isinstance(open_store(str(tmp_path / "dir")), FileStore)
    assert isinstance(open_store(f"sqlite://{tmp_path}/a.db"), SqliteStore)
    assert isinstance(open_store(str(tmp_path / "b.db")), FileStore)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_store_path() == tmp_path / "snapdelta"


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
