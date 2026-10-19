import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI

from snapdelta.output import compare
from snapdelta.settings import Settings, StoreSettings
from snapdelta.store import FileStore, Store
from snapdelta_example import ExampleSource
from snapdelta_web.app import create_app
from snapdelta_web.state import AppState

SAMPLES = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "samples"
BEFORE = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
AFTER = BEFORE + timedelta(hours=1)


@dataclass(frozen=True)
class Names:
    before: str = "R1/20260925T100000Z-before"
    after: str = "R1/20260925T110000Z-after"
    config: str = "R1/20260925T113000Z-config"
    same: str = "20260925T110000Z-R1-vs-R1"
    partial: str = "20260925T110100Z-R1-vs-R1"


@pytest.fixture(scope="session")
def base_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """R1 before and after from the example samples, plus a config-only R1; two outputs."""
    root = tmp_path_factory.mktemp("store")
    store = FileStore(root)
    config_only = tmp_path_factory.mktemp("input") / "r1-config.json"
    after = json.loads((SAMPLES / "r1-after.json").read_text())
    config_only.write_text(json.dumps({key: after[key] for key in ("device", "interfaces", "vlans")}))
    adapters = [
        ExampleSource(files=[SAMPLES / "r1-before.json"], label="before", captured_at=BEFORE),
        ExampleSource(files=[SAMPLES / "r1-after.json"], label="after", captured_at=AFTER),
        ExampleSource(files=[config_only], label="config", captured_at=AFTER + timedelta(minutes=30)),
    ]
    for adapter in adapters:
        adapter.load()
        store.save_snapshot(adapter)
    names = Names()
    for minute, (left, right) in enumerate([(names.before, names.after), (names.before, names.config)]):
        output = compare(store.load_snapshot(left), store.load_snapshot(right))
        output.created_at = AFTER + timedelta(minutes=minute)
        store.save_output(output)
    return root


@pytest.fixture
def store(base_store: Path, tmp_path: Path) -> Store:
    """A copy per test, so a test that writes never changes another test's store."""
    root = tmp_path / "store"
    shutil.copytree(base_store, root)
    return FileStore(root)


@pytest.fixture
def ticketed(store: Store) -> Store:
    """R1 before in two tickets (CHG-2 the newer) and R1 after in CHG-1, next to the snapshots of no ticket."""
    day = BEFORE + timedelta(days=1)
    for sample, label, ticket, moment in [
        ("r1-before", "before", "CHG-1", day),
        ("r1-before", "before", "CHG-2", day + timedelta(hours=1)),
        ("r1-after", "after", "CHG-1", day + timedelta(hours=2)),
    ]:
        adapter = ExampleSource(files=[SAMPLES / f"{sample}.json"], label=label, ticket=ticket, captured_at=moment)
        adapter.load()
        store.save_snapshot(adapter)
    return store


@pytest.fixture
def names() -> Names:
    return Names()


@pytest.fixture
def big_store(store: Store, names: Names) -> Store:
    """30 more targets with 60 snapshots each, written as files from one real snapshot, plus 60 outputs."""
    assert isinstance(store, FileStore)
    body = json.loads((store.snapshots_dir / f"{names.before}.json").read_text())
    for number in range(30):
        target = f"DEV{number:03d}"
        directory = store.snapshots_dir / target
        directory.mkdir(parents=True)
        for index in range(60):
            moment = BEFORE + timedelta(minutes=index)
            body["target"] = target
            (directory / f"{moment:%Y%m%dT%H%M%S}Z.json").write_text(json.dumps(body))
    output = (store.outputs_dir / f"{names.same}.json").read_text()
    for index in range(60):
        moment = AFTER + timedelta(hours=1, minutes=index)
        (store.outputs_dir / f"{moment:%Y%m%dT%H%M%S}Z-DEV{index:03d}-vs-R1.json").write_text(output)
    return store


@pytest.fixture(autouse=True)
def no_machine_settings(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings files of the machine never change a test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.delenv("SNAPDELTA_SETTINGS", raising=False)


type MakeApp = Callable[..., FastAPI]


@pytest.fixture
def make_app() -> MakeApp:
    """The web app of `store`, with `settings` or the defaults."""

    def build(store: Store, settings: Settings | None = None) -> FastAPI:
        return create_app(AppState(settings or Settings(store=StoreSettings(location=store.location)), store))

    return build
