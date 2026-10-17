import pytest
from fastapi.testclient import TestClient

from snapdelta.settings import Secret, Settings, StoreSettings, WebSettings

TOKEN = "t0ken-for-tests"


@pytest.fixture
def api(store, make_app) -> TestClient:
    return TestClient(make_app(store), base_url="http://127.0.0.1")


@pytest.fixture
def guarded(store, make_app) -> TestClient:
    settings = Settings(store=StoreSettings(location=store.location), web=WebSettings(token=Secret(plain=TOKEN)))
    return TestClient(make_app(store, settings), base_url="http://127.0.0.1")


def test_the_plugins_of_every_extension_point(api) -> None:
    found = {(item["group"], item["name"]): item for item in api.get("/api/v1/plugins").json()}
    assert found[("snapdelta.stores", "file")]["package"] == "snapdelta"
    assert found[("snapdelta.sources", "example")] | {"version": None} == {
        "group": "snapdelta.sources",
        "kind": "source",
        "name": "example",
        "package": "snapdelta-example",
        "version": None,
        "severity": "ok",
        "problems": [],
    }


def test_a_pipeline_takes_before_and_after_then_compares(api) -> None:
    assert "snapdelta" in [item["name"] for item in api.get("/api/v1/version").json()["components"]]
    assert {"name": "example-live", "distribution": "snapdelta-example", "fetches": True} in api.get(
        "/api/v1/sources"
    ).json()
    before = api.post("/api/v1/snapshots", json={"source": "example-live", "targets": ["R1", "R9"], "label": "before"})
    assert before.status_code == 200
    result = before.json()
    assert result["ok"] is False and result["saved"]["R1"].endswith("-before") and "R9" in result["failed"]
    api.post("/api/v1/snapshots", json={"source": "example-live", "targets": ["R1"], "label": "after"})
    body = {"left": "R1@before", "right": "R1@after", "title": "Change 42", "description": "Nothing should move"}
    diff = api.post("/api/v1/diffs", json=body).json()
    assert diff["has_differences"] is False and diff["title"] == "Change 42" and diff["url"] == f"/diffs/{diff['name']}"
    assert diff["output"] is None
    markdown = api.get(f"/api/v1/diffs/{diff['name']}", params={"format": "markdown"})
    assert markdown.headers["content-type"].startswith("text/markdown") and markdown.text.startswith("# Change 42")
    assert api.get(f"/api/v1/diffs/{diff['name']}").json()["title"] == "Change 42"
    changed = api.patch(f"/api/v1/diffs/{diff['name']}", json={"title": "Change 43"}).json()
    assert (changed["title"], api.get("/api/v1/diffs").json()[0]["title"]) == ("Change 43", "Change 43")


def test_a_live_diff_without_saving_returns_the_whole_output(api, store) -> None:
    api.post("/api/v1/snapshots", json={"source": "example-live", "targets": ["R2"]})
    outputs, snapshots = len(store.output_entries()), len(store.snapshot_entries("R2"))
    diff = api.post("/api/v1/diffs?include_output=true", json={"left": "R2@latest", "live": True, "save": False})
    assert diff.status_code == 200 and diff.json()["name"] is None and diff.json()["output"]["schema"] == 2
    assert (len(store.output_entries()), len(store.snapshot_entries("R2"))) == (outputs, snapshots)


def test_api_errors_are_json(api, names) -> None:
    missing = api.get("/api/v1/diffs/nope")
    assert missing.status_code == 404 and "error" in missing.json()
    both = api.post("/api/v1/diffs", json={"left": names.before, "right": names.after, "live": True})
    assert both.status_code == 400 and both.json() == {"error": "give right, or live, not both"}
    assert api.post("/api/v1/snapshots", json={"source": "nope", "targets": ["R1"]}).status_code == 400
    assert api.get("/api/v1/openapi.json").json()["info"]["title"] == "snapdelta API"


def test_with_a_token_the_pages_need_a_login(guarded) -> None:
    redirect = guarded.get("/diffs?q=R1", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/login?next=%2Fdiffs%3Fq%3DR1"
    assert guarded.get("/static/app.css").status_code == 200
    wrong = guarded.post("/login", data={"token": "nope", "next": "/diffs"})
    assert wrong.status_code == 401 and "not the token of this server" in wrong.text
    login = guarded.post("/login", data={"token": TOKEN, "next": "//evil.example/x"}, follow_redirects=False)
    assert login.headers["location"] == "/diffs" and "httponly" in login.headers["set-cookie"].lower()
    page = guarded.get("/diffs")
    assert page.status_code == 200 and 'action="/logout"' in page.text
    assert guarded.get("/api/v1/targets").status_code == 200
    guarded.post("/logout")
    assert guarded.get("/diffs", follow_redirects=False).status_code == 303


def test_with_a_token_the_api_needs_the_bearer_token(guarded) -> None:
    refused = guarded.get("/api/v1/targets")
    assert refused.status_code == 401 and refused.headers["www-authenticate"] == "Bearer"
    assert guarded.get("/api/v1/targets", headers={"Authorization": "Bearer wrong"}).status_code == 401
    allowed = guarded.get("/api/v1/targets", headers={"Authorization": f"Bearer {TOKEN}"})
    assert allowed.status_code == 200 and allowed.json() == ["R1"]


def test_with_a_token_any_host_name_works_unless_listed(store, make_app) -> None:
    web = WebSettings(token=Secret(plain=TOKEN))
    open_client = TestClient(
        make_app(store, Settings(store=StoreSettings(location=store.location), web=web)),
        base_url="http://snapdelta.example.net",
    )
    assert open_client.get("/login").status_code == 200
    listed = web.model_copy(update={"allowed_hosts": ["snapdelta.example.net"]})
    strict = TestClient(
        make_app(store, Settings(store=StoreSettings(location=store.location), web=listed)),
        base_url="http://other.example.net",
    )
    assert strict.get("/login").status_code == 400


def test_auth_none_needs_no_login_even_with_a_token(store, make_app) -> None:
    web = WebSettings(token=Secret(env="SNAPDELTA_UNSET_TOKEN"), auth="none", allowed_hosts=["10.0.0.5"])
    client = TestClient(
        make_app(store, Settings(store=StoreSettings(location=store.location), web=web)), base_url="http://10.0.0.5"
    )
    assert client.get("/api/v1/targets").json() == ["R1"]
    page = client.get("/diffs", follow_redirects=False)
    assert page.status_code == 200 and 'action="/logout"' not in page.text
    assert "No login (auth none)" in client.get("/admin/about").text
    other = TestClient(client.app, base_url="http://other.example.net")
    assert other.get("/diffs").status_code == 400


def test_a_pipeline_reads_and_accepts_the_changes(api, names) -> None:
    state = api.get(f"/api/v1/diffs/{names.same}/reviews").json()
    assert state["accepted"] == 0 and state["changes"] == len(state["to_review"]) > 0
    key = state["to_review"][0]
    body = {"key": key, "by": "pipeline", "comment": "expected by CHG-1042"}
    after = api.post(f"/api/v1/diffs/{names.same}/reviews", json=body).json()
    assert after["accepted"] == 1 and key not in after["to_review"] and after["reviews"][key]["by"] == "pipeline"
    listed = {item["name"]: item for item in api.get("/api/v1/diffs").json()}
    assert listed[names.same]["accepted"] == 1
    undone = api.post(f"/api/v1/diffs/{names.same}/reviews", json={"key": key, "accepted": False}).json()
    assert undone["accepted"] == 0
    wrong = api.post(f"/api/v1/diffs/{names.same}/reviews", json={"key": "interface:nope"})
    assert wrong.status_code == 400 and "no change 'interface:nope'" in wrong.json()["error"]
