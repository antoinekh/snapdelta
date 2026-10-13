import pytest

from snapdelta.errors import NotFoundError
from snapdelta_web.access import SnapshotCache


def test_names_resolve_inside_the_store(store, names) -> None:
    assert store.resolve_snapshot(names.before) == names.before
    assert store.resolve_snapshot("R1@latest") == names.config
    assert store.resolve_output(names.same) == names.same


def test_a_file_outside_the_store_is_not_found(store, tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    for reference in (str(outside), "../store/snapshots/R1/20260925T100000Z-before.json", "R1/.."):
        with pytest.raises(NotFoundError):
            store.resolve_snapshot(reference)
    with pytest.raises(NotFoundError):
        store.resolve_output(str(outside))


def test_side_snapshot_loads_the_referenced_snapshot(store, names) -> None:
    left = SnapshotCache().side(store, store.load_output(names.same).left)
    assert left is not None
    assert (left.reference, left.target) == (names.before, "R1")


def test_side_snapshot_is_none_when_the_snapshot_is_gone(store, names) -> None:
    output = store.load_output(names.same)
    store.delete_snapshot(names.after)
    assert SnapshotCache().side(store, output.right) is None


def test_the_cache_reads_a_snapshot_once_and_sees_a_delete(store, names, monkeypatch) -> None:
    cache = SnapshotCache()
    loads = []
    load = store.load_snapshot
    monkeypatch.setattr(store, "load_snapshot", lambda reference: loads.append(reference) or load(reference))
    first, second = cache.load(store, names.before), cache.load(store, names.before)
    assert first is second and loads == [names.before]
    store.delete_snapshot(names.before)
    with pytest.raises(NotFoundError):
        cache.load(store, names.before)
