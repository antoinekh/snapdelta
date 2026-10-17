import os
import stat
from pathlib import Path

import pytest

from snapdelta.settings import (
    Origin,
    Secret,
    SettingsError,
    SourceSettings,
    find_settings_file,
    load_settings,
    save_settings,
    setting_rows,
    user_settings_path,
)
from snapdelta.source import Source
from snapdelta.store import default_store_path


class DemoSettings(SourceSettings):
    timeout_s: float = 30
    outputs: Path | None = None
    password: Secret | None = None


class DemoSource(Source):
    source = "demo"
    settings_class = DemoSettings


INSTALLED = {"demo": DemoSource}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch) -> None:
    """No settings of the machine leak into a test."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SNAPDELTA_SETTINGS", raising=False)
    monkeypatch.delenv("SNAPDELTA_STORE", raising=False)


def write(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def test_no_file_gives_the_defaults() -> None:
    settings = load_settings(installed=INSTALLED)
    assert settings.file is None
    assert settings.snapshot.jobs == 4
    assert settings.web.port == 8765
    assert settings.store.location == str(default_store_path())
    assert settings.origins["web.port"] == Origin.DEFAULT


def test_lookup_order(tmp_path, monkeypatch) -> None:
    user = write(user_settings_path(), "[snapshot]\njobs = 1\n")
    assert find_settings_file(None) == user
    local = write(tmp_path / "snapdelta.toml", "[snapshot]\njobs = 2\n")
    assert find_settings_file(None) == local
    env = write(tmp_path / "env.toml", "[snapshot]\njobs = 3\n")
    monkeypatch.setenv("SNAPDELTA_SETTINGS", str(env))
    assert find_settings_file(None) == env
    explicit = write(tmp_path / "explicit.toml", "[snapshot]\njobs = 5\n")
    assert find_settings_file(explicit) == explicit
    assert load_settings(explicit, installed=INSTALLED).snapshot.jobs == 5


def test_a_missing_explicit_file_is_an_error(tmp_path) -> None:
    with pytest.raises(SettingsError, match="settings file .*nope.toml does not exist"):
        load_settings(tmp_path / "nope.toml", installed=INSTALLED)


def test_precedence_and_origins(tmp_path, monkeypatch) -> None:
    write(tmp_path / "snapdelta.toml", '[store]\nlocation = "from-file"\n[web]\nport = 9000\n')
    settings = load_settings(installed=INSTALLED)
    assert settings.store.location == str(tmp_path / "from-file")
    assert settings.origins["store.location"] == Origin.FILE
    assert settings.origins["web.port"] == Origin.FILE
    monkeypatch.setenv("SNAPDELTA_STORE", "/env/store")
    settings = load_settings(installed=INSTALLED)
    assert (settings.store.location, settings.origins["store.location"]) == ("/env/store", Origin.ENV)
    assert settings.notes["store.location"] == "SNAPDELTA_STORE"
    settings = load_settings(store="/option/store", installed=INSTALLED)
    assert (settings.store.location, settings.origins["store.location"]) == ("/option/store", Origin.OPTION)
    assert settings.notes["store.location"] == "--store"


def test_relative_paths_are_relative_to_the_file(tmp_path) -> None:
    folder = tmp_path / "project"
    write(folder / "snapdelta.toml", '[store]\nlocation = "sqlite://data/s.db"\n[sources.demo]\noutputs = "out"\n')
    settings = load_settings(folder / "snapdelta.toml", installed=INSTALLED)
    assert settings.store.location == f"sqlite://{folder / 'data' / 's.db'}"
    assert settings.source(DemoSource).outputs == folder / "out"
    write(folder / "home.toml", '[store]\nlocation = "~/store"\n')
    assert load_settings(folder / "home.toml", installed=INSTALLED).store.location == str(Path.home() / "store")


def test_a_tilde_settings_path_is_expanded(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "snapdelta.toml").write_text('[store]\nlocation = "store"\n')
    monkeypatch.setenv("HOME", str(home))
    settings = load_settings(Path("~/snapdelta.toml"), installed={})
    assert settings.file == home / "snapdelta.toml"
    monkeypatch.setenv("SNAPDELTA_SETTINGS", "~/snapdelta.toml")
    assert load_settings(None, installed={}).file == home / "snapdelta.toml"


def test_a_tilde_store_location_of_the_option_or_the_environment_is_expanded(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    assert load_settings(store="~/data", installed={}).store.location == str(home / "data")
    sqlite = load_settings(store="sqlite://~/data/s.db", installed={})
    assert sqlite.store.location == f"sqlite://{home / 'data' / 's.db'}"
    monkeypatch.setenv("SNAPDELTA_STORE", "~/env-data")
    assert load_settings(installed={}).store.location == str(home / "env-data")
    # An option or an environment path stays relative to the current directory, unlike a path in a settings file.
    assert load_settings(store="data", installed={}).store.location == "data"
    # A backend whose address is not a local path keeps its location as written, as one in a settings file does.
    assert load_settings(store="s3://~/bucket", installed={}).store.location == "s3://~/bucket"
    with pytest.raises(SettingsError, match="cannot expand"):
        load_settings(store="~snapdelta-no-such-user/data", installed={})


def test_a_tilde_of_an_unknown_user_in_a_settings_file_says_so(tmp_path) -> None:
    path = tmp_path / "snapdelta.toml"
    path.write_text('[store]\nlocation = "~snapdelta-no-such-user/data"\n')
    with pytest.raises(SettingsError, match="cannot expand"):
        load_settings(path, installed={})


def test_a_plugin_backend_gets_the_path_resolution_of_its_kind(tmp_path) -> None:
    from snapdelta.plugins import register_plugin
    from snapdelta.store import STORES, FileStore

    class LocalStore(FileStore):
        backend = "local"

    class RemoteStore(FileStore):
        backend = "remote"
        local_path = False

    register_plugin(STORES, "local", LocalStore)
    register_plugin(STORES, "s3", RemoteStore)
    write(tmp_path / "local.toml", '[store]\nlocation = "local://data"\n')
    assert load_settings(tmp_path / "local.toml", installed=INSTALLED).store.location == f"local://{tmp_path / 'data'}"
    write(tmp_path / "s3.toml", '[store]\nlocation = "s3://bucket/data"\n')
    assert load_settings(tmp_path / "s3.toml", installed=INSTALLED).store.location == "s3://bucket/data"


def test_unknown_key_and_wrong_type_name_the_file_and_the_key(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", '[web]\nport = "high"\npages = 3\n')
    with pytest.raises(SettingsError) as caught:
        load_settings(installed=INSTALLED)
    assert f"{path}: web.port: Input should be a valid integer" in str(caught.value)
    assert f"{path}: web.pages: Extra inputs are not permitted" in str(caught.value)
    assert set(caught.value.errors) == {"web.port", "web.pages"}


def test_toml_syntax_error_names_the_line(tmp_path) -> None:
    write(tmp_path / "snapdelta.toml", "[web\nport = 1\n")
    with pytest.raises(SettingsError, match="snapdelta.toml: .*line 1"):
        load_settings(installed=INSTALLED)


def test_empty_file_gives_defaults(tmp_path) -> None:
    write(tmp_path / "snapdelta.toml", "# only a comment\n")
    settings = load_settings(installed=INSTALLED)
    assert settings.file == tmp_path / "snapdelta.toml" and settings.web.port == 8765


def test_file_with_bom_and_crlf(tmp_path) -> None:
    (tmp_path / "snapdelta.toml").write_bytes(b"\xef\xbb\xbf[snapshot]\r\njobs = 7\r\n")
    assert load_settings(installed=INSTALLED).snapshot.jobs == 7


def test_source_settings_are_typed(tmp_path) -> None:
    write(tmp_path / "snapdelta.toml", "[sources.demo]\ntimeout_s = 5\n")
    settings = load_settings(installed=INSTALLED)
    assert settings.source(DemoSource).timeout_s == 5
    write(tmp_path / "snapdelta.toml", '[sources.demo]\ntimeout_s = "slow"\n')
    with pytest.raises(SettingsError, match="sources.demo.timeout_s: Input should be a valid number"):
        load_settings(installed=INSTALLED)


def test_unknown_source_table_is_a_warning(tmp_path) -> None:
    write(tmp_path / "snapdelta.toml", "[sources.ffr]\ntimeout_s = 5\n")
    settings = load_settings(installed=INSTALLED)
    assert settings.warnings == [
        f"{tmp_path / 'snapdelta.toml'}: [sources.ffr]: no source 'ffr' is installed (installed: demo)"
    ]


def test_secret_from_env_or_plain(tmp_path, monkeypatch) -> None:
    write(tmp_path / "snapdelta.toml", '[sources.demo]\npassword = { env = "DEMO_PASSWORD" }\n')
    secret = load_settings(installed=INSTALLED).source(DemoSource).password
    assert secret is not None and secret.describe() == "env DEMO_PASSWORD"
    with pytest.raises(SettingsError, match="environment variable DEMO_PASSWORD is not set"):
        secret.value()
    monkeypatch.setenv("DEMO_PASSWORD", "s3cret")
    assert secret.value() == "s3cret"
    plain = Secret.model_validate("hunter2")
    assert (plain.value(), plain.describe(), plain.is_plain) == ("hunter2", "********", True)
    assert "hunter2" not in repr(plain)


def test_plain_secret_in_a_readable_file_is_a_warning(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", '[sources.demo]\npassword = "hunter2"\n', mode=0o644)
    assert load_settings(installed=INSTALLED).warnings == [
        f"{path} holds a plain secret and other users can read it: chmod 600 {path}"
    ]
    os.chmod(path, 0o600)
    assert load_settings(installed=INSTALLED).warnings == []


def test_setting_rows_show_values_origins_and_mask_secrets(tmp_path, monkeypatch) -> None:
    write(tmp_path / "snapdelta.toml", '[web]\nport = 9000\n[sources.demo]\npassword = "hunter2"\n')
    monkeypatch.setenv("SNAPDELTA_STORE", "/env/store")
    rows = {row.key: row for row in setting_rows(load_settings(installed=INSTALLED), INSTALLED)}
    assert (rows["web.port"].value, rows["web.port"].origin) == ("9000", Origin.FILE)
    assert (rows["store.location"].origin, rows["store.location"].note) == (Origin.ENV, "SNAPDELTA_STORE")
    assert rows["sources.demo.password"].value == "********"
    assert (rows["sources.demo.timeout_s"].value, rows["sources.demo.timeout_s"].origin) == ("30", Origin.DEFAULT)
    assert rows["sources.demo.outputs"].value == "not set"


def test_save_keeps_comments_and_writes_only_changes(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", "# my settings\n[web]\n# the port\nport = 9000\n")
    save_settings(path, {"web": {"port": 9000, "host": "localhost"}, "snapshot": {"jobs": 4}}, INSTALLED)
    text = path.read_text()
    assert "# my settings" in text and "# the port" in text and 'host = "localhost"' in text
    assert "[snapshot]" not in text


def test_save_removes_a_key_back_at_its_default(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", "[web]\nport = 9000\n")
    save_settings(path, {"web": {"port": 8765}}, INSTALLED)
    assert "port" not in path.read_text()


def test_save_writes_source_tables_and_secrets(tmp_path) -> None:
    path = tmp_path / "new" / "snapdelta.toml"
    secret = Secret(backend="env", reference="DEMO_PASSWORD")
    save_settings(path, {"sources.demo": {"timeout_s": 5.0, "outputs": None, "password": secret}}, INSTALLED)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    reread = load_settings(path, installed=INSTALLED).source(DemoSource)
    assert (reread.timeout_s, reread.password) == (5.0, secret)


def test_each_store_backend_has_its_stores_table(tmp_path) -> None:
    from snapdelta.store import SqliteSettings, SqliteStore, open_store_from

    path = write(
        tmp_path / "snapdelta.toml", '[store]\nlocation = "sqlite://s.db"\n[stores.sqlite]\nbusy_timeout_s = 5\n'
    )
    settings = load_settings(installed=INSTALLED)
    assert settings.plugin("stores", "sqlite", SqliteSettings).busy_timeout_s == 5
    store = open_store_from(settings)
    assert isinstance(store, SqliteStore) and store.busy_timeout_s == 5
    rows = {row.key: row for row in setting_rows(settings, INSTALLED)}
    assert (rows["stores.sqlite.busy_timeout_s"].value, rows["stores.sqlite.busy_timeout_s"].origin) == (
        "5.0",
        Origin.FILE,
    )
    save_settings(path, {"stores.sqlite": {"busy_timeout_s": 30}}, INSTALLED)
    assert "busy_timeout_s" not in path.read_text()
    write(path, "[stores.s3]\nbucket = 1\n")
    assert load_settings(installed=INSTALLED).warnings == [
        f"{path}: [stores.s3]: no store backend 's3' is installed (installed: file, sqlite)"
    ]


def test_save_refuses_a_bad_value_and_keeps_the_file(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", "[web]\nport = 9000\n")
    with pytest.raises(SettingsError) as caught:
        save_settings(path, {"web": {"port": 0}}, INSTALLED)
    assert "web.port" in caught.value.errors
    assert path.read_text() == "[web]\nport = 9000\n"


def test_save_twice_is_stable(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", "[web]\nport = 9000\n")
    values = {"web": {"port": 9001}}
    save_settings(path, values, INSTALLED)
    first = path.read_bytes()
    save_settings(path, values, INSTALLED)
    assert path.read_bytes() == first


def test_save_keeps_a_table_that_holds_only_comments(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", "[web]\n# port = 8765\n\n[snapshot]\njobs = 3\n")
    save_settings(path, {"web": {"port": None}, "snapshot": {"jobs": 3}}, INSTALLED)
    assert path.read_text() == "[web]\n# port = 8765\n\n[snapshot]\njobs = 3\n"


def test_save_leaves_an_equal_value_as_written(tmp_path) -> None:
    path = write(tmp_path / "snapdelta.toml", '[sources.demo]\ntimeout_s = 10\noutputs = "out"\n')
    save_settings(path, {"sources.demo": {"timeout_s": 10.0, "outputs": Path("out")}}, INSTALLED)
    assert path.read_text() == '[sources.demo]\ntimeout_s = 10\noutputs = "out"\n'


def test_a_plugin_store_location_stays_as_written(tmp_path) -> None:
    from snapdelta.settings import load_settings

    path = tmp_path / "snapdelta.toml"
    path.write_text('[store]\nlocation = "s3://bucket/data"\n')
    assert load_settings(path, installed={}).store.location == "s3://bucket/data"
    path.write_text('[store]\nlocation = "file://data"\n')
    assert load_settings(path, installed={}).store.location == f"file://{tmp_path / 'data'}"


def test_a_relative_settings_path_becomes_absolute(tmp_path, monkeypatch) -> None:
    from snapdelta.settings import load_settings

    (tmp_path / "snapdelta.toml").write_text('[inventory]\nlocation = "inventory"\n')
    monkeypatch.chdir(tmp_path)
    settings = load_settings(Path("snapdelta.toml"), installed={})
    assert settings.file == tmp_path / "snapdelta.toml"
    assert settings.inventory.location == str(tmp_path / "inventory")
