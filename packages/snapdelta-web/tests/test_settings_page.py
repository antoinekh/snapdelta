import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from snapdelta.settings import load_settings
from snapdelta_web.app import create_app
from snapdelta_web.state import AppState


@pytest.fixture
def settings_file(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SNAPDELTA_STORE", raising=False)
    path = tmp_path / "snapdelta.toml"
    path.write_text('# kept\n[store]\nlocation = "store"\n[snapshot]\njobs = 10\n')
    path.chmod(0o600)
    return path


@pytest.fixture
def client(settings_file) -> TestClient:
    from snapdelta.store import open_store_from

    settings = load_settings()
    state = AppState(settings, open_store_from(settings))
    return TestClient(create_app(state), base_url="http://127.0.0.1")


def form(**changes: str) -> dict[str, str]:
    values = {
        "store.location": "store",
        "stores.sqlite.busy_timeout_s": "30",
        "snapshot.jobs": "10",
        "web.port": "8765",
        "sources.example-live.data_dir": "",
    }
    return values | changes


def test_settings_page_shows_values_origins_and_file(client, settings_file) -> None:
    page = client.get("/admin/settings")
    assert page.status_code == 200
    assert str(settings_file) in page.text
    assert 'name="snapshot.jobs" value="10"' in page.text and "sources.example-live" in page.text


def test_save_writes_the_change_and_reloads(client, settings_file) -> None:
    response = client.post("/admin/settings", data=form(**{"snapshot.jobs": "25"}), follow_redirects=False)
    assert response.status_code == 303
    text = settings_file.read_text()
    assert "# kept" in text and "jobs = 25" in text
    assert 'value="25"' in client.get("/admin/settings").text


def test_bad_value_comes_back_with_its_message(client, settings_file) -> None:
    before = settings_file.read_text()
    page = client.post("/admin/settings", data=form(**{"web.port": "0"}))
    assert page.status_code == 400 and "greater than or equal to 1" in page.text
    assert settings_file.read_text() == before


def test_empty_number_goes_back_to_default(client, settings_file) -> None:
    client.post("/admin/settings", data=form(**{"snapshot.jobs": ""}), follow_redirects=False)
    assert "jobs" not in settings_file.read_text()


def test_save_to_a_read_only_file_shows_the_error(client, settings_file) -> None:
    settings_file.parent.chmod(0o500)
    try:
        page = client.post("/admin/settings", data=form(**{"snapshot.jobs": "30"}))
    finally:
        settings_file.parent.chmod(0o700)
    assert page.status_code == 400 and "Permission denied" in page.text
    assert "jobs = 10" in settings_file.read_text()


def test_save_without_a_file_creates_the_user_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SNAPDELTA_STORE", raising=False)
    from snapdelta.store import open_store_from

    settings = load_settings()
    state = AppState(settings, open_store_from(settings))
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    location = settings.store.location or ""
    client.post(
        "/admin/settings", data=form(**{"store.location": location, "snapshot.jobs": "2"}), follow_redirects=False
    )
    created = tmp_path / "config" / "snapdelta" / "snapdelta.toml"
    assert "jobs = 2" in created.read_text()
    assert stat.S_IMODE(created.stat().st_mode) == 0o600


def test_a_store_from_the_environment_is_never_written(settings_file, monkeypatch) -> None:
    from snapdelta.store import open_store_from

    monkeypatch.setenv("SNAPDELTA_STORE", "/elsewhere/store")
    settings = load_settings()
    state = AppState(settings, open_store_from(settings))
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    page = client.get("/admin/settings")
    assert 'name="store.location" value="store"' in page.text
    assert "In use: /elsewhere/store, from SNAPDELTA_STORE" in page.text
    client.post("/admin/settings", data=form(**{"snapshot.jobs": "11"}), follow_redirects=False)
    assert "/elsewhere" not in settings_file.read_text()


def test_an_empty_secret_keeps_the_current_one(tmp_path, monkeypatch) -> None:
    from snapdelta.settings import Secret, SourceSettings
    from snapdelta.source import Source
    from snapdelta_web import settings_form

    class TokenSettings(SourceSettings):
        token: Secret | None = None

    class TokenSource(Source):
        source = "token"
        settings_class = TokenSettings

    monkeypatch.chdir(tmp_path)
    (tmp_path / "snapdelta.toml").write_text('[sources.token]\ntoken = { env = "MY_TOKEN" }\n')
    installed = {"token": TokenSource}
    groups = settings_form.form_groups(load_settings(installed=installed), installed)
    assert settings_form.parse_form({}, groups).values["sources.token"]["token"] == Secret(
        backend="env", reference="MY_TOKEN"
    )
    changed = settings_form.parse_form({"sources.token.token.plain": "abc"}, groups)
    assert changed.values["sources.token"]["token"] == Secret(plain="abc")
    removed = settings_form.parse_form({"sources.token.token.clear": "true"}, groups)
    assert removed.values["sources.token"]["token"] is None
    posted = {"sources.token.token.backend": "file", "sources.token.token.reference": "/run/secrets/token"}
    from_file = settings_form.parse_form(posted, groups)
    assert from_file.values["sources.token"]["token"] == Secret(backend="file", reference="/run/secrets/token")


def test_a_store_that_cannot_open_keeps_the_running_state(client, settings_file, tmp_path) -> None:
    before = settings_file.read_text()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        page = client.post("/admin/settings", data=form(**{"store.location": f"sqlite://{locked}/sub/s.db"}))
    finally:
        locked.chmod(0o700)
    assert page.status_code == 400
    assert "the store" in page.text and "cannot be opened" in page.text
    assert settings_file.read_text() == before
    settings_page = client.get("/admin/settings").text
    assert "Settings file in use" in settings_page
    assert client.get("/diffs").status_code == 200


def test_profiles_are_shown_edited_added_and_removed(client, settings_file) -> None:
    settings_file.write_text(
        settings_file.read_text() + '\n# my profiles\n[profiles.quiet]\nignore = ["*.description"]\n'
        "\n[profiles.old]\nskip_only_left = true\n"
    )
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    page = client.get("/admin/profiles").text
    assert "<legend>quiet</legend>" in page and "<legend>old</legend>" in page and "New profile" in page
    assert "Profile" not in client.get("/admin/settings").text.split('action="/admin/settings"')[1]
    assert (
        '<textarea id="f-profiles.quiet.ignore" name="profiles.quiet.ignore"' in page
        and "*.description</textarea>" in page
    )
    edited = form(
        **{
            "profiles.quiet.ignore": "*.description\ninterface.mtu",
            "profiles.quiet.tolerance": "bgp_peer.prefixes_received = 5%\ninterface.speed_mbps = 100",
            "profiles.old.skip_only_left": "true",
            "profiles.old.remove": "true",
            "new_profile": "migration",
            "profiles.+.pairs": "ge-0/0/6 = 1/1/1",
        }
    )
    assert client.post("/admin/profiles", data=edited, follow_redirects=False).status_code == 303
    saved = load_settings(settings_file)
    assert saved.profiles["quiet"].ignore == ["*.description", "interface.mtu"]
    assert saved.profiles["quiet"].tolerance == {"bgp_peer.prefixes_received": "5%", "interface.speed_mbps": 100}
    assert saved.profiles["migration"].pairs == {"ge-0/0/6": "1/1/1"}
    assert "old" not in saved.profiles
    assert "# my profiles" in settings_file.read_text()


def test_a_bad_profile_value_comes_back_on_its_field(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + '\n[profiles.quiet]\nignore = ["*.description"]\n')
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    before = settings_file.read_text()
    page = client.post("/admin/profiles", data=form(**{"profiles.quiet.tolerance": "interface.mtu = 5 percent"}))
    assert page.status_code == 400 and settings_file.read_text() == before
    assert 'class="setting-row has-error"' in page.text and "give a number, or a percent" in page.text
    broken = client.post("/admin/profiles", data=form(**{"profiles.quiet.pairs": "no separator"}))
    assert "write one KEY = VALUE per line" in broken.text
    named = client.post("/admin/profiles", data=form(**{"new_profile": "a.b", "profiles.+.ignore": "x.y"}))
    assert named.status_code == 400 and "letters, digits, _ and - only" in named.text


def test_the_profile_fields_explain_themselves(client) -> None:
    page = client.get("/admin/profiles").text
    assert 'class="hint"' in page and "N% is a percent of the left value" in page
    assert 'placeholder="interface.mtu = 10%' in page


def test_a_save_of_the_settings_tab_keeps_the_profiles(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + '\n[profiles.quiet]\nignore = ["*.description"]\n')
    client.post("/admin/settings", data=form(**{"snapshot.jobs": "30"}), follow_redirects=False)
    saved = load_settings(settings_file)
    assert saved.snapshot.jobs == 30 and saved.profiles["quiet"].ignore == ["*.description"]
    assert 'aria-current="page" href="/admin/profiles"' in client.get("/admin/profiles").text


def test_the_store_location_shows_the_real_default(client, settings_file, monkeypatch) -> None:
    settings_file.write_text("[snapshot]\njobs = 10\n")
    client.post("/admin/settings", data=form(**{"store.location": ""}), follow_redirects=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(Path.home() / "data"))
    page = client.get("/admin/settings").text
    assert 'name="store.location" value="" placeholder="sqlite://~/data/snapdelta/snapdelta.db"' in page


def test_the_save_bar_tracks_the_changes_on_both_tabs(client) -> None:
    for tab in ("settings", "profiles"):
        page = client.get(f"/admin/{tab}").text
        assert f'action="/admin/{tab}" class="settings-form" autocomplete="off" data-track-changes>' in page
        assert '<div class="save-bar">' in page and "Unsaved changes" in page and f"Save {tab}" in page
        assert '<button type="reset" class="button" data-discard disabled>Discard</button>' in page


def test_a_saved_access_setting_waits_for_the_next_start(settings_file) -> None:
    from snapdelta.store import open_store_from

    settings_file.write_text('[store]\nlocation = "store"\n[web]\ntoken = "t0ken-for-tests"\n')
    settings = load_settings()
    state = AppState(settings, open_store_from(settings))
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    client.post("/login", data={"token": "t0ken-for-tests", "next": "/diffs"})
    saved = client.post("/admin/settings", data=form(**{"web.auth": "none"}), follow_redirects=False)
    assert saved.status_code == 303 and "restart=true" in saved.headers["location"]
    assert 'auth = "none"' in settings_file.read_text()
    client.post("/logout")
    assert client.get("/api/v1/targets").status_code == 401


def test_a_field_with_known_values_is_a_menu(client, settings_file) -> None:
    page = client.get("/admin/settings").text
    assert '<a href="#group-snapshot">snapshot</a>' in page and 'id="group-snapshot"' in page
    log_level = '<option value="">info (default)</option><option>debug</option><option>info</option>'
    assert f'<select id="f-snapshot.log_level" name="snapshot.log_level">{log_level}' in page
    assert '<option value="">threads (default)</option><option>processes</option><option>threads</option>' in page
    assert '<option value="">token (default)</option><option>header</option><option>none</option>' in page
    saved = client.post("/admin/settings", data=form(**{"snapshot.log_level": "debug"}), follow_redirects=False)
    assert saved.status_code == 303 and 'log_level = "debug"' in settings_file.read_text()
    assert "<option selected>debug</option>" in client.get("/admin/settings").text
