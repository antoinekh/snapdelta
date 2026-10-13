import time
from pathlib import Path

import pytest

from snapdelta.errors import LoadError, SnapdeltaError
from snapdelta.settings import Settings, SourceSettings
from snapdelta.store import FileStore
from snapdelta.take import load_source, take_snapshots, target_names
from snapdelta_example import ExampleLiveSource, ExampleSource


class SlowSource(ExampleLiveSource):
    def load(self) -> None:
        time.sleep(0.2)
        super().load()


def test_target_names_keep_the_order_and_drop_duplicates_and_blanks() -> None:
    assert target_names(ExampleLiveSource, ["R2", " R1 ", "R2", ""], False, Settings()) == ["R2", "R1"]


def test_all_targets_come_from_the_source() -> None:
    assert target_names(ExampleLiveSource, ["R9"], True, Settings()) == ["EDGE1", "R1", "R2", "R9"]
    with pytest.raises(SnapdeltaError, match="reads input files, so it lists no targets"):
        target_names(ExampleSource, [], True, Settings())


def test_targets_are_fetched_at_the_same_time(tmp_path) -> None:
    store = FileStore(tmp_path)
    start = time.monotonic()
    taken, failures = take_snapshots(store, dict.fromkeys(["R1", "R2", "EDGE1"], SlowSource), label="live")
    assert time.monotonic() - start < 0.5
    assert failures == {}
    assert list(taken) == ["R1", "R2", "EDGE1"]
    assert taken["R1"].reference.startswith("R1/") and taken["R1"].reference.endswith("-live")
    assert sorted(store.targets()) == ["EDGE1", "R1", "R2"]


def test_a_failed_target_is_reported_and_the_others_are_saved(tmp_path) -> None:
    store = FileStore(tmp_path)
    taken, failures = take_snapshots(store, dict.fromkeys(["R1", "R9"], ExampleLiveSource), jobs=1)
    assert list(taken) == ["R1"]
    assert failures["R9"].startswith("R9: not reachable")


class DirSettings(SourceSettings):
    data_dir: Path | None = None


class DirSource(ExampleLiveSource):
    source = "dir"
    settings_class = DirSettings

    @classmethod
    def targets(cls, settings: SourceSettings | None = None) -> list[str]:
        folder = getattr(settings or cls.default_settings(), "data_dir", None)
        return sorted(path.stem for path in folder.glob("*.json")) if folder else []


def test_target_names_and_take_pass_the_settings(tmp_path) -> None:
    (tmp_path / "X1.json").write_text("{}")
    settings = Settings(sources={"dir": {"data_dir": str(tmp_path)}})
    assert target_names(DirSource, [], True, settings) == ["X1"]


class BrokenSource(ExampleLiveSource):
    source = "broken"

    def load(self) -> None:
        raise KeyError("interfaces")


def test_a_plugin_bug_becomes_a_load_error_that_names_the_target() -> None:
    with pytest.raises(LoadError, match=r"^R1: KeyError: 'interfaces'$") as caught:
        load_source(BrokenSource, target="R1")
    assert isinstance(caught.value.__cause__, KeyError)
