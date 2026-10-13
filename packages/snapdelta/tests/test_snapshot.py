import json

import pytest

from snapdelta.errors import NotFoundError, SchemaVersionError, SnapdeltaError
from snapdelta.snapshot import StoredSource, load_snapshot, read_snapshot_file, save_snapshot, top_level_models

EXCLUDE = {"adapter", "model_flags"}


def test_round_trip_keeps_data_and_metadata(make_adapter, tmp_path) -> None:
    original = make_adapter(with_state=True, label="before")
    path = tmp_path / "snapshots" / "R1.json"
    save_snapshot(original, path)
    loaded = load_snapshot(path)
    assert isinstance(loaded, StoredSource)
    data = original.dict(exclude_defaults=False, exclude=EXCLUDE)
    assert loaded.dict(exclude_defaults=False, exclude=EXCLUDE) == data
    assert (loaded.target, loaded.source, loaded.label) == ("R1", "example", "before")
    assert loaded.captured_at == original.captured_at
    assert loaded.loaded == original.loaded
    assert loaded.top_level == original.top_level
    assert loaded.reference == "snapshots/R1"


def test_file_content(make_adapter, tmp_path) -> None:
    path = tmp_path / "R1.json"
    save_snapshot(make_adapter(), path)
    value = json.loads(path.read_text())
    assert value["schema"] == 2
    assert value["loaded"] == ["interface", "address", "vlan"]
    assert value["data"]["interface"]["eth0"]["description"] == "to R2"
    assert value["data"]["interface"]["eth0"]["addresses"] == ["eth0__10.0.0.1/30"]
    assert "model_flags" not in value["data"]["interface"]["eth0"]
    assert set(value["versions"]) == {"diffsync", "snapdelta", "snapdelta-example"}
    assert not list(tmp_path.glob("*.tmp"))


def test_config_only_snapshot_keeps_its_coverage(make_adapter, tmp_path) -> None:
    path = tmp_path / "R1.json"
    save_snapshot(make_adapter(), path)
    assert load_snapshot(path).top_level == ["interface", "vlan"]


def test_unknown_schema(make_adapter, tmp_path) -> None:
    path = tmp_path / "R1.json"
    save_snapshot(make_adapter(), path)
    value = json.loads(path.read_text())
    value["schema"] = 99
    path.write_text(json.dumps(value))
    with pytest.raises(SchemaVersionError, match="schema 99"):
        load_snapshot(path)


def test_missing_and_broken_files(tmp_path) -> None:
    with pytest.raises(NotFoundError):
        load_snapshot(tmp_path / "none.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    with pytest.raises(SnapdeltaError, match="not a valid JSON file"):
        load_snapshot(broken)
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"schema": 2, "target": "R1"}))
    with pytest.raises(SnapdeltaError, match="not a valid snapshot"):
        load_snapshot(incomplete)


def test_read_snapshot_file_gives_the_metadata_without_an_adapter(make_adapter, tmp_path) -> None:
    from snapdelta.snapshot import SnapshotFile, read_snapshot_file

    path = tmp_path / "R1.json"
    save_snapshot(make_adapter(label="before"), path)
    snapshot = read_snapshot_file(path)
    assert isinstance(snapshot, SnapshotFile)
    assert (snapshot.target, snapshot.label, snapshot.loaded) == ("R1", "before", ["interface", "address", "vlan"])


def test_snapshot_adapter_is_a_subclass_of_the_vendor_adapter(make_adapter, tmp_path) -> None:
    original = make_adapter()
    path = tmp_path / "R1.json"
    save_snapshot(original, path)
    assert issubclass(type(load_snapshot(path)), type(original))


def test_unknown_vendor_names_the_missing_plugin(make_adapter, tmp_path) -> None:
    path = tmp_path / "R1.json"
    save_snapshot(make_adapter(), path)
    value = json.loads(path.read_text())
    value["source"] = "nope"
    path.write_text(json.dumps(value))
    with pytest.raises(SnapdeltaError, match="no source 'nope' is installed"):
        load_snapshot(path)


def test_top_level_models_come_from_the_file(make_adapter, tmp_path) -> None:
    path = tmp_path / "R1.json"
    save_snapshot(make_adapter(with_state=True), path)
    assert top_level_models(read_snapshot_file(path)) == ["interface", "vlan", "interface_state"]
