import pytest

from snapdelta.errors import PluginError
from snapdelta.inventory import INVENTORIES, NornirInventory
from snapdelta.plugins import discover, extension_points, package_of, plugins, register_plugin, require
from snapdelta.render import RENDERERS, TEXT
from snapdelta.source import SOURCES
from snapdelta.store import STORES, FileStore, SqliteStore


def test_the_built_ins_are_entry_points_of_snapdelta() -> None:
    assert {"file": FileStore, "sqlite": SqliteStore}.items() <= plugins(STORES).items()
    assert plugins(INVENTORIES) == {"nornir": NornirInventory}
    assert set(plugins(RENDERERS)) == {"csv", "json", "markdown", "text"}
    for point, name in ((STORES, "file"), (RENDERERS, "text"), (INVENTORIES, "nornir")):
        plugin = discover(point).plugins[name]
        assert plugin.distribution == "snapdelta" and plugin.version and plugin.target
    assert package_of(SOURCES, "example") == "snapdelta-example"


@pytest.mark.parametrize("point", [STORES, RENDERERS, INVENTORIES], ids=lambda point: point.kind)
def test_a_broken_plugin_of_any_group_is_left_out_with_its_error(fake_entry_points, point) -> None:
    def broken() -> object:
        raise ImportError("no module named vendor_sdk")

    fake_entry_points(point.group, "bad", broken, "bad-plugin")
    fake_entry_points(point.group, "wrong", lambda: 42, "wrong-plugin")
    found = discover(point)
    assert "bad" not in found.plugins and "wrong" not in found.plugins and found.plugins
    assert found.broken["bad"].error == "ImportError: no module named vendor_sdk"
    assert found.broken["bad"].distribution == "bad-plugin"
    assert "42" in found.broken["wrong"].error
    with pytest.raises(PluginError, match=f"the {point.kind} 'bad' cannot be used: ImportError"):
        require(point, "bad")


def test_a_registered_plugin_is_checked_and_wins() -> None:
    with pytest.raises(PluginError, match="output format 'pdf': not a snapdelta.render.Renderer: 42"):
        register_plugin(RENDERERS, "pdf", 42)
    register_plugin(RENDERERS, "json", TEXT)
    assert require(RENDERERS, "json") is TEXT and package_of(RENDERERS, "json") is None


def test_every_extension_point_has_its_own_group_and_table() -> None:
    from snapdelta.settings import FileSettings

    points = extension_points()
    assert len({point.group for point in points}) == len(points)
    tables = [point.table for point in points if point.table is not None]
    assert all(table in FileSettings.model_fields for table in tables)
