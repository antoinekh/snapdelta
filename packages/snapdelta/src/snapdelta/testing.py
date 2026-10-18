"""Contract tests for plugins: subclass a contract in the tests of a plugin, and give its fixture.

`StoreContract` holds the behaviour that every store backend must have, `SourceContract` the behaviour that every
source must have. The built-in stores pass `StoreContract` in the tests of snapdelta. Needs pytest:
`pip install "snapdelta[testing]"`. See docs/plugins.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from diffsync import DiffSyncModel

from .activity import Run, RunStatus
from .errors import NotFoundError, SnapdeltaError
from .output import Output, compare, new_review
from .plugins import Severity, register_plugin, unregister_plugin
from .source import SOURCES, Source
from .store import FileStore, Store, open_store

FIRST = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


class SampleInterface(DiffSyncModel):
    _modelname = "interface"
    _identifiers = ("name",)
    _attributes = ("mtu", "description")

    name: str
    mtu: int | None = None
    description: str | None = None


class SampleSource(Source):
    """A source of the contracts: one model, read from JSON files such as `{"R1": {"eth0": 1500}}`."""

    source = "snapdelta-sample"
    interface = SampleInterface
    top_level = ["interface"]

    def load(self) -> None:
        for item in self.read_inputs():
            for target, interfaces in json.loads(item.content).items():
                for name, mtu in interfaces.items():
                    self.add_or_warn(SampleInterface(name=name, mtu=mtu))
                self.finish_load(target, ["interface"])


def sample(
    target: str = "R1",
    label: str | None = None,
    captured_at: datetime = FIRST,
    interfaces: dict[str, int] | None = None,
) -> SampleSource:
    """A loaded sample source, with no input file."""
    adapter = SampleSource(target=target, label=label, captured_at=captured_at)
    for name, mtu in (interfaces or {"eth0": 1500, "eth1": 9000}).items():
        adapter.add(SampleInterface(name=name, mtu=mtu))
    adapter.finish_load(target, ["interface"])
    return adapter


def sample_run(second: int, kind: str = "snapshot") -> Run:
    started = FIRST + timedelta(seconds=second)
    return Run(
        id=f"{started:%Y%m%dT%H%M%S%f}Z-{kind}-abcd",
        kind=kind,
        origin="python",
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        status=RunStatus.OK,
    )


class StoreContract:
    """The behaviour of a store backend; a subclass gives the fixture `store`, a new and empty store each time."""

    @pytest.fixture
    def store(self) -> Store:
        raise NotImplementedError("give a `store` fixture: a new, empty store of the backend under test")

    @pytest.fixture(autouse=True)
    def sample_source(self) -> Iterator[type[SampleSource]]:
        """The sample source, registered for the time of a test, so that the store reads its snapshots back."""
        register_plugin(SOURCES, SampleSource.source, SampleSource)
        yield SampleSource
        unregister_plugin(SOURCES, SampleSource.source)

    def test_a_snapshot_reads_back_as_it_was_saved(self, store: Store) -> None:
        saved = sample(label="before swap")
        reference = store.save_snapshot(saved)
        assert reference == "R1/20260101T100000Z-before_swap"
        assert store.has_snapshot(reference)
        assert store.read_snapshot(reference).label == "before swap"
        loaded = store.load_snapshot(reference)
        assert (loaded.target, loaded.reference, loaded.captured_at) == ("R1", reference, FIRST)
        assert not compare(saved, loaded).has_differences()

    def test_a_snapshot_is_never_overwritten(self, store: Store) -> None:
        store.save_snapshot(sample())
        with pytest.raises(SnapdeltaError, match="already exists"):
            store.save_snapshot(sample())

    def test_a_reference_resolves_by_label_by_name_and_to_the_latest(self, store: Store) -> None:
        before = store.save_snapshot(sample(label="before"))
        newest_before = store.save_snapshot(sample(label="before", captured_at=FIRST + timedelta(hours=1)))
        after = store.save_snapshot(sample(label="after", captured_at=FIRST + timedelta(hours=2)))
        assert store.resolve_snapshot("R1@latest") == after
        assert store.resolve_snapshot("R1@before") == newest_before
        assert store.resolve_snapshot(before.partition("/")[2]) == before
        assert store.resolve_snapshot(f"{before}.json") == before
        with pytest.raises(NotFoundError, match="no snapshot labelled 'nope' of R1"):
            store.resolve_snapshot("R1@nope")

    def test_an_unknown_an_ambiguous_or_an_unsafe_reference_is_not_found(self, store: Store) -> None:
        with pytest.raises(NotFoundError, match="no snapshot of R9"):
            store.resolve_snapshot("R9@latest")
        with pytest.raises(NotFoundError, match="no snapshot matches"):
            store.resolve_snapshot("nothing")
        for target in ("R1", "R2"):
            store.save_snapshot(sample(target=target))
        with pytest.raises(NotFoundError, match="matches 2 snapshots"):
            store.resolve_snapshot("20260101T100000Z")
        for unsafe in ("../R1/20260101T100000Z", "R1/..", "R1/../../x", "/etc/passwd"):
            assert not store.has_snapshot(unsafe)
            with pytest.raises(NotFoundError):
                store.read_snapshot(unsafe)
        with pytest.raises(NotFoundError):
            store.load_output("../outputs/x")

    def test_the_targets_and_their_snapshots_newest_first(self, store: Store) -> None:
        assert store.targets() == []
        for target in ("R2", "R1", "EDGE1"):
            store.save_snapshot(sample(target=target))
        store.save_snapshot(sample(label="before", captured_at=FIRST - timedelta(hours=1)))
        assert store.targets() == ["EDGE1", "R1", "R2"]
        assert store.targets("r") == ["R1", "R2"]
        assert store.targets("GE1") == ["EDGE1"]
        entries = store.snapshot_entries("R1")
        assert [(entry.reference, entry.label) for entry in entries] == [
            ("R1/20260101T100000Z", None),
            ("R1/20260101T090000Z-before", "before"),
        ]
        assert entries[1].captured_at == FIRST - timedelta(hours=1)
        assert store.snapshot_entries("R9") == []

    def test_an_output_reads_back_and_lists_newest_first(self, store: Store) -> None:
        names = [store.save_output(compare(sample(), sample(target=right))) for right in ("R2", "EDGE1")]
        assert names[0].endswith("-R1-vs-R2") and names[1].endswith("-R1-vs-EDGE1")
        entries = store.output_entries()
        assert {entry.name for entry in entries} == set(names)
        assert [entry.name for entry in entries] == sorted((entry.name for entry in entries), reverse=True)
        assert [entry.right for entry in store.output_entries("edge")] == ["EDGE1"]
        assert store.resolve_output(f"{names[0]}.json") == names[0]
        assert store.load_output(names[0]).right.target == "R2"
        with pytest.raises(NotFoundError, match="no output matches"):
            store.resolve_output("nope")

    def test_an_output_is_found_by_its_change(self, store: Store) -> None:
        first = store.save_output(compare(sample(), sample(target="R2")))
        second = store.save_output(compare(sample(), sample(target="R3")))
        store.describe_output(second, None, None, "CHG-1042")
        assert [entry.name for entry in store.output_entries("chg-10")] == [second]
        assert [entry.name for entry in store.output_entries("r2")] == [first]
        store.describe_output(second, None, None, "")
        assert store.output_entries("chg-10") == []

    def test_an_output_keeps_the_order_of_its_models_and_objects(self, store: Store) -> None:
        # A diff shows its models and objects in this order: a backend that sorts the keys changes every report.
        names = {"zeta": 1, "alpha": 2, "mid": 3, "b": 4}
        output = compare(sample(interfaces=names), sample(interfaces={name: 9000 for name in names}))
        saved = store.load_output(store.save_output(output))
        assert list(saved.diff["interface"]) == list(output.diff["interface"]) == list(names)
        assert saved.summary == output.summary

    def test_two_outputs_of_one_pair_in_one_second_get_two_names(self, store: Store) -> None:
        output = compare(sample(), sample(interfaces={"eth0": 9000}))
        first, second = store.save_output(output), store.save_output(output)
        assert second == first.replace("Z-", "Z.2-", 1)
        assert [entry.name for entry in store.output_entries()] == [second, first]

    def test_the_title_and_the_reviews_of_an_output_change_and_stay(self, store: Store) -> None:
        output = compare(sample(), sample(interfaces={"eth0": 9000, "eth1": 9000}))
        name = store.save_output(output)
        store.describe_output(name, "Change 42", "MTU of eth0", None)
        [key] = output.change_keys()
        store.review_output(name, key, new_review("ops", "expected"))
        saved = _reopened(store).load_output(name)
        assert (saved.title, saved.description) == ("Change 42", "MTU of eth0")
        assert saved.reviews[key].comment == "expected" and saved.review_counts() == (1, 1)
        assert store.review_output(name, key, None).reviews == {}

    def test_delete_and_info(self, store: Store) -> None:
        reference = store.save_snapshot(sample())
        name = store.save_output(compare(sample(target="R2"), store.load_snapshot(reference)))
        info = store.info()
        assert (info.backend, info.targets, info.snapshots, info.outputs) == (store.backend, 1, 1, 1)
        assert info.size_bytes >= 0
        store.delete_snapshot(reference)
        store.delete_output(name)
        assert (store.targets(), store.output_entries()) == ([], [])
        with pytest.raises(NotFoundError):
            store.delete_snapshot(reference)
        with pytest.raises(NotFoundError):
            store.delete_output(name)

    def test_prune_deletes_what_no_rule_keeps(self, store: Store) -> None:
        for hour in (8, 9, 10, 11):
            store.save_snapshot(sample(captured_at=FIRST.replace(hour=hour)))
        store.save_snapshot(sample(target="R2", captured_at=FIRST.replace(hour=8)))
        name = store.save_output(compare(sample(), sample(target="R2")))
        plan = store.prune_plan(keep=1, older_than=FIRST, outputs=True)
        assert plan.snapshots == ["R1/20260101T090000Z", "R1/20260101T080000Z"] and plan.outputs == []
        store.apply(plan)
        assert [entry.reference for entry in store.snapshot_entries("R1")] == [
            "R1/20260101T110000Z",
            "R1/20260101T100000Z",
        ]
        assert store.targets() == ["R1", "R2"]
        future = datetime(2100, 1, 1, tzinfo=UTC)
        assert store.prune_plan(older_than=future, outputs=True).outputs == [name]

    def test_the_runs_newest_first_and_the_newest_only(self, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        from . import store as store_module

        monkeypatch.setattr(store_module, "RUNS_KEPT", 3)
        for second in range(4):
            store.save_run(sample_run(second, "diff" if second == 2 else "snapshot"))
        assert [run.id for run in store.runs()] == [sample_run(3).id, sample_run(2, "diff").id, sample_run(1).id]
        assert [run.kind for run in store.runs(kind="diff")] == ["diff"]
        assert store.runs(limit=1)[0].id == sample_run(3).id
        assert store.load_run(sample_run(1).id) == sample_run(1)
        assert store.info().runs == 3
        for missing in (sample_run(0).id, "../../etc/passwd"):
            with pytest.raises(NotFoundError):
                store.load_run(missing)

    def test_the_location_opens_the_same_store(self, store: Store) -> None:
        reference = store.save_snapshot(sample())
        assert _reopened(store).resolve_snapshot("R1@latest") == reference


def _reopened(store: Store) -> Store:
    return open_store(store.location)


class SourceContract:
    """The behaviour of a source; a subclass gives the fixture `load`, which loads one target, a new load each call."""

    @pytest.fixture
    def load(self) -> Callable[[], Source]:
        raise NotImplementedError("give a `load` fixture: a function that returns a new load of one target")

    def test_the_doctor_finds_no_error(self, load: Callable[[], Source]) -> None:
        cls = type(load())
        problems = [item.message for item in SOURCES.inspect(cls.source, cls) if item.severity == Severity.ERROR]
        assert problems == []
        assert SOURCES.check(cls.source, cls) is None

    def test_a_load_names_its_target_its_time_and_its_models(self, load: Callable[[], Source]) -> None:
        loaded = load()
        target, captured_at = loaded.require_loaded()
        assert target and captured_at.tzinfo is not None
        assert loaded.loaded and set(loaded.loaded) <= set(type(loaded).model_order())

    def test_two_loads_of_the_same_data_have_no_difference(self, load: Callable[[], Source]) -> None:
        # A list in another order, a default that depends on the run: a false difference at each compare.
        output = compare(load(), load())
        assert not output.has_differences(), _changes(output)

    def test_a_snapshot_reads_back_with_no_difference(self, load: Callable[[], Source], tmp_path: Path) -> None:
        loaded = load()
        cls = type(loaded)
        register_plugin(SOURCES, cls.source, cls)
        try:
            store = FileStore(tmp_path / "store")
            stored = store.load_snapshot(store.save_snapshot(loaded))
            output = compare(loaded, stored)
        finally:
            unregister_plugin(SOURCES, cls.source)
        assert not output.has_differences(), _changes(output)
        assert stored.loaded == loaded.loaded


def _changes(output: Output) -> str:
    return json.dumps(output.diff, indent=2, default=str)
