import time
from pathlib import Path

import pytest
from snapdelta.errors import FetchError
from snapdelta.inputs import InputFile
from snapdelta.output import compare

from snapdelta_lab import FrrSource, SrlinuxSource
from snapdelta_lab.capture import capture
from snapdelta_lab.commands import SavedRunner, without_keys
from snapdelta_lab.settings import RouterSettings

from .test_frr import OUTPUTS

SAVED = RouterSettings(outputs=OUTPUTS)


def loaded(cls: type[FrrSource] | type[SrlinuxSource], target: str) -> FrrSource | SrlinuxSource:
    source = cls(target=target, settings=SAVED)
    source.load()
    return source


def test_each_source_lists_its_saved_routers() -> None:
    assert (FrrSource.targets(SAVED), SrlinuxSource.targets(SAVED)) == (["r1", "r2"], ["r3"])


def test_a_load_fills_every_model_and_records_its_outputs() -> None:
    source = loaded(FrrSource, "r1")
    assert source.loaded == ["interface", "bgp_peer", "bgp_route"]
    assert len(source.get_all("bgp_route")) == 8
    assert sorted(item.name for item in source.inputs) == [
        "bgp.json",
        "interfaces.json",
        "routes-10.12.10.2.json",
        "routes-10.13.0.3.json",
    ]
    assert source.warnings == []
    srl = loaded(SrlinuxSource, "r3")
    assert srl.loaded == source.loaded and len(srl.get_all("bgp_route")) == 8 and srl.warnings == []


def test_the_same_outputs_give_no_difference() -> None:
    assert not compare(loaded(FrrSource, "r1"), loaded(FrrSource, "r1")).has_differences()


def test_two_vendors_compare_on_the_same_models() -> None:
    output = compare(loaded(FrrSource, "r1"), loaded(SrlinuxSource, "r3"))
    assert output.compared == ["interface", "bgp_peer"] and output.skipped == []


class SlowRunner(SavedRunner):
    def run(self, key: str, command: str) -> InputFile:
        time.sleep(0.2)
        return super().run(key, command)


def test_the_commands_of_one_router_run_at_the_same_time() -> None:
    start = time.monotonic()
    outputs = FrrSource.fetch_outputs(SlowRunner("r1", OUTPUTS / "frr" / "r1"))
    # Two steps of 0.2 s: the two commands, then the routes of the two peers.
    assert time.monotonic() - start < 0.6
    assert len(outputs) == 4


def test_a_missing_output_names_the_failed_command(tmp_path) -> None:
    (tmp_path / "r3").mkdir()
    (tmp_path / "r3" / "bgp.json").write_text("{}")
    with pytest.raises(FetchError) as caught:
        SrlinuxSource.fetch_outputs(SavedRunner("r3", tmp_path / "r3"))
    assert set(caught.value.failures) == {"interfaces", "rib"}


def test_captures_leave_out_the_noise_of_each_vendor() -> None:
    value = {"a": [{"domainname": "x", "statistics": {"in": 1}, "b": 1}]}
    assert without_keys(value, FrrSource.noise) == {"a": [{"statistics": {"in": 1}, "b": 1}]}
    assert without_keys(value, SrlinuxSource.noise) == {"a": [{"domainname": "x", "b": 1}]}
    assert callable(capture)


def test_lab_settings_file() -> None:
    from snapdelta.settings import load_settings

    lab = Path(__file__).resolve().parents[1]
    live = load_settings(lab / "snapdelta.toml", installed={"frr": FrrSource, "srlinux": SrlinuxSource})
    assert live.store.location == str(lab / ".store")
    assert (live.snapshot.jobs, live.source(FrrSource)) == (3, RouterSettings(timeout_s=10))
    assert live.source(SrlinuxSource) == RouterSettings(timeout_s=20)


def test_the_saved_output_lines_of_the_lab_file(tmp_path) -> None:
    from snapdelta.settings import load_settings

    lab = Path(__file__).resolve().parents[1]
    text = (lab / "snapdelta.toml").read_text().replace('location = ".store"\n', "", 1)
    text = text.replace('# location = ".store-saved"', 'location = ".store-saved"')
    text = text.replace('# outputs = "tests/outputs"', 'outputs = "tests/outputs"')
    (tmp_path / "snapdelta.toml").write_text(text)
    saved = load_settings(tmp_path / "snapdelta.toml", installed={"frr": FrrSource, "srlinux": SrlinuxSource})
    assert saved.store.location == str(tmp_path / ".store-saved")
    expected = tmp_path / "tests" / "outputs"
    assert saved.source(FrrSource) == RouterSettings(timeout_s=10, outputs=expected)
    assert saved.source(SrlinuxSource) == RouterSettings(timeout_s=20, outputs=expected)


def test_the_sqlite_line_of_the_lab_file_gives_a_sqlite_store(tmp_path) -> None:
    from snapdelta.settings import load_settings
    from snapdelta.store import SqliteStore, open_store_from

    lab = Path(__file__).resolve().parents[1]
    text = (lab / "snapdelta.toml").read_text()
    text = text.replace('location = ".store"\n', "").replace('# location = "sqlite://', 'location = "sqlite://')
    (tmp_path / "snapdelta.toml").write_text(text)
    settings = load_settings(tmp_path / "snapdelta.toml", installed={"frr": FrrSource, "srlinux": SrlinuxSource})
    assert settings.store.location == f"sqlite://{tmp_path / '.store' / 'snapdelta.db'}"
    assert isinstance(open_store_from(settings), SqliteStore)


def test_the_lab_inventory_names_the_routers_their_groups_and_containers() -> None:
    from snapdelta.inventory import load_nornir

    inventory = load_nornir(Path(__file__).resolve().parents[1] / "inventory")
    assert sorted(inventory.hosts) == ["r1", "r2", "r3"]
    assert inventory.members("trunk") == ["r1", "r2"] and inventory.members("lab") == ["r1", "r2", "r3"]
    r1, r3 = inventory.hosts["r1"], inventory.hosts["r3"]
    assert (r1.platform, r1.hostname) == ("frr", "clab-snapdelta-lab-r1")
    assert (r3.platform, r3.hostname) == ("srlinux", "clab-snapdelta-lab-r3")
    assert r1.data == {"asn": 65001, "loopback": "10.0.0.1"}


def test_the_host_names_the_container() -> None:
    from snapdelta_lab.commands import DockerRunner

    runner = SrlinuxSource.runner("r3", RouterSettings(), "my-router")
    assert isinstance(runner, DockerRunner) and runner.container == "my-router" and runner.shell == ("sr_cli",)
    default = FrrSource.runner("r1", RouterSettings())
    assert isinstance(default, DockerRunner) and default.container == "clab-snapdelta-lab-r1"
