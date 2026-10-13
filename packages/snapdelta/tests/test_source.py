import pytest

from snapdelta.errors import LoadError
from snapdelta.settings import SourceSettings
from snapdelta.source import Source, register_source, source_classes
from snapdelta_example import ExampleSource
from snapdelta_example.models import Address, Interface


def test_loaded_models_limit_top_level(make_adapter) -> None:
    adapter = make_adapter()
    assert adapter.loaded == ["interface", "address", "vlan"]
    assert adapter.top_level == ["interface", "vlan"]
    assert type(adapter).top_level == ["interface", "vlan", "interface_state"]


def test_the_base_declares_no_model() -> None:
    assert Source.top_level == []
    assert Source.model_order() == []


def test_model_order_puts_children_after_their_parent() -> None:
    assert ExampleSource.model_order() == ["interface", "address", "vlan", "interface_state"]


def test_config_and_state(make_adapter) -> None:
    adapter = make_adapter(with_state=True)
    assert adapter.top_level == ["interface", "vlan", "interface_state"]
    assert adapter.get("interface", "eth0").addresses == ["eth0__10.0.0.1/30"]
    assert adapter.get("interface_state", "eth0").oper_status == "up"


def test_duplicate_object_gives_a_warning_and_keeps_the_first() -> None:
    adapter = ExampleSource(target="R1")
    adapter.add_or_warn(Interface(name="eth0", description="first"))
    adapter.add_or_warn(Interface(name="eth0", description="second"))
    for _ in range(2):
        adapter.add_child_or_warn("interface", "eth0", Address(interface="eth0", prefix="10.0.0.1/30"))
    assert adapter.get("interface", "eth0").description == "first"
    assert adapter.get("interface", "eth0").addresses == ["eth0__10.0.0.1/30"]
    assert adapter.warnings == [
        "duplicate interface eth0: the first one is kept",
        "duplicate address eth0__10.0.0.1/30: the first one is kept",
    ]


def test_child_without_parent_gives_a_warning_and_is_skipped() -> None:
    adapter = ExampleSource(target="R1")
    assert not adapter.add_child_or_warn("interface", "eth9", Address(interface="eth9", prefix="10.9.9.9/32"))
    assert adapter.get_or_none("address", "eth9__10.9.9.9/32") is None
    assert adapter.warnings == ["address eth9__10.9.9.9/32: no interface eth9, skipped"]


def test_device_name_rules() -> None:
    given = Source(target="R1")
    given.set_target("R1-CONFIG")
    assert given.target == "R1"
    assert given.warnings == ["target name 'R1' given, the config says 'R1-CONFIG': 'R1' is kept"]
    from_config = Source()
    from_config.set_target("R2")
    assert (from_config.target, from_config.name) == ("R2", "R2")
    with pytest.raises(LoadError, match="no target name"):
        Source().set_target(None)


def test_finish_load_sets_the_capture_time() -> None:
    adapter = ExampleSource(target="R1")
    adapter.finish_load(None, ["vlan"])
    assert adapter.captured_at is not None
    assert adapter.loaded == ["vlan"]


def test_read_inputs_needs_a_file() -> None:
    with pytest.raises(LoadError, match="no input file"):
        Source().read_inputs()


def test_require_loaded() -> None:
    with pytest.raises(ValueError, match="not loaded"):
        Source().require_loaded()


def test_versions(make_adapter) -> None:
    assert set(make_adapter().versions()) == {"diffsync", "snapdelta", "snapdelta-example"}


def test_registered_adapters_join_the_entry_points() -> None:
    class Extra(ExampleSource):
        source = "extra"

    register_source("extra", Extra)
    classes = source_classes()
    assert classes["extra"] is Extra
    assert classes["example"] is ExampleSource
    assert all(issubclass(cls, Source) for cls in classes.values())


class Timeout(SourceSettings):
    timeout_s: float = 30


class WithSettings(ExampleSource):
    source = "with-settings"
    settings_class = Timeout


def test_a_source_gets_its_settings_or_the_defaults() -> None:
    assert WithSettings().settings == Timeout()
    assert WithSettings(settings=Timeout(timeout_s=2)).settings.timeout_s == 2


def test_a_broken_plugin_does_not_stop_the_other_sources(monkeypatch) -> None:
    from types import SimpleNamespace

    import pytest

    from snapdelta import source as source_module
    from snapdelta.errors import LoadError, SnapdeltaError
    from snapdelta.source import broken_sources, require_source
    from snapdelta_example import ExampleSource

    def broken() -> type:
        raise ImportError("no module named vendor_sdk")

    entries = [SimpleNamespace(name="good", load=lambda: ExampleSource), SimpleNamespace(name="bad", load=broken)]
    monkeypatch.setattr(source_module, "entry_points", lambda group: entries)
    assert "good" in source_classes() and "bad" not in source_classes()
    assert broken_sources() == {"bad": "ImportError: no module named vendor_sdk"}
    with pytest.raises(LoadError, match=r"bad: the plugin of this source cannot load \(ImportError"):
        require_source("bad")
    with pytest.raises(SnapdeltaError, match="no source 'nope' is installed"):
        require_source("nope")


def test_a_missing_distribution_has_an_unknown_version(make_adapter) -> None:
    adapter = make_adapter()
    adapter.distribution = "no-such-distribution"
    assert adapter.versions()["no-such-distribution"] == "unknown"


def test_a_file_given_twice_is_read_once(tmp_path) -> None:
    import json

    from snapdelta_example import ExampleSource

    path = tmp_path / "r1.json"
    path.write_text(json.dumps({"device": "R1", "interfaces": [{"name": "eth0"}]}))
    adapter = ExampleSource(files=[path, tmp_path / "." / "r1.json", path])
    adapter.load()
    assert adapter.files == [path] and adapter.warnings == [] and len(adapter.inputs) == 1
