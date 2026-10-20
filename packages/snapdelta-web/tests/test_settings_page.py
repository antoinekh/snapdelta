import re
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
    assert settings_form.parse_form({}, groups)["sources.token"]["token"] == Secret(backend="env", reference="MY_TOKEN")
    changed = settings_form.parse_form({"sources.token.token.plain": "abc"}, groups)
    assert changed["sources.token"]["token"] == Secret(plain="abc")
    removed = settings_form.parse_form({"sources.token.token.clear": "true"}, groups)
    assert removed["sources.token"]["token"] is None
    posted = {"sources.token.token.backend": "file", "sources.token.token.reference": "/run/secrets/token"}
    from_file = settings_form.parse_form(posted, groups)
    assert from_file["sources.token"]["token"] == Secret(backend="file", reference="/run/secrets/token")


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


def test_profiles_are_shown_and_edited(client, settings_file) -> None:
    settings_file.write_text(
        settings_file.read_text() + '\n# my profiles\n[profiles.quiet]\nignore = ["*.description"]\n'
        "\n[profiles.old]\nskip_only_left = true\n"
    )
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    page = client.get("/admin/profiles").text
    assert "<legend>quiet</legend>" in page and "<legend>old</legend>" in page
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
            "profiles.old.pairs": "ge-0/0/6 = 1/1/1",
        }
    )
    assert client.post("/admin/profiles", data=edited, follow_redirects=False).status_code == 303
    saved = load_settings(settings_file)
    assert saved.profiles["quiet"].ignore == ["*.description", "interface.mtu"]
    assert saved.profiles["quiet"].tolerance == {"bgp_peer.prefixes_received": "5%", "interface.speed_mbps": 100}
    assert saved.profiles["old"].pairs == {"ge-0/0/6": "1/1/1"}
    assert saved.profiles["old"].skip_only_left is True
    assert "# my profiles" in settings_file.read_text()


def test_only_the_settings_tab_shows_the_origin_of_a_value(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + "\n[profiles.quiet]\nskip_only_left = true\n")
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    assert 'class="tag origin-' not in client.get("/admin/profiles").text
    assert 'class="tag origin-file"' in client.get("/admin/settings").text


def test_the_group_titles_start_with_a_capital_letter_in_the_legends_and_the_section_links(client) -> None:
    page = client.get("/admin/settings").text
    for title in ("Store", "Snapshot", "Web", "Inventory", "Store backend sqlite"):
        assert f"<legend>{title}</legend>" in page and f'">{title}</a>' in page
    assert "<legend>store</legend>" not in page and ">snapshot</a>" not in page


def test_a_profile_keeps_its_name_as_the_title_with_its_delete_button_right_after(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + "\n[profiles.quiet]\nskip_only_left = true\n")
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    page = client.get("/admin/profiles").text
    group = page.split('<fieldset class="settings-group" id="profile-quiet">')[1].split("</fieldset>")[0]
    assert re.match(r'\s*<legend>quiet</legend>\s*<button [^>]*form="delete-profile-quiet"', group)
    assert group.count("<legend") == 1 and group.index("</legend>") < group.index("<button")


def test_each_profile_has_a_delete_button_in_its_own_form(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + "\n[profiles.quiet]\nskip_only_left = true\n")
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    page = client.get("/admin/profiles").text
    assert "Remove profile" not in page and ".remove" not in page
    settings_form = page.split('<form method="post" action="/admin/profiles"')[1].split("</form>")[0]
    assert 'form="delete-profile-quiet"' in settings_form
    assert (
        '<form id="delete-profile-quiet" method="post" action="/admin/profiles/quiet/delete"'
        ' data-confirm="Delete the profile quiet?">'
    ) in page
    assert 'class="button danger quiet delete-profile">Delete</button>' in page


def test_a_profile_is_deleted_at_once_and_the_rest_of_the_file_stays(client, settings_file) -> None:
    settings_file.write_text(
        settings_file.read_text() + '\n[profiles.change-window]\nignore = ["*.description"]\n'
        "\n[profiles.quiet]\nskip_only_left = true\n"
    )
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    response = client.post("/admin/profiles/change-window/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/profiles?deleted=change-window"
    text = settings_file.read_text()
    assert "change-window" not in text and "skip_only_left = true" in text
    assert "# kept" in text and "jobs = 10" in text and "[profiles.quiet]" in text
    page = client.get(response.headers["location"]).text
    assert f"Deleted the profile <code>change-window</code> from <code>{settings_file}</code>." in page
    assert "<legend>change-window</legend>" not in page and "<legend>quiet</legend>" in page
    assert load_settings(settings_file).profiles.keys() == {"quiet"}


def test_deleting_an_unknown_profile_is_a_404(client, settings_file) -> None:
    before = settings_file.read_text()
    assert client.post("/admin/profiles/nope/delete").status_code == 404
    assert settings_file.read_text() == before


def test_a_failed_delete_leaves_the_profile_and_says_why(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + "\n[profiles.quiet]\nskip_only_left = true\n")
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    settings_file.parent.chmod(0o500)
    try:
        page = client.post("/admin/profiles/quiet/delete")
    finally:
        settings_file.parent.chmod(0o700)
    assert page.status_code == 400 and "Not saved" in page.text and "Permission denied" in page.text
    assert "[profiles.quiet]" in settings_file.read_text() and "<legend>quiet</legend>" in page.text


def test_a_saved_diff_still_opens_after_its_profile_is_deleted(client, settings_file, store, names) -> None:
    settings_file.write_text(
        f'[store]\nlocation = "{store.location}"\n[profiles.quiet]\nignore = ["interface.description"]\n'
    )
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    data = {"left": names.before, "right": names.after, "profile": "quiet"}
    diff = client.post("/diffs", data=data, follow_redirects=False).headers["location"]
    assert "profile quiet" in client.get(diff).text
    assert client.post("/admin/profiles/quiet/delete", follow_redirects=False).status_code == 303
    page = client.get(diff)
    assert page.status_code == 200 and "profile quiet" in page.text


def test_a_bad_profile_value_comes_back_on_its_field(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + '\n[profiles.quiet]\nignore = ["*.description"]\n')
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    before = settings_file.read_text()
    page = client.post("/admin/profiles", data=form(**{"profiles.quiet.tolerance": "interface.mtu = 5 percent"}))
    assert page.status_code == 400 and settings_file.read_text() == before
    assert 'class="setting-row has-error"' in page.text and "give a number, or a percent" in page.text
    broken = client.post("/admin/profiles", data=form(**{"profiles.quiet.pairs": "no separator"}))
    assert "write one KEY = VALUE per line" in broken.text


def test_a_profile_is_created_from_its_name_and_stays_empty(client, settings_file) -> None:
    response = client.post("/admin/profiles/new", data={"name": "night"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin/profiles#profile-night"
    text = settings_file.read_text()
    assert "[profiles.night]" in text and "# kept" in text and "jobs = 10" in text
    assert load_settings(settings_file).profiles.keys() == {"night"}
    page = client.get("/admin/profiles").text
    assert "<legend>night</legend>" in page and 'id="profile-night"' in page


def test_a_created_profile_survives_a_save_of_the_page(client, settings_file) -> None:
    client.post("/admin/profiles/new", data={"name": "night"})
    client.post("/admin/profiles", data=form(**{"profiles.night.skip_only_left": "true"}))
    assert load_settings(settings_file).profiles["night"].skip_only_left is True
    client.post("/admin/profiles", data=form(**{"profiles.night.skip_only_left": ""}))
    assert "[profiles.night]" in settings_file.read_text()


def test_the_profiles_page_has_a_name_and_a_create_button_and_no_new_profile_group(client) -> None:
    page = client.get("/admin/profiles").text
    assert 'action="/admin/profiles/new"' in page and "Create</button>" in page
    assert "New profile" not in page and "profiles.+" not in page
    new_form = page.split('action="/admin/profiles/new"')[1].split("</form>")[0]
    assert 'name="name"' in new_form and 'action="/admin/profiles"' not in new_form
    assert '<button type="submit" class="button">Create</button>' in new_form
    assert page.count('class="button primary"') == 1


@pytest.mark.parametrize(
    ("name", "message"),
    [("bad name", "letters, digits, _ and - only"), ("a.b", "letters, digits, _ and - only"), ("", "letters, digits")],
)
def test_a_bad_profile_name_comes_back_on_its_field(client, settings_file, name, message) -> None:
    before = settings_file.read_text()
    page = client.post("/admin/profiles/new", data={"name": name})
    assert page.status_code == 400 and settings_file.read_text() == before
    row = page.text.split('class="setting-row has-error"')[1].split("</div>\n")[0]
    assert message in row and f'value="{name}"' in row
    assert load_settings(settings_file).profiles == {}


def test_an_existing_profile_name_comes_back_on_its_field(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + "\n[profiles.quiet]\nskip_only_left = true\n")
    client.post("/admin/profiles", data=form(), follow_redirects=False)
    before = settings_file.read_text()
    page = client.post("/admin/profiles/new", data={"name": " quiet "})
    assert page.status_code == 400 and settings_file.read_text() == before
    assert "a profile &#39;quiet&#39; exists already" in page.text and 'value="quiet"' in page.text


def test_a_failed_create_says_why_and_keeps_the_name(client, settings_file) -> None:
    before = settings_file.read_text()
    settings_file.parent.chmod(0o500)
    try:
        page = client.post("/admin/profiles/new", data={"name": "night"})
    finally:
        settings_file.parent.chmod(0o700)
    assert page.status_code == 400 and "Not saved" in page.text and "Permission denied" in page.text
    assert settings_file.read_text() == before and 'value="night"' in page.text


def test_the_profile_fields_explain_themselves(client, settings_file) -> None:
    settings_file.write_text(settings_file.read_text() + "\n[profiles.quiet]\nskip_only_left = true\n")
    client.post("/admin/profiles", data=form(), follow_redirects=False)
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
        assert "Saves to" not in page.split('<div class="save-bar">')[1].split("</div>")[0]
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
    assert '<a href="#group-snapshot">Snapshot</a>' in page and 'id="group-snapshot"' in page
    log_level = '<option value="">info (default)</option><option>debug</option><option>info</option>'
    assert f'<select id="f-snapshot.log_level" name="snapshot.log_level">{log_level}' in page
    assert '<option value="">threads (default)</option><option>processes</option><option>threads</option>' in page
    assert '<option value="">token (default)</option><option>header</option><option>none</option>' in page
    saved = client.post("/admin/settings", data=form(**{"snapshot.log_level": "debug"}), follow_redirects=False)
    assert saved.status_code == 303 and 'log_level = "debug"' in settings_file.read_text()
    assert "<option selected>debug</option>" in client.get("/admin/settings").text
