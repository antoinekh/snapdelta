import pytest
from fastapi.testclient import TestClient

from snapdelta.settings import Secret, Settings, StoreSettings, WebSettings
from snapdelta.store import SnapshotQuery

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
        "used_by": [],
    }


def test_a_pipeline_takes_before_and_after_then_compares(api) -> None:
    assert "snapdelta" in [item["name"] for item in api.get("/api/v1/version").json()["components"]]
    assert {"name": "example-live", "distribution": "snapdelta-example", "fetches": True} in api.get(
        "/api/v1/sources"
    ).json()
    ticket = "CHG-42"
    before = api.post(
        "/api/v1/snapshots",
        json={"source": "example-live", "targets": ["R1", "R9"], "label": "before", "ticket": ticket},
    )
    assert before.status_code == 200
    result = before.json()
    assert result["ok"] is False and result["saved"]["R1"].endswith("-before") and "R9" in result["failed"]
    api.post(
        "/api/v1/snapshots", json={"source": "example-live", "targets": ["R1"], "label": "after", "ticket": ticket}
    )
    body = {"left": "R1@before", "right": "R1@after", "ticket": ticket, "note": "Nothing should move"}
    diff = api.post("/api/v1/diffs", json=body).json()
    assert diff["has_differences"] is False and diff["note"] == "Nothing should move"
    assert diff["url"] == f"/diffs/{diff['name']}" and diff["output"] is None
    markdown = api.get(f"/api/v1/diffs/{diff['name']}", params={"format": "markdown"})
    assert markdown.headers["content-type"].startswith("text/markdown") and markdown.text.startswith("# CHG-42")
    assert api.get(f"/api/v1/diffs/{diff['name']}").json()["note"] == "Nothing should move"
    changed = api.patch(f"/api/v1/diffs/{diff['name']}", json={"note": "Moved nothing"}).json()
    assert (changed["note"], api.get("/api/v1/diffs").json()[0]["note"]) == ("Moved nothing", "Moved nothing")


def test_a_live_diff_without_saving_returns_the_whole_output(api, store) -> None:
    api.post("/api/v1/snapshots", json={"source": "example-live", "targets": ["R2"]})
    outputs, snapshots = len(store.output_entries()), len(store.snapshot_entries(SnapshotQuery(targets=("R2",))))
    diff = api.post("/api/v1/diffs?include_output=true", json={"left": "R2@latest", "live": True, "save": False})
    assert diff.status_code == 200 and diff.json()["name"] is None and diff.json()["output"]["schema"] == 3
    assert (len(store.output_entries()), len(store.snapshot_entries(SnapshotQuery(targets=("R2",))))) == (
        outputs,
        snapshots,
    )


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
    assert wrong.status_code == 401 and "The login was refused" in wrong.text
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
    web = WebSettings(
        token=Secret(backend="env", reference="SNAPDELTA_UNSET_TOKEN"), auth="none", allowed_hosts=["10.0.0.5"]
    )
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


def test_a_pipeline_compares_many_targets_at_once(api, store) -> None:
    body = {"targets": ["R1", "R9"], "left": "before", "right": "after", "note": "VLAN 20"}
    result = api.post("/api/v1/diffs/targets", json=body).json()
    assert result["has_differences"] is True and result["changed"] == ["R1"] and result["unchanged"] == []
    assert result["failed"] == {"R9": f"no snapshot labelled 'before' of R9 in {store.location}"}
    [first, second] = result["diffs"]
    assert first["diff"]["note"] == "VLAN 20" and first["diff"]["output"] is None and second["diff"] is None
    assert first["diff"]["ticket"] is None
    assert api.post("/api/v1/diffs/targets", json={"targets": ["R1"], "left": "before"}).status_code == 400


def test_the_health_check_needs_no_login(guarded) -> None:
    assert guarded.get("/healthz").json() == {"status": "ok"}
    assert guarded.get("/api/v1/targets").status_code == 401


def test_a_pipeline_takes_snapshots_with_a_ticket(api, store) -> None:
    body = {"source": "example-live", "targets": ["R1"], "label": "live", "ticket": "  CHG-1  "}
    assert api.post("/api/v1/snapshots", json=body).status_code == 200
    assert store.load_snapshot(store.resolve_snapshot("R1@live")).ticket == "CHG-1"


def test_a_pipeline_compares_inside_a_ticket(api, ticketed) -> None:
    body = {"left": "R1@before", "right": "R1@after", "ticket": "chg-1", "note": "x"}
    made = api.post("/api/v1/diffs", json=body).json()
    assert (made["ticket"], made["note"]) == ("CHG-1", "x") and made["has_differences"] is True
    assert made["left"]["snapshot"].startswith("R1/20260926T100000Z")
    missing = api.post("/api/v1/diffs", json=body | {"ticket": "CHG-9"})
    assert missing.status_code == 404 and "CHG-9" in missing.json()["error"]
    inherited = api.post("/api/v1/diffs", json={"left": "R1/20260926T100000Z-before", "right": "R1@after"}).json()
    assert inherited["ticket"] == "CHG-1"


def test_a_pipeline_compares_many_targets_inside_a_ticket(api, ticketed) -> None:
    body = {"targets": ["R1"], "left": "before", "right": "after", "ticket": "CHG-1", "note": "many"}
    [item] = api.post("/api/v1/diffs/targets", json=body).json()["diffs"]
    assert (item["diff"]["ticket"], item["diff"]["note"]) == ("CHG-1", "many")
    assert item["diff"]["left"]["snapshot"].startswith("R1/20260926T100000Z")


def test_the_snapshots_of_a_target_filter_by_ticket(api, ticketed) -> None:
    listed = api.get("/api/v1/snapshots", params={"target": "R1", "ticket": "chg-1"}).json()
    assert [item["label"] for item in listed] == ["after", "before"]
    assert [(item["ticket"], item["source"]) for item in listed] == [("CHG-1", "example")] * 2
    assert len(api.get("/api/v1/snapshots", params={"target": "R1"}).json()) == 6
    assert api.get(f"/api/v1/snapshots/{listed[0]['reference']}").json()["ticket"] == "CHG-1"


def test_the_snapshots_are_found_across_targets_by_label_and_ticket(api, ticketed) -> None:
    everything = api.get("/api/v1/snapshots").json()
    assert len(everything) == 6 and everything[0]["ticket"] == "CHG-1" and everything[-1]["ticket"] is None
    stamps = [item["captured_at"] for item in everything]
    assert stamps == sorted(stamps, reverse=True)
    found = api.get("/api/v1/snapshots", params={"label": "before", "ticket": "chg-2"}).json()
    assert [(item["label"], item["ticket"]) for item in found] == [("before", "CHG-2")]
    assert [item["label"] for item in api.get("/api/v1/snapshots", params={"label": "before"}).json()] == ["before"] * 3
    assert api.get("/api/v1/snapshots", params={"limit": 2}).json() == everything[:2]
    assert api.get("/api/v1/snapshots", params={"limit": 0}).status_code == 422
    assert api.get("/api/v1/snapshots", params={"limit": 501}).status_code == 422


def test_the_snapshots_filter_on_several_targets(api, two_targets) -> None:
    both = api.get("/api/v1/snapshots", params={"target": ["R1", "R2"]}).json()
    assert both == api.get("/api/v1/snapshots").json() and {item["target"] for item in both} == {"R1", "R2"}
    only_r2 = api.get("/api/v1/snapshots", params={"target": ["R2", "R9", "R2"]}).json()
    assert [item["target"] for item in only_r2] == ["R2"]
    chg_2 = api.get("/api/v1/snapshots", params={"target": ["R1", "R2"], "ticket": "chg-2"}).json()
    assert [(item["target"], item["ticket"]) for item in chg_2] == [("R1", "CHG-2")]


def test_an_empty_filter_of_the_snapshots_does_not_filter(api, ticketed) -> None:
    listed = api.get("/api/v1/snapshots", params={"target": "", "label": "", "ticket": ""}).json()
    assert listed == api.get("/api/v1/snapshots").json() and len(listed) == 6


def test_a_pipeline_describes_a_diff_and_finds_it_by_ticket(api, ticketed) -> None:
    body = {"left": "R1@before", "right": "R1@after", "ticket": "CHG-1", "note": "r1"}
    made = api.post("/api/v1/diffs", json=body).json()
    kept = api.patch(f"/api/v1/diffs/{made['name']}", json={"note": "r1 uplink"}).json()
    assert (kept["ticket"], kept["note"]) == ("CHG-1", "r1 uplink")
    assert api.patch(f"/api/v1/diffs/{made['name']}", json={"note": ""}).json() | {"note": 0} == kept | {"note": 0}
    exact = api.get("/api/v1/diffs", params={"ticket": "chg-1"}).json()
    assert [item["name"] for item in exact] == [made["name"]] and exact[0]["note"] is None
    assert api.get("/api/v1/diffs", params={"ticket": "chg"}).json() == []
    assert [item["name"] for item in api.get("/api/v1/diffs", params={"q": "chg"}).json()] == [made["name"]]
    removed = api.patch(f"/api/v1/diffs/{made['name']}", json={"ticket": ""}).json()
    assert removed["ticket"] is None and api.get("/api/v1/diffs", params={"ticket": "chg-1"}).json() == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/snapshots", {"targets": ["R1"], "source": "example-live", "ticket": "x" * 81}),
        ("post", "/api/v1/diffs", {"left": "R1@before", "right": "R1@after", "ticket": "x" * 81}),
        ("post", "/api/v1/diffs", {"left": "R1@before", "right": "R1@after", "note": "x" * 2001}),
        ("post", "/api/v1/diffs/targets", {"targets": ["R1"], "left": "before", "live": True, "ticket": "x" * 81}),
        ("post", "/api/v1/diffs/targets", {"targets": ["R1"], "left": "before", "live": True, "note": "x" * 2001}),
        ("patch", "/api/v1/diffs/any", {"ticket": "x" * 81}),
        ("patch", "/api/v1/diffs/any", {"note": "x" * 2001}),
        ("post", "/api/v1/snapshots", {"targets": ["R1"], "source": "example-live", "ticket": f" {'x' * 81} "}),
        ("post", "/api/v1/diffs", {"left": "R1@before", "right": "R1@after", "ticket": f" {'x' * 81} "}),
        ("post", "/api/v1/diffs/targets", {"targets": ["R1"], "left": "before", "live": True, "ticket": 5}),
        ("patch", "/api/v1/diffs/any", {"ticket": f" {'x' * 81} "}),
    ],
)
def test_a_ticket_or_a_note_that_is_too_long_is_refused_before_any_work(api, store, method, path, body) -> None:
    before = (
        len(store.output_entries()),
        [len(store.snapshot_entries(SnapshotQuery(targets=(target,)))) for target in store.targets()],
    )
    response = getattr(api, method)(path, json=body)
    assert response.status_code == 422
    assert before == (
        len(store.output_entries()),
        [len(store.snapshot_entries(SnapshotQuery(targets=(t,)))) for t in store.targets()],
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/snapshots", {"targets": ["R1"], "source": "example-live", "title": "old"}),
        ("post", "/api/v1/diffs", {"left": "R1@before", "right": "R1@after", "title": "old"}),
        ("post", "/api/v1/diffs", {"left": "R1@before", "right": "R1@after", "description": "old"}),
        ("post", "/api/v1/diffs/targets", {"targets": ["R1"], "left": "before", "live": True, "title": "old"}),
        ("patch", "/api/v1/diffs/any", {"title": "old"}),
    ],
)
def test_an_argument_that_is_not_known_is_refused(api, method, path, body) -> None:
    response = getattr(api, method)(path, json=body)
    assert response.status_code == 422
    assert "title" in response.text or "description" in response.text


def test_a_ticket_is_checked_after_its_normalization(api, store) -> None:
    ticket = f"  {'x' * 40}   {'y' * 39}  "
    padded = "x" * 40 + " " + "y" * 39
    for label in ("before", "after"):
        body = {"source": "example-live", "targets": ["R1"], "label": label, "ticket": ticket}
        assert api.post("/api/v1/snapshots", json=body).status_code == 200
    assert store.load_snapshot(store.resolve_snapshot("R1@before", padded)).ticket == padded

    made = api.post("/api/v1/diffs", json={"left": "R1@before", "right": "R1@after", "ticket": ticket})
    assert made.status_code == 200 and made.json()["ticket"] == padded
    many = api.post(
        "/api/v1/diffs/targets", json={"targets": ["R1"], "left": "before", "right": "after", "ticket": ticket}
    )
    assert many.status_code == 200 and many.json()["diffs"][0]["diff"]["ticket"] == padded

    described = api.patch(f"/api/v1/diffs/{made.json()['name']}", json={"ticket": "  CHG-1   x  "})
    assert described.status_code == 200 and described.json()["ticket"] == "CHG-1 x"
    removed = api.patch(f"/api/v1/diffs/{made.json()['name']}", json={"ticket": "  "})
    assert removed.status_code == 200 and removed.json()["ticket"] is None
