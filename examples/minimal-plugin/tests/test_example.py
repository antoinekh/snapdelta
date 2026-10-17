from pathlib import Path

import pytest

from snapdelta.errors import LoadError
from snapdelta.output import compare
from snapdelta.testing import SourceContract
from snapdelta_example import ExampleLiveSource, ExampleSource

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
DEVICES = Path(__file__).resolve().parents[1] / "devices"


def loaded(*names: str, **kwargs) -> ExampleSource:
    adapter = ExampleSource(files=[SAMPLES / name for name in names], **kwargs)
    adapter.load()
    return adapter


class TestExampleSource(SourceContract):
    """The tests that every source must pass, from snapdelta: this fixture gives one load of one target.

    They check what a reader of the code could miss: two loads of the same file must compare equal (sort the lists,
    no value that depends on the run), and a saved snapshot must read back with no difference.
    """

    @pytest.fixture
    def load(self):
        return lambda: loaded("r1-before.json")


class TestExampleLiveSource(SourceContract):
    @pytest.fixture
    def load(self):
        from snapdelta_example.live import ExampleLiveSettings

        def fetch() -> ExampleLiveSource:
            adapter = ExampleLiveSource(target="R1", settings=ExampleLiveSettings(data_dir=DEVICES))
            adapter.load()
            return adapter

        return fetch


def test_a_sample_fills_every_model() -> None:
    adapter = loaded("r1-before.json")
    assert adapter.target == "R1"
    assert adapter.loaded == ["interface", "address", "vlan", "interface_state"]
    assert adapter.top_level == ["interface", "vlan", "interface_state"]
    assert adapter.get("interface", "eth0").addresses == ["eth0__10.0.0.1/30"]
    assert adapter.get("interface", "eth2").enabled is False


def test_diff_of_the_two_samples() -> None:
    diff = compare(loaded("r1-before.json"), loaded("r1-after.json")).diff
    interfaces = diff["interface"]
    assert interfaces["eth0"]["-"] == {"description": "uplink", "mtu": 1500}
    assert interfaces["eth0"]["+"] == {"description": "uplink to R2", "mtu": 9000}
    assert interfaces["eth0"]["address"]["eth0__10.0.0.1/30"] == {"-": {"primary": True}, "+": {"primary": False}}
    assert "-" not in interfaces["eth0"]["address"]["eth0__10.0.1.1/30"]
    assert interfaces["eth1"]["-"] == {} and "eth1__192.168.1.1/24" in interfaces["eth1"]["address"]
    assert "+" not in interfaces["eth2"] and "-" not in interfaces["eth3"]
    assert diff["vlan"]["20"] == {"-": {"name": "voice"}, "+": {"name": "voip"}}
    assert "-" not in diff["vlan"]["30"]
    assert diff["interface_state"]["eth1"] == {"-": {"oper_status": "up"}, "+": {"oper_status": "down"}}


def test_a_state_only_file(tmp_path) -> None:
    path = tmp_path / "r2.json"
    path.write_text('{"device": "R2", "state": {"eth0": "up"}}')
    adapter = ExampleSource(files=[path])
    adapter.load()
    assert (adapter.target, adapter.loaded) == ("R2", ["interface_state"])


def test_invalid_json_is_a_load_error(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{")
    with pytest.raises(LoadError, match="bad.json: not a valid JSON file"):
        ExampleSource(files=[path]).load()
    path.write_text("[]")
    with pytest.raises(LoadError, match="a JSON object"):
        ExampleSource(files=[path]).load()


def test_the_entry_point_name_is_the_vendor() -> None:
    from snapdelta.source import source_classes

    assert source_classes()["example"] is ExampleSource
    assert ExampleSource.source == "example"


def test_the_collector_reads_a_simulated_device() -> None:
    from snapdelta_example import ExampleLiveSource

    assert ExampleLiveSource.fetches and not ExampleSource.fetches
    assert ExampleLiveSource.targets() == ["EDGE1", "R1", "R2"]
    adapter = ExampleLiveSource(target="R2", label="live")
    adapter.load()
    assert (adapter.target, adapter.loaded) == ("R2", ["interface", "address", "vlan", "interface_state"])
    assert [ref.name for ref in adapter.inputs] == ["R2.json"]
    with pytest.raises(LoadError, match="R9: not reachable"):
        ExampleLiveSource(target="R9").load()
    with pytest.raises(LoadError, match="give the target name"):
        ExampleLiveSource().load()


def test_the_data_directory_comes_from_the_settings(tmp_path) -> None:
    from snapdelta_example import ExampleLiveSettings, ExampleLiveSource

    (tmp_path / "LAB1.json").write_text('{"device": "LAB1", "state": {"eth0": "up"}}')
    settings = ExampleLiveSettings(data_dir=tmp_path)
    assert ExampleLiveSource.targets(settings) == ["LAB1"]
    source = ExampleLiveSource(target="LAB1", settings=settings)
    source.load()
    assert source.loaded == ["interface_state"]
