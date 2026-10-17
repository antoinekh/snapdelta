from pathlib import Path

from diffsync import DiffSyncModel
from typer.testing import CliRunner

from snapdelta.catalog import catalog
from snapdelta.cli import app
from snapdelta.doctor import diagnose, plugin_reports
from snapdelta.inventory import Host
from snapdelta.settings import InventorySettings, Settings
from snapdelta.source import SOURCES, Source, model_groups, register_source
from snapdelta.store import FileStore
from snapdelta.take import take_snapshots

INVENTORY = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "inventory"


class Interface(DiffSyncModel):
    _modelname = "interface"
    _identifiers = ("name",)
    name: str


class Session(DiffSyncModel):
    """A BGP session to a peer: only the edge routers have them."""

    _modelname = "session"
    _identifiers = ("peer",)
    _groups = ("edge",)
    _children = {"route": "routes"}
    peer: str
    routes: list[str] = []


class Route(DiffSyncModel):
    _modelname = "route"
    _identifiers = ("prefix",)
    prefix: str


class Router(Source):
    source = "router"
    fetches = True
    interface = Interface
    session = Session
    route = Route
    top_level = ["interface", "session"]
    fetched: dict[str, list[str]] = {}

    def load(self) -> None:
        self.add(Interface(name="eth0"))
        if self.wants("session"):
            session = Session(peer="192.0.2.99")
            self.add(session)
            self.add_child_or_warn("session", session.get_unique_id(), Route(prefix="10.0.0.0/8"))
        type(self).fetched[self.target or ""] = list(self.wanted)
        self.finish_load(self.target, ["interface", "session", "route"])


def test_a_model_with_groups_is_for_the_hosts_of_these_groups() -> None:
    assert model_groups(Session) == ("edge",) and model_groups(Interface) == ()
    assert Router.models_for(Host("EDGE1", groups=("edge", "lab"))) == ["interface", "session", "route"]
    assert Router.models_for(Host("R1", groups=("core", "lab"))) == ["interface"]
    assert Router.models_for(None) == ["interface", "session", "route"]
    assert Router(target="R1", host=Host("R1", groups=("core",))).wants("route") is False


def test_a_snapshot_records_the_models_of_its_host(tmp_path) -> None:
    register_source(Router)
    settings = Settings(inventory=InventorySettings(location=str(INVENTORY)))
    store = FileStore(tmp_path)
    taken, failures = take_snapshots(store, {"R1": Router, "EDGE1": Router}, settings=settings)
    assert failures == {}
    assert taken["R1"].loaded == ["interface"] and taken["EDGE1"].loaded == ["interface", "session", "route"]
    assert Router.fetched["R1"] == ["interface"]
    assert store.read_snapshot(taken["R1"].reference).loaded == ["interface"]


def test_the_catalog_the_cli_and_the_doctor_show_the_groups(tmp_path) -> None:
    register_source(Router)
    [models] = [found for found in catalog({"router": Router})]
    assert {model.name: model.groups for model in models.models} == {"interface": [], "session": ["edge"], "route": []}
    listed = CliRunner().invoke(app, ["models", "--source", "router"])
    assert "  session  id: peer  groups: edge" in listed.output
    assert [report.problems for report in plugin_reports([SOURCES]) if report.name == "router"] == [[]]
    path = tmp_path / "snapdelta.toml"
    path.write_text(f'[store]\nlocation = "{tmp_path / "store"}"\n[inventory]\nlocation = "{INVENTORY}"\n')
    assert [
        problem.message for check in diagnose(path).checks if check.subject == "inventory" for problem in check.problems
    ] == []


def test_the_doctor_finds_wrong_groups(tmp_path) -> None:
    class Peer(DiffSyncModel):
        _modelname = "peer"
        _identifiers = ("name",)
        _groups = "edge"
        name: str

    class Child(DiffSyncModel):
        _modelname = "route"
        _identifiers = ("prefix",)
        _groups = ("edge",)
        prefix: str

    class Parent(DiffSyncModel):
        _modelname = "session"
        _identifiers = ("peer",)
        _groups = ("border",)
        _children = {"route": "routes"}
        peer: str
        routes: list[str] = []

    class Wrong(Router):
        source = "wrong"
        peer = Peer
        session = Parent
        route = Child
        top_level = ["peer", "session"]

    register_source(Wrong)
    [problems] = [
        [item.message for item in report.problems] for report in plugin_reports([SOURCES]) if report.name == "wrong"
    ]
    assert problems == [
        "peer: _groups must be a tuple of inventory group names",
        "route: _groups of a child model are not used, the child follows its parent",
    ]
    path = tmp_path / "snapdelta.toml"
    path.write_text(f'[store]\nlocation = "{tmp_path / "store"}"\n[inventory]\nlocation = "{INVENTORY}"\n')
    [inventory] = [check for check in diagnose(path).checks if check.subject == "inventory"]
    assert "source wrong: the model session is for groups that the inventory has not: border" in [
        problem.message for problem in inventory.problems
    ]
