from pathlib import Path

import pytest
from typer.testing import CliRunner

from snapdelta.cli import app
from snapdelta.errors import SnapdeltaError
from snapdelta.inventory import INVENTORY_GROUP, InventoryError, inventory_format, load_nornir
from snapdelta.settings import InventorySettings, Settings
from snapdelta.store import FileStore
from snapdelta.take import plan_sources, take_snapshots, target_names
from snapdelta_example import ExampleLiveSource

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "inventory"
runner = CliRunner()


def with_inventory(**platforms: str) -> Settings:
    return Settings(inventory=InventorySettings(location=str(EXAMPLE), platforms=platforms))


def test_a_host_takes_its_values_from_its_groups_then_the_defaults() -> None:
    inventory = load_nornir(EXAMPLE)
    r1, edge = inventory.hosts["R1"], inventory.hosts["EDGE1"]
    assert (r1.hostname, r1.port, r1.username, r1.platform) == ("192.0.2.1", 22, "lab", "example")
    assert edge.port == 2222 and edge.groups == ("edge", "lab")
    assert r1.data == {"owner": "network team", "role": "core", "site": "paris"}
    assert inventory.groups == ["core", "edge", "lab"]
    assert inventory.members("lab") == ["EDGE1", "R1", "R2"] and inventory.members("core") == ["R1", "R2"]


def test_the_password_is_not_in_the_repr(tmp_path) -> None:
    (tmp_path / "hosts.yaml").write_text("R1:\n  password: s3cret\n")
    (tmp_path / "hosts.yaml").chmod(0o644)
    inventory = load_nornir(tmp_path)
    assert inventory.hosts["R1"].password == "s3cret" and "s3cret" not in repr(inventory.hosts["R1"])
    assert inventory.warnings == [
        f"{tmp_path} holds passwords and other users can read it: chmod 600 {tmp_path}/*.yaml"
    ]


def test_a_bad_inventory_says_why(tmp_path) -> None:
    with pytest.raises(InventoryError, match="no hosts.yaml"):
        load_nornir(tmp_path)
    (tmp_path / "hosts.yaml").write_text("R1:\n  groups: [nope]\n")
    with pytest.raises(InventoryError, match="R1: group 'nope' is not in groups.yaml"):
        load_nornir(tmp_path)
    (tmp_path / "hosts.yaml").write_text("R1: [\n")
    with pytest.raises(InventoryError, match="not valid YAML"):
        load_nornir(tmp_path)
    with pytest.raises(InventoryError, match=r"no inventory format 'netbox' \(formats: nornir\)"):
        inventory_format("netbox")


def test_a_plugin_format_reads_its_location_with_its_settings(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from snapdelta import inventory as inventory_module
    from snapdelta.inventory import Host, Inventory, InventoryFormat
    from snapdelta.settings import InventoryFormatSettings, Secret, load_settings

    class NetboxSettings(InventoryFormatSettings):
        token: Secret | None = None

    class NetboxInventory(InventoryFormat):
        settings_class = NetboxSettings

        @classmethod
        def load(cls, location: str, settings: InventoryFormatSettings) -> Inventory:
            assert isinstance(settings, NetboxSettings) and settings.token is not None
            return Inventory({"R1": Host("R1", hostname=f"{location} {settings.token.value()}")}, [], location)

    plugin = SimpleNamespace(name="netbox", load=lambda: NetboxInventory)
    monkeypatch.setattr(inventory_module, "entry_points", lambda group: [plugin] if group == INVENTORY_GROUP else [])
    monkeypatch.setenv("NETBOX_TOKEN", "t0ken")
    (tmp_path / "snapdelta.toml").write_text(
        '[inventory]\nformat = "netbox"\nlocation = "https://netbox.example.net"\n'
        '[inventories.netbox]\ntoken = { env = "NETBOX_TOKEN" }\n'
    )
    settings = load_settings(tmp_path / "snapdelta.toml", installed={})
    assert settings.inventory.location == "https://netbox.example.net"
    hosts = settings.hosts()
    assert hosts is not None and hosts.hosts["R1"].hostname == "https://netbox.example.net t0ken"


def test_groups_and_all_targets_come_from_the_inventory() -> None:
    settings = with_inventory()
    assert target_names(None, ["R2"], False, settings, ["edge"]) == ["R2", "EDGE1"]
    assert target_names(None, [], True, settings) == ["EDGE1", "R1", "R2"]
    with pytest.raises(InventoryError, match="no group 'nope'"):
        target_names(None, [], False, settings, ["nope"])
    with pytest.raises(SnapdeltaError, match="a group needs an inventory"):
        target_names(None, [], False, Settings(), ["core"])


def test_the_platform_chooses_the_source() -> None:
    assert plan_sources(["R1", "EDGE1"], with_inventory(example="example-live")) == {
        "R1": ExampleLiveSource,
        "EDGE1": ExampleLiveSource,
    }
    with pytest.raises(
        SnapdeltaError, match=r"R9 is not in the inventory; R1: the source 'example' .* reads input files"
    ):
        plan_sources(["R9", "R1"], with_inventory())
    with pytest.raises(SnapdeltaError, match="give --source"):
        plan_sources(["R1"], Settings())


def test_the_source_gets_its_host(tmp_path) -> None:
    seen = {}

    class Recording(ExampleLiveSource):
        def load(self) -> None:
            seen[self.target] = self.host
            super().load()

    settings = with_inventory()
    taken, failures = take_snapshots(FileStore(tmp_path), {"R1": Recording}, settings=settings)
    assert failures == {} and list(taken) == ["R1"]
    assert seen["R1"] is not None and seen["R1"].hostname == "192.0.2.1"


def test_the_cli_takes_a_group_by_platform_and_lists_the_inventory(tmp_path) -> None:
    settings = tmp_path / "snapdelta.toml"
    settings.write_text(f'[inventory]\nlocation = "{EXAMPLE}"\nplatforms = {{ example = "example-live" }}\n')
    base = ["--settings", str(settings), "--store", str(tmp_path / "store")]
    taken = runner.invoke(app, [*base, "snapshot", "--group", "core", "--label", "before"])
    assert taken.exit_code == 0, taken.output
    assert "saved R1/" in taken.output and "saved R2/" in taken.output and "EDGE1" not in taken.output
    listed = runner.invoke(app, [*base, "inventory", "--group", "edge"])
    assert listed.output == "EDGE1\texample\texample-live\tedge,lab\t192.0.2.10:2222\n"
    none = runner.invoke(app, ["--store", str(tmp_path / "store"), "inventory"])
    assert none.exit_code == 2 and "no inventory" in none.output


def test_a_platform_with_the_name_of_a_source_needs_no_mapping(monkeypatch) -> None:
    from snapdelta import source as source_module
    from snapdelta.take import platform_source

    monkeypatch.setitem(source_module._REGISTERED, "example", ExampleLiveSource)
    assert plan_sources(["R1"], with_inventory()) == {"R1": ExampleLiveSource}
    assert platform_source(with_inventory(example="example-live"), "example") == "example-live"
    assert platform_source(with_inventory(), "nope") is None
