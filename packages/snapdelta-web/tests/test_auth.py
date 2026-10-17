from collections.abc import Mapping

import pytest
from fastapi.testclient import TestClient

from snapdelta.plugins import register_plugin
from snapdelta.settings import Secret, Settings, SettingsError, StoreSettings, WebSettings
from snapdelta_web.auth import (
    AUTH,
    SESSION_COOKIE,
    Access,
    AuthBackend,
    HeaderAuth,
    HeaderSettings,
    LoginField,
    User,
    auth_backend,
    session_user,
    session_value,
)

TOKEN = "t0ken-for-tests"
PROXY = ("10.0.0.5", 40000)


def settings_of(store, auth: str, **tables: dict) -> Settings:
    settings = Settings(store=StoreSettings(location=store.location), web=WebSettings(auth=auth))
    settings.auth = tables
    return settings


def test_a_session_carries_its_user_and_refuses_a_change() -> None:
    value = session_value(b"key", User("alice"))
    assert session_user(b"key", value) == User("alice")
    assert session_user(b"other key", value) is None
    expires, name, signature = value.split(".")
    forged = f"{expires}.{session_value(b'key', User('mallory')).split('.')[1]}.{signature}"
    assert session_user(b"key", forged) is None
    assert session_user(b"key", session_value(b"key", User("alice"), now=0)) is None
    assert session_user(b"key", "garbage") is None


def test_the_header_of_a_trusted_proxy_names_the_user(store, make_app) -> None:
    settings = settings_of(store, "header", header={"header": "X-Remote-User", "trusted_proxies": ["10.0.0.0/24"]})
    proxy = TestClient(make_app(store, settings), base_url="http://127.0.0.1", client=PROXY)
    page = proxy.get("/diffs", headers={"X-Remote-User": "alice"})
    assert page.status_code == 200 and '<span class="muted small">alice</span>' in page.text
    assert 'action="/logout"' not in page.text
    refused = proxy.get("/api/v1/targets")
    assert refused.status_code == 401 and refused.json() == {"error": "the reverse proxy gave no user"}
    assert proxy.get("/diffs", follow_redirects=False).status_code == 401
    elsewhere = TestClient(make_app(store, settings), base_url="http://127.0.0.1", client=("192.0.2.9", 1))
    assert elsewhere.get("/api/v1/targets", headers={"X-Remote-User": "alice"}).status_code == 401
    assert proxy.get("/login", follow_redirects=False).headers["location"] == "/diffs"


def test_the_runs_and_the_reviews_of_a_request_record_its_user(store, make_app) -> None:
    settings = settings_of(store, "header", header={"trusted_proxies": ["10.0.0.5"]})
    proxy = TestClient(make_app(store, settings), base_url="http://127.0.0.1", client=PROXY)
    headers = {"X-Forwarded-User": "alice"}
    taken = proxy.post(
        "/api/v1/snapshots", json={"source": "example-live", "targets": ["R1"], "label": "a"}, headers=headers
    )
    assert taken.status_code == 200
    diff = proxy.post("/api/v1/diffs", json={"left": "R1@before", "right": "R1@after"}, headers=headers).json()
    assert {run.kind: run.user for run in store.runs()} == {"snapshot": "alice", "diff": "alice"}
    key = store.load_output(diff["name"]).change_keys()[0]
    review = {"key": key, "by": "mallory", "comment": "fine"}
    assert proxy.post(f"/api/v1/diffs/{diff['name']}/reviews", json=review, headers=headers).status_code == 200
    assert store.load_output(diff["name"]).reviews[key].by == "alice"


def test_a_token_login_can_give_a_name(store, make_app) -> None:
    settings = Settings(store=StoreSettings(location=store.location), web=WebSettings(token=Secret(plain=TOKEN)))
    client = TestClient(make_app(store, settings), base_url="http://127.0.0.1")
    login = client.post("/login", data={"token": TOKEN, "name": "bob", "next": "/snapshots"}, follow_redirects=False)
    assert login.status_code == 303 and SESSION_COOKIE in login.headers["set-cookie"]
    page = client.get("/snapshots")
    assert '<span class="muted small">bob</span>' in page.text and 'action="/logout"' in page.text
    client.post("/api/v1/diffs", json={"left": "R1@before", "right": "R1@after"})
    assert store.runs()[0].user == "bob"
    form = client.get("/login").text
    assert 'name="token"' in form and 'name="name"' in form


class Directory(AuthBackend):
    """A directory of users, as an LDAP plugin would give."""

    login_fields = (LoginField("username", "User name"), LoginField("password", "Password", secret=True))
    users = {"carol": "s3cret"}

    def login(self, form: Mapping[str, str]) -> User | None:
        name = form.get("username", "")
        return User(name) if self.users.get(name) == form.get("password") else None


def test_a_plugin_backend_gets_the_login_page(store, make_app) -> None:
    register_plugin(AUTH, "directory", Directory)
    client = TestClient(make_app(store, settings_of(store, "directory")), base_url="http://127.0.0.1")
    assert client.get("/diffs", follow_redirects=False).headers["location"] == "/login?next=%2Fdiffs"
    assert 'name="username"' in client.get("/login").text
    refused = client.post("/login", data={"username": "carol", "password": "nope"})
    assert refused.status_code == 401
    client.post("/login", data={"username": "carol", "password": "s3cret"})
    assert '<span class="muted small">carol</span>' in client.get("/diffs").text
    assert client.get("/api/v1/targets").status_code == 200
    assert client.get("/admin/about").text.count("Directory login") == 1


def test_an_unknown_backend_or_a_bad_proxy_stops_the_start(store) -> None:
    with pytest.raises(SettingsError, match=r"\[web\] auth: no auth backend 'ldap' is installed"):
        auth_backend(settings_of(store, "ldap"))
    with pytest.raises(SettingsError, match="trusted_proxies"):
        HeaderAuth(WebSettings(), HeaderSettings(trusted_proxies=["not-an-address"]))


def test_the_access_of_each_built_in_backend(store) -> None:
    assert auth_backend(settings_of(store, "none")).access == Access.OPEN
    assert auth_backend(settings_of(store, "token")).access == Access.LOCAL
    assert auth_backend(settings_of(store, "header")).access == Access.LOGIN
